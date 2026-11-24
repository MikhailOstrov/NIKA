"""Expose the subset of the Wit message API consumed by NIKA, backed by Jev."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from catalog import (
    CANONICAL_NAMES,
    ENTITIES,
    INTENTS,
    TOPICS,
    TOPIC_CANONICAL_NAMES,
)

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = os.getenv("JEV_MODEL", "typesafe/jev-1.14")
TIMEOUT_SECONDS = float(os.getenv("JEV_TIMEOUT_SECONDS", "20"))
MAX_MESSAGE_LENGTH = 2000
_lock = threading.Lock()
_stats = {"calls_ok": 0, "calls_failed": 0}
ANIMAL_WORDS = {
    "elk": r"лось|лося|лосю|лосем|лосе",
    "wild_boar": r"кабан|кабана|кабану|кабаном|кабане",
    "roe_deer": r"косуля|косули|косуле|косулю|косулей",
    "red_deer": r"олень|оленя|оленю|оленем|олене",
    "fox": r"лиса|лисы|лисе|лису|лисой|лисица|лисицы|лисицу",
    "hare": r"заяц|зайца|зайцу|зайцем|зайце",
    "duck": r"утка|утки|утке|утку|уткой",
    "goose": r"гусь|гуся|гусю|гусем|гусе",
    "mallard": r"кряква|кряквы|крякве|крякву|кряквой",
    "capercaillie": r"глухарь|глухаря|глухарю|глухарем|глухаре",
    "black_grouse": r"тетерев|тетерева|тетереву|тетеревом|тетереве",
    "hazel_grouse": r"рябчик|рябчика|рябчику|рябчиком|рябчике",
    "woodcock": r"вальдшнеп|вальдшнепа|вальдшнепу|вальдшнепом|вальдшнепе",
}
ANIMAL_PATTERNS = {
    name: re.compile(r"(?<!\w)(?:" + forms + r")(?!\w)", re.IGNORECASE)
    for name, forms in ANIMAL_WORDS.items()
}
GROUP_ENTITY_IDS = {
    "group_ungulates",
    "group_forest_birds",
    "group_waterfowl",
    "group_birds",
    "group_mammals",
    "family_cervidae",
}
GRAPH_INFERENCE_COMMAND = re.compile(
    r"\b(?:выведи|выведите|рассчитай)\b"
    r"|\bчто\s+(?:следует|можно\s+вывести)\b.{0,80}\b(?:граф\w*|связ\w*)\b"
    r"|\b(?:из\s+графа|по\s+графу)\b",
    re.IGNORECASE,
)
SAVED_NOTE_COMMAND = re.compile(
    r"\b(?:покажи|открой|найди|выведи)\b.{0,80}"
    r"\b(?:заметк\w*|сохран[её]нн\w*|внешн\w*)\b"
    r"|\bчто\s+(?:ты\s+)?(?:добавил[аи]?|сохранил[аи]?)\b",
    re.IGNORECASE,
)
EXTERNAL_RESEARCH_COMMAND = re.compile(
    r"\b(?:найди|поищи|отыщи)\b",
    re.IGNORECASE,
)
SHOW_CURRENT_COMMAND = re.compile(
    r"^\s*(?:покажи|покажите|что\s+такое)\b", re.IGNORECASE
)
FAMILY_MEMBERS_QUERY = re.compile(
    r"\b(?:вид\w*|представител\w*|состав|кто\s+входит)\b.{0,80}"
    r"\b(?:семейств\w*|групп\w*|боров\w*|копытн\w*)\b"
    r"|\b(?:семейств\w*|групп\w*)\b.{0,80}"
    r"\b(?:вид\w*|представител\w*|состав)\b",
    re.IGNORECASE,
)
GROUP_HABITAT_QUERY = re.compile(
    r"\b(?:общ\w*\s+сред\w*|сред\w*\s+обитан\w*|мест\w*\s+обитан\w*)\b",
    re.IGNORECASE,
)


def has_multiple_named_animals(message: str) -> bool:
    """Conservative guard for known forms; this is not full multi-entity NLU."""
    return sum(bool(pattern.search(message)) for pattern in ANIMAL_PATTERNS.values()) >= 2


class ClassificationError(Exception):
    """A safe error message without request headers or credentials."""


def classify_with_jev(message: str, token: str) -> dict:
    if not token:
        raise ClassificationError("OpenRouter API key is not configured")
    request_body = {
        "model": MODEL,
        "state": {"message": message},
        "questions": {
            "intent": {
                "type": "choice",
                "instructions": "Определи тему сообщения пользователя в поле `message`. Выбери unknown, если ни один класс не подходит.",
                "criteria": INTENTS,
            },
            "entity": {
                "type": "choice",
                "instructions": "Какой конкретный объект явно упомянут в `message`? Если вопрос одновременно спрашивает об отдельных свойствах нескольких разных животных, выбери multiple_animals. Не выбирай объект только потому, что он подходит по теме. Выбери unknown, если объекта нет в списке, он не упомянут или не определён.",
                "criteria": ENTITIES,
            },
            "topic": {
                "type": "choice",
                "instructions": (
                    "Выбери основной аспект, если пользователь просит поискать внешние сведения "
                    "или вывести знание из графа. Для обычного вопроса по уже имеющейся базе и "
                    "неопределённого аспекта выбери unknown. Не добавляй аспект от себя."
                ),
                "criteria": TOPICS,
            },
        },
    }
    request = Request(
        ENDPOINT,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise ClassificationError(f"OpenRouter HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError):
        raise ClassificationError("OpenRouter connection failed or timed out") from None
    except (ValueError, UnicodeDecodeError):
        raise ClassificationError("OpenRouter returned invalid JSON") from None

    try:
        answers = payload["answers"]
        intent_answer = answers["intent"]
        entity_answer = answers["entity"]
        topic_answer = answers.get("topic", {"type": "choice", "choice": "unknown"})
        intent = intent_answer["choice"]
        entity = entity_answer["choice"]
        topic = topic_answer["choice"]
        if (
            intent_answer["type"] != "choice"
            or entity_answer["type"] != "choice"
            or topic_answer["type"] != "choice"
        ):
            raise ValueError
        if intent not in INTENTS or entity not in ENTITIES or topic not in TOPICS:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ClassificationError("OpenRouter returned unexpected decisions") from None

    # Resolve explicit operation verbs locally so Jev's intent choice cannot
    # silently turn a read, graph inference, or web search into another action.
    if SAVED_NOTE_COMMAND.search(message):
        intent = "about_hunting_research"
    elif SHOW_CURRENT_COMMAND.search(message):
        if entity in ANIMAL_PATTERNS:
            if re.search(r"\b(?:г[оа]н\w*|размнож\w*|токован\w*)\b", message, re.I):
                intent = "about_hunting_breeding"
            elif re.search(r"\b(?:сред\w*|мест\w*\s+обитан\w*|где\s+обита\w*)\b", message, re.I):
                intent = "about_hunting_habitat"
            elif re.search(r"\b(?:особенност\w*|характеристик\w*)\b", message, re.I):
                intent = "about_hunting_features"
            else:
                intent = "about_hunting_animal"
        elif entity in GROUP_ENTITY_IDS:
            if GROUP_HABITAT_QUERY.search(message):
                intent = "about_hunting_habitat"
            elif FAMILY_MEMBERS_QUERY.search(message):
                intent = "about_hunting_group_members"
            else:
                intent = "about_hunting_group_definition"
        elif intent in {"about_hunting_research", "about_hunting_inference"}:
            intent = "unknown"
    elif GRAPH_INFERENCE_COMMAND.search(message):
        intent = "about_hunting_inference"
    elif EXTERNAL_RESEARCH_COMMAND.search(message):
        intent = "about_hunting_research"
    if intent == "about_hunting_inference":
        if FAMILY_MEMBERS_QUERY.search(message):
            topic = "taxonomy"
        elif GROUP_HABITAT_QUERY.search(message):
            topic = "habitat"
        elif re.search(r"\b(?:снаряж\w*|оборудован\w*|бинокл\w*)\b", message, re.I):
            topic = "equipment"
        elif re.search(r"\b(?:метод\w*|способ\w*)\s+охот\w*\b", message, re.I):
            topic = "hunting_method"
    elif intent == "about_hunting_research" and topic == "unknown":
        topic = "general"

    wit_payload = {"text": message, "intents": [], "entities": {}, "traits": {}}
    if intent != "unknown":
        item = {"name": intent}
        confidence = intent_answer.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= confidence <= 1:
            item["confidence"] = confidence
        wit_payload["intents"].append(item)
    is_named_animal = entity in ANIMAL_PATTERNS
    should_emit_entity = entity not in {"unknown", "multiple_animals"}
    if is_named_animal and has_multiple_named_animals(message):
        should_emit_entity = False
    if should_emit_entity:
        # NIKA's C++ classifier uses the canonical `value` and role from the
        # entity key. Jev returns a candidate, not a reliable text span, so do
        # not invent `body`, `start`, or `end` offsets.
        wit_payload["entities"]["rrel_entity:rrel_entity"] = [{
            "name": "rrel_entity", "role": "rrel_entity",
            "value": CANONICAL_NAMES[entity], "type": "value",
        }]
    if intent in {"about_hunting_research", "about_hunting_inference"} and topic != "unknown":
        wit_payload["entities"]["rrel_topic:rrel_topic"] = [{
            "name": "rrel_topic",
            "role": "rrel_topic",
            "value": TOPIC_CANONICAL_NAMES[topic],
            "type": "value",
        }]
    return wit_payload


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            with _lock:
                stats = dict(_stats)
            self.send_json(200, {"backend": "jev", "model": MODEL, **stats})
            return
        if parsed.path != "/message":
            self.send_json(404, {"error": "not found"})
            return
        message = parse_qs(parsed.query).get("q", [""])[0].strip()
        if not message or len(message) > MAX_MESSAGE_LENGTH:
            self.send_json(400, {"error": "invalid message length"})
            return
        started = time.monotonic()
        try:
            result = classify_with_jev(message, os.getenv("OPENROUTER_API_KEY", ""))
        except ClassificationError as exc:
            with _lock:
                _stats["calls_failed"] += 1
            print(f"Jev classification failed: {exc}", flush=True)
            # NIKA's classifier can parse this as empty, rather than mistaking
            # a fabricated classification for a successful Jev response.
            self.send_json(502, {"text": message, "intents": [], "entities": {}, "traits": {}, "error": "Jev unavailable"})
            return
        with _lock:
            _stats["calls_ok"] += 1
        print(f"Jev classification completed in {time.monotonic()-started:.2f}s", flush=True)
        self.send_json(200, result)

    def log_message(self, format: str, *args) -> None:
        # The query string contains the user's text; keep it out of access logs.
        return


if __name__ == "__main__":
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is not configured")
    print(f"Starting Wit-compatible Jev adapter, model={MODEL}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
