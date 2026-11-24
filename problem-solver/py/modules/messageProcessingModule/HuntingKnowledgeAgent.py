"""Research and graph-inference actions for NIKA's hunting domain."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from sc_client.client import (
    erase_elements,
    generate_elements_by_scs,
    search_by_template,
    set_link_contents,
)
from sc_client.constants import sc_type
from sc_client.models import ScAddr, ScLinkContent, ScLinkContentType, ScTemplate
from sc_kpm import ScAgentClassic, ScKeynodes, ScResult
from sc_kpm.utils import (
    check_connector,
    generate_connector,
    generate_link,
    generate_node,
    get_element_system_identifier,
    get_link_content_data,
    search_connector,
)
from sc_kpm.utils.action_utils import (
    finish_action_with_status,
    generate_action_result,
    get_action_arguments,
)


LOGGER = logging.getLogger(__name__)
WIKIPEDIA_API = "https://ru.wikipedia.org/w/api.php"
MAX_EXCERPT_CHARS = 1000
MAX_SOURCE_CHARS = 12000
GROUP_NODE_IDS = {
    "concept_hunting_ungulate",
    "concept_forest_game_bird",
    "concept_waterfowl",
    "concept_bird",
    "concept_mammal",
    "family_cervidae",
    "семейство оленьих",
    "семейство оленевых",
    "оленьи",
    "cervidae",
}
CERVIDAE_GROUP_IDS = {
    "family_cervidae",
    "семейство оленьих",
    "семейство оленевых",
    "оленьи",
    "cervidae",
}
TAXONOMY_TOPIC_IDS = {
    "hunting_research_topic_taxonomy",
    "систематическое положение",
    "таксономия",
    "классификация",
}
HABITAT_TOPIC_IDS = {
    "hunting_research_topic_habitat",
    "место обитания",
    "среда обитания",
}
GENERAL_TOPIC_IDS = {
    "hunting_research_topic_general",
    "общая информация",
}
CERVIDAE_LABEL_MARKERS = ("семейств", "олен", "cervid")
TAXONOMY_LABEL_MARKERS = ("таксоном", "систематическ", "классификац")
HABITAT_LABEL_MARKERS = ("место обитания", "среда обитания", "habitat")
GENERAL_LABEL_MARKERS = ("общая информация", "общие сведения")
HUNTING_METHOD_LABEL_MARKERS = ("способ охоты", "метод охоты", "охота")
EQUIPMENT_LABEL_MARKERS = ("снаряж", "оруж", "оборудован")
TOPIC_LABELS = {
    "hunting_research_topic_habitat": "место обитания",
    "hunting_research_topic_distribution": "ареал распространения",
    "hunting_research_topic_biology": "биологические особенности",
    "hunting_research_topic_breeding": "размножение и гон",
    "hunting_research_topic_behavior": "поведение",
    "hunting_research_topic_diet": "питание",
    "hunting_research_topic_taxonomy": "систематическое положение",
    "hunting_research_topic_population": "численность и охрана",
    "hunting_research_topic_hunting_method": "способы охоты",
    "hunting_research_topic_equipment": "охотничье снаряжение",
    "hunting_research_topic_general": "общая информация",
}
SUBJECT_TITLE_TERMS = {
    "elk": ("лось", "лоси"),
    "wild_boar": ("кабан", "кабаны"),
    "roe_deer": ("косул",),
    "red_deer": ("олень", "олен"),
    "hare": ("заяц", "зайц"),
    "fox": ("лиса", "лисиц"),
    "duck": ("утка", "утк"),
    "goose": ("гусь", "гус"),
    "mallard": ("кряква",),
    "capercaillie": ("глухарь", "глухар"),
    "black_grouse": ("тетерев",),
    "hazel_grouse": ("рябчик",),
    "woodcock": ("вальдшнеп",),
    "family_cervidae": ("оленев", "cervidae"),
    "group_ungulates": ("копыт", "ungulate"),
    "group_forest_birds": ("боров", "глухарь", "тетерев"),
    "group_waterfowl": ("водоплава", "утка", "гусь"),
    "group_birds": ("птиц", "bird"),
    "group_mammals": ("млекопита", "mammal"),
    "binoculars": ("бинокл",),
    "camouflage_clothing": ("маскиров",),
}
TOPIC_CONTENT_PATTERNS = {
    "hunting_research_topic_habitat": re.compile(
        r"обита|местообитан|среда обитания|биотоп|населя|ареал|распростран", re.I
    ),
    "hunting_research_topic_distribution": re.compile(
        r"ареал|распростран|населя|обита|территор|регион|евраз|сибир|америк", re.I
    ),
    "hunting_research_topic_biology": re.compile(
        r"внешн|длин|масса|высот|окрас|шерст|рог|размер", re.I
    ),
    "hunting_research_topic_breeding": re.compile(
        r"размнож|гон|беремен|брач|течк|дет[её]ныш|теленок|теленок", re.I
    ),
    "hunting_research_topic_behavior": re.compile(
        r"поведен|образ жизни|актив|стад|мигр|кочев", re.I
    ),
    "hunting_research_topic_diet": re.compile(
        r"питан|корм|растен|трав|листь|пищ", re.I
    ),
    "hunting_research_topic_taxonomy": re.compile(
        r"семейств|род\b|вид\b|классификац|подвид|таксон", re.I
    ),
    "hunting_research_topic_population": re.compile(
        r"числен|охрана|статус|исчез|популяц|МСОП", re.I
    ),
    "hunting_research_topic_hunting_method": re.compile(
        r"охот|способ добыч|охота", re.I
    ),
    "hunting_research_topic_equipment": re.compile(
        r"снаряж|бинокл|руж|оруж|маскиров", re.I
    ),
}
SAVED_NOTE_REQUEST = re.compile(
    r"\b(?:покажи|открой|найди|выведи)\b.{0,80}"
    r"\b(?:заметк\w*|сохран[её]нн\w*|внешн\w*)\b"
    r"|\bчто\s+(?:ты\s+)?(?:добавил[аи]?|сохранил[аи]?)\b",
    re.I,
)


def _normalize_sc_identifier(value: str) -> str:
    return value.strip().casefold().replace("ё", "е")


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    normalized = _normalize_sc_identifier(value)
    return any(_normalize_sc_identifier(marker) in normalized for marker in markers)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class HuntingKnowledgeStore:
    """SQLite source of truth plus an append-only, recoverable JSONL journal."""

    def __init__(self, data_dir: str | None = None):
        self.data_dir = Path(data_dir or os.getenv("HUNTING_DATA_DIR", "/data/hunting"))
        self.db_path = self.data_dir / "hunting_knowledge.sqlite3"
        self.log_path = self.data_dir / "knowledge_events.jsonl"
        self._lock = threading.Lock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS knowledge_notes "
                "(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
        self._recover_from_journal()
        self._sync_journal_from_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=20)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _valid_payload(payload: Any) -> bool:
        return (
            isinstance(payload, dict)
            and isinstance(payload.get("id"), str)
            and payload.get("kind") in {"external", "derived"}
            and isinstance(payload.get("subject_system_id"), str)
            and isinstance(payload.get("content"), str)
        )

    def _recover_from_journal(self) -> None:
        if not self.log_path.exists():
            return
        recovered = 0
        with self._connection() as connection, self.log_path.open(encoding="utf-8") as journal:
            for line_number, line in enumerate(journal, start=1):
                try:
                    payload = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    LOGGER.warning("Ignoring invalid JSONL record at line %s", line_number)
                    continue
                if not self._valid_payload(payload):
                    continue
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO knowledge_notes(id, payload) VALUES (?, ?)",
                    (payload["id"], json.dumps(payload, ensure_ascii=False, sort_keys=True)),
                )
                recovered += cursor.rowcount
        if recovered:
            LOGGER.info("Recovered %d notes from the JSONL journal", recovered)

    def _all_payloads(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM knowledge_notes ORDER BY rowid"
            ).fetchall()
        payloads = []
        for (raw_payload,) in rows:
            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                LOGGER.exception("Ignoring invalid record in the SQLite store")
                continue
            if self._valid_payload(payload):
                payloads.append(payload)
        return payloads

    def _sync_journal_from_database(self) -> None:
        known_ids: set[str] = set()
        if self.log_path.exists():
            with self.log_path.open(encoding="utf-8") as journal:
                for line in journal:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if self._valid_payload(payload):
                        known_ids.add(payload["id"])
        missing = [item for item in self._all_payloads() if item["id"] not in known_ids]
        if missing:
            with self._lock, self.log_path.open("a", encoding="utf-8", newline="\n") as journal:
                for payload in missing:
                    journal.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                journal.flush()
                os.fsync(journal.fileno())

    @staticmethod
    def _make_id(payload: dict[str, Any]) -> str:
        stable_fields = {
            key: payload.get(key)
            for key in (
                "kind", "subject_system_id", "topic_system_id", "source_url",
                "content", "derivation_basis",
            )
        }
        encoded = json.dumps(stable_fields, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def save(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        payload = dict(payload)
        payload["id"] = self._make_id(payload)
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO knowledge_notes(id, payload) VALUES (?, ?)",
                (payload["id"], serialized),
            )
            created = cursor.rowcount == 1
            if not created:
                row = connection.execute(
                    "SELECT payload FROM knowledge_notes WHERE id = ?", (payload["id"],)
                ).fetchone()
                if row:
                    payload = json.loads(row[0])
        if created:
            with self._lock, self.log_path.open("a", encoding="utf-8", newline="\n") as journal:
                journal.write(serialized + "\n")
                journal.flush()
                os.fsync(journal.fileno())
        return payload, created

    def load_all(self) -> list[dict[str, Any]]:
        return self._all_payloads()


class HuntingKnowledgeAgent(ScAgentClassic):
    """Handles source-backed research and deterministic SC-graph inferences."""

    def __init__(self):
        super().__init__("action_hunting_knowledge")
        self.store = HuntingKnowledgeStore()

    def on_event(self, event_element: ScAddr, event_edge: ScAddr, action_element: ScAddr) -> ScResult:
        try:
            result = self.run(action_element)
        except Exception as exc:
            self.logger.info(
                f"HuntingKnowledgeAgent failed: {type(exc).__name__}: {exc}"
            )
            result = ScResult.ERROR
        finish_action_with_status(action_element, result == ScResult.OK)
        self.logger.info(
            "HuntingKnowledgeAgent finished %s",
            "successfully" if result == ScResult.OK else "unsuccessfully",
        )
        return result

    def run(self, action_node: ScAddr) -> ScResult:
        args = get_action_arguments(action_node, 1)
        if not args:
            return ScResult.ERROR
        message_addr = args[0]
        research_class = ScKeynodes.resolve(
            "concept_message_about_hunting_research", sc_type.CONST_NODE_CLASS
        )
        inference_class = ScKeynodes.resolve(
            "concept_message_about_hunting_inference", sc_type.CONST_NODE_CLASS
        )
        if check_connector(sc_type.VAR_PERM_POS_ARC, research_class, message_addr):
            self._run_research(action_node, message_addr)
        elif check_connector(sc_type.VAR_PERM_POS_ARC, inference_class, message_addr):
            self._run_inference(action_node, message_addr)
        return ScResult.OK

    def restore_persisted_notes(self) -> int:
        restored = 0
        for payload in self.store.load_all():
            try:
                self._materialize_note(payload)
                restored += 1
            except Exception as exc:
                self.logger.info(
                    f"Could not restore hunting note {payload.get('id')}: "
                    f"{type(exc).__name__}: {exc}"
                )
        self.logger.info("Restored %d persisted hunting notes into SC-memory", restored)
        return restored

    def _reply(self, action_node: ScAddr, text: str) -> None:
        answer_link = generate_link(
            text, ScLinkContentType.STRING, link_type=sc_type.CONST_NODE_LINK
        )
        args = get_action_arguments(action_node, 1)
        if args:
            self._publish_chat_reply(args[0], answer_link, text)
        generate_action_result(action_node, answer_link)

    def _publish_chat_reply(
        self, message_addr: ScAddr, answer_link: ScAddr, text: str
    ) -> None:
        reply_relation = ScKeynodes.resolve(
            "nrel_reply", sc_type.CONST_NODE_NON_ROLE
        )
        translation_relation = ScKeynodes.resolve(
            "nrel_sc_text_translation", sc_type.CONST_NODE_NON_ROLE
        )
        lang_ru = ScKeynodes.resolve("lang_ru", sc_type.CONST_NODE_CLASS)

        replies_template = ScTemplate()
        replies_template.quintuple(
            message_addr,
            sc_type.VAR_COMMON_ARC,
            sc_type.VAR_NODE,
            sc_type.VAR_PERM_POS_ARC,
            reply_relation,
        )
        for reply_result in search_by_template(replies_template):
            translation_template = ScTemplate()
            translation_template.quintuple(
                sc_type.VAR_NODE,
                sc_type.VAR_COMMON_ARC,
                reply_result[2],
                sc_type.VAR_PERM_POS_ARC,
                translation_relation,
            )
            for translation_result in search_by_template(translation_template):
                links_template = ScTemplate()
                links_template.triple(
                    translation_result[0],
                    sc_type.VAR_PERM_POS_ARC,
                    sc_type.VAR_NODE_LINK,
                )
                for link_result in search_by_template(links_template):
                    link = link_result[2]
                    if check_connector(sc_type.VAR_PERM_POS_ARC, lang_ru, link):
                        if set_link_contents(
                            ScLinkContent(text, ScLinkContentType.STRING, link)
                        ):
                            return

        reply_message = generate_node(sc_type.CONST_NODE)
        translation_node = generate_node(sc_type.CONST_NODE)
        concept_message = ScKeynodes.resolve(
            "concept_message", sc_type.CONST_NODE_CLASS
        )
        generate_connector(
            sc_type.CONST_PERM_POS_ARC, concept_message, reply_message
        )
        generate_connector(sc_type.CONST_PERM_POS_ARC, lang_ru, answer_link)
        generate_connector(
            sc_type.CONST_PERM_POS_ARC, translation_node, answer_link
        )
        translation_arc = generate_connector(
            sc_type.CONST_COMMON_ARC, translation_node, reply_message
        )
        generate_connector(
            sc_type.CONST_PERM_POS_ARC, translation_relation, translation_arc
        )
        reply_arc = generate_connector(
            sc_type.CONST_COMMON_ARC, message_addr, reply_message
        )
        generate_connector(
            sc_type.CONST_PERM_POS_ARC, reply_relation, reply_arc
        )

    def _role_values(self, message_addr: ScAddr, role_id: str) -> list[ScAddr]:
        role = ScKeynodes.resolve(role_id, sc_type.CONST_NODE_ROLE)
        template = ScTemplate()
        template.quintuple(
            message_addr,
            sc_type.VAR_PERM_POS_ARC,
            sc_type.VAR,
            sc_type.VAR_PERM_POS_ARC,
            role,
        )
        return [result[2] for result in search_by_template(template)]

    def _message_text(self, message_addr: ScAddr) -> str:
        relation = ScKeynodes.resolve(
            "nrel_sc_text_translation", sc_type.CONST_NODE_NON_ROLE
        )
        translation_template = ScTemplate()
        translation_template.quintuple(
            sc_type.VAR_NODE,
            sc_type.VAR_COMMON_ARC,
            message_addr,
            sc_type.VAR_PERM_POS_ARC,
            relation,
        )
        translations = search_by_template(translation_template)
        if not translations:
            return ""
        translation_node = translations[0][0]
        links_template = ScTemplate()
        links_template.triple(
            translation_node, sc_type.VAR_PERM_POS_ARC, sc_type.VAR_NODE_LINK
        )
        links = search_by_template(links_template)
        lang_ru = ScKeynodes.resolve("lang_ru", sc_type.CONST_NODE_CLASS)
        for result in links:
            link = result[2]
            if search_connector(lang_ru, link, sc_type.VAR_PERM_POS_ARC):
                return str(get_link_content_data(link)).strip()
        if links:
            return str(get_link_content_data(links[0][2])).strip()
        return ""

    def _element_id(self, element: ScAddr) -> str:
        try:
            link_content = get_link_content_data(element)
            if link_content:
                return str(link_content).strip()
        except Exception:
            pass
        return str(get_element_system_identifier(element) or "")

    def _label(self, element: ScAddr) -> str:
        main_idtf = ScKeynodes.resolve("nrel_main_idtf", sc_type.CONST_NODE_NON_ROLE)
        lang_ru = ScKeynodes.resolve("lang_ru", sc_type.CONST_NODE_CLASS)
        template = ScTemplate()
        template.quintuple(
            element,
            sc_type.VAR_COMMON_ARC,
            sc_type.VAR_NODE_LINK,
            sc_type.VAR_PERM_POS_ARC,
            main_idtf,
        )
        for result in search_by_template(template):
            link = result[2]
            if search_connector(lang_ru, link, sc_type.VAR_PERM_POS_ARC):
                label = str(get_link_content_data(link)).strip()
                if label:
                    return label
        return self._element_id(element).replace("_", " ")

    def _message_subject(self, message_addr: ScAddr) -> ScAddr | None:
        values = self._role_values(message_addr, "rrel_entity")
        return values[0] if values else None

    def _message_topic(self, message_addr: ScAddr) -> ScAddr | None:
        values = self._role_values(message_addr, "rrel_topic")
        return values[0] if values else None

    def _relation_targets(self, source: ScAddr, relation_id: str) -> list[ScAddr]:
        relation = ScKeynodes.resolve(relation_id, sc_type.CONST_NODE_NON_ROLE)
        template = ScTemplate()
        template.quintuple(
            source,
            sc_type.VAR_COMMON_ARC,
            sc_type.VAR_NODE,
            sc_type.VAR_PERM_POS_ARC,
            relation,
        )
        return [result[2] for result in search_by_template(template)]

    def _relation_sources(self, target: ScAddr, relation_id: str) -> list[ScAddr]:
        relation = ScKeynodes.resolve(relation_id, sc_type.CONST_NODE_NON_ROLE)
        template = ScTemplate()
        template.quintuple(
            sc_type.VAR_NODE,
            sc_type.VAR_COMMON_ARC,
            target,
            sc_type.VAR_PERM_POS_ARC,
            relation,
        )
        return [result[0] for result in search_by_template(template)]

    def _run_research(self, action_node: ScAddr, message_addr: ScAddr) -> None:
        self._replace_answer_structure([])
        subject = self._message_subject(message_addr)
        topic = self._message_topic(message_addr)
        message_text = self._message_text(message_addr)
        if subject is None:
            self._reply(
                action_node,
                "Укажите животное и тему. Например: «Найди общую информацию о зайце и добавь её в базу знаний».",
            )
            return
        subject_id = self._element_id(subject)
        subject_label = self._label(subject)
        if SAVED_NOTE_REQUEST.search(message_text):
            notes = [
                item
                for item in self.store.load_all()
                if item.get("kind") == "external"
                and item.get("subject_system_id") == subject_id
            ]
            if not notes:
                self._reply(
                    action_node,
                    f"Сохранённых внешних заметок о «{subject_label}» пока нет. "
                    f"Чтобы добавить первую, напишите: «Найди общую информацию о {subject_label} и добавь её в базу знаний».",
                )
                return
            self._replace_answer_structure(
                self._knowledge_note_graph_elements(subject, notes[-3:])
            )
            lines = []
            for note in notes[-3:]:
                lines.append(
                    f"Тема: {note.get('topic_label', 'справка')}. "
                    f"Источник: {note.get('source_title', 'источник')} — {note.get('source_url', '')}. "
                    f"Фрагмент: {note.get('content', '')}"
                )
            self._reply(
                action_node,
                f"Сохранённые внешние заметки о «{subject_label}»:\n" + "\n".join(lines),
            )
            return
        if topic is None:
            self._reply(
                action_node,
                f"Объект распознан: «{subject_label}». Уточните тему: общая информация, "
                "место обитания, ареал, особенности или гон. "
                f"Пример: «Найди сведения о гоне {subject_label} и добавь заметку».",
            )
            return
        topic_id = self._element_id(topic)
        topic_label = TOPIC_LABELS.get(topic_id, self._label(topic))
        query = (
            subject_label
            if topic_id == "hunting_research_topic_general"
            else " ".join(value for value in (subject_label, topic_label) if value).strip()
        )
        try:
            source = self._search_wikipedia(query, subject_id, topic_id)
        except (requests.RequestException, ValueError, KeyError) as exc:
            self.logger.info(f"Wikipedia search failed: {type(exc).__name__}")
            self._reply(
                action_node,
                "Не получилось получить страницу внешнего источника. Попробуйте повторить запрос позже.",
            )
            return
        if source is None:
            self._reply(
                action_node,
                f"В русской Википедии не нашлась подходящая страница по запросу «{query}».",
            )
            return

        payload = {
            "kind": "external",
            "subject_system_id": subject_id,
            "subject_label": subject_label,
            "topic_system_id": topic_id,
            "topic_label": topic_label,
            "query": query,
            "user_question": self._message_text(message_addr),
            "content": source["extract"],
            "source_title": source["title"],
            "source_url": source["url"],
            "retrieved_at": _utc_now(),
            "derivation_basis": [],
        }
        try:
            saved, created = self.store.save(payload)
            self._materialize_note(saved)
        except Exception as exc:
            self.logger.info(
                f"Could not persist or add research note: {type(exc).__name__}: {exc}"
            )
            self._reply(
                action_node,
                "Источник найден, но сохранить заметку в базе знаний сейчас не удалось.",
            )
            return
        self._replace_answer_structure(
            self._knowledge_note_graph_elements(subject, [saved])
        )
        status = "Добавила новую заметку" if created else "Такая заметка уже есть"
        self._reply(
            action_node,
            f"{status} по теме «{subject_label}: {topic_label}». "
            f"Источник: {saved['source_title']} — {saved['source_url']}\n"
            f"Фрагмент: {saved['content']}\n"
            "Это внешний справочный материал; перед использованием проверьте первоисточник.",
        )

    @staticmethod
    def _search_wikipedia(
        query: str, subject_id: str, topic_id: str
    ) -> dict[str, str] | None:
        headers = {
            "User-Agent": "NIKA-HuntingKnowledgeLab/1.0 (educational prototype)"
        }
        search_response = requests.get(
            WIKIPEDIA_API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srnamespace": 0,
                "srlimit": 8,
                "srprop": "snippet",
                "format": "json",
                "formatversion": 2,
            },
            headers=headers,
            timeout=(5, 15),
        )
        search_response.raise_for_status()
        candidates = search_response.json().get("query", {}).get("search", [])
        subject_terms = SUBJECT_TITLE_TERMS.get(subject_id)
        if not subject_terms:
            subject_terms = tuple(
                term.casefold()
                for term in re.findall(r"[\w-]+", query, flags=re.UNICODE)
                if len(term) > 2
            )
        topic_pattern = TOPIC_CONTENT_PATTERNS.get(topic_id)

        for candidate in candidates:
            title = str(candidate.get("title", ""))
            normalized_title = title.casefold().replace("ё", "е")
            if not any(term.casefold().replace("ё", "е") in normalized_title for term in subject_terms):
                continue
            if subject_id == "elk" and "американ" in normalized_title:
                continue

            page_response = requests.get(
                WIKIPEDIA_API,
                params={
                    "action": "query",
                    "prop": "extracts|info",
                    "titles": title,
                    "explaintext": 1,
                    "exchars": MAX_SOURCE_CHARS,
                    "inprop": "url",
                    "format": "json",
                    "formatversion": 2,
                },
                headers=headers,
                timeout=(5, 15),
            )
            page_response.raise_for_status()
            pages = page_response.json().get("query", {}).get("pages", [])
            if not pages:
                continue
            page = pages[0]
            raw_extract = str(page.get("extract", "")).strip()
            if not raw_extract:
                continue

            if topic_pattern is None:
                excerpt = re.split(r"(?m)^={2,}", raw_extract, maxsplit=1)[0].strip()
            else:
                excerpt = HuntingKnowledgeAgent._topic_excerpt(raw_extract, topic_pattern)
                if not excerpt:
                    snippet = html.unescape(
                        re.sub(r"<[^>]+>", " ", str(candidate.get("snippet", "")))
                    )
                    snippet = " ".join(snippet.split())
                    if topic_pattern.search(snippet):
                        excerpt = snippet
            excerpt = " ".join(excerpt.split())
            if not excerpt:
                continue
            return {
                "title": str(page.get("title", title)),
                "url": str(page.get("fullurl", "")),
                "extract": excerpt[:MAX_EXCERPT_CHARS],
            }
        return None

    @staticmethod
    def _topic_excerpt(raw_extract: str, pattern: re.Pattern[str]) -> str:
        sections = re.split(r"(?m)^={2,}\s*(.+?)\s*={2,}\s*$", raw_extract)
        for index in range(1, len(sections), 2):
            heading = sections[index]
            body = sections[index + 1] if index + 1 < len(sections) else ""
            if pattern.search(heading) and body.strip():
                return body.strip()

        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", raw_extract) if part.strip()]
        relevant = []
        for paragraph in paragraphs:
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            relevant.extend(sentence.strip() for sentence in sentences if pattern.search(sentence))
        if relevant:
            return " ".join(relevant)
        return ""

    def _run_inference(self, action_node: ScAddr, message_addr: ScAddr) -> None:
        self._replace_answer_structure([])
        subject = self._message_subject(message_addr)
        topic = self._message_topic(message_addr)
        if subject is None:
            self._reply(
                action_node,
                "Для вывода из графа укажите объект и вопрос. Готовые примеры:\n"
                "• Выведи из графа виды семейства оленевых, представленные в базе.\n"
                "• Выведи общую среду обитания боровых птиц.\n"
                "• Выведи, какое снаряжение связано с охотой на лося.",
            )
            return
        subject_id = self._element_id(subject)
        topic_id = self._element_id(topic) if topic is not None else ""
        subject_label = self._label(subject)
        if not topic_id:
            self._reply(
                action_node,
                f"Объект распознан: «{subject_label}». Уточните, что вывести из графа. "
                "Для животного спросите о методе охоты и связанном с ним снаряжении; "
                "для группы — о её представителях или общей среде обитания. "
                "Например: «Выведи, какое снаряжение связано с охотой на лося».",
            )
            return
        normalized_subject_id = _normalize_sc_identifier(subject_id)
        normalized_topic_id = _normalize_sc_identifier(topic_id)
        subject_marker = f"{subject_id} {subject_label}"
        topic_label = self._label(topic) if topic is not None else ""
        topic_marker = f"{topic_id} {topic_label}"
        is_cervidae_group = (
            normalized_subject_id
            in {_normalize_sc_identifier(item) for item in CERVIDAE_GROUP_IDS}
            or _contains_any(subject_marker, CERVIDAE_LABEL_MARKERS)
        )
        is_group = (
            normalized_subject_id
            in {_normalize_sc_identifier(item) for item in GROUP_NODE_IDS}
            or is_cervidae_group
            or _contains_any(subject_marker, ("групп", "копыт", "птиц", "mammal"))
        )
        if is_cervidae_group:
            canonical_family = ScKeynodes.resolve(
                "family_cervidae", sc_type.CONST_NODE
            )
            if canonical_family.is_valid():
                subject = canonical_family
                subject_id = self._element_id(subject)
                subject_label = self._label(subject)
        if is_group:
            is_taxonomy_topic = (
                normalized_topic_id
                in {_normalize_sc_identifier(item) for item in TAXONOMY_TOPIC_IDS}
                or _contains_any(topic_marker, TAXONOMY_LABEL_MARKERS)
            )
            is_habitat_topic = (
                normalized_topic_id
                in {
                    *(_normalize_sc_identifier(item) for item in HABITAT_TOPIC_IDS),
                    *(_normalize_sc_identifier(item) for item in GENERAL_TOPIC_IDS),
                }
                or _contains_any(
                    topic_marker, HABITAT_LABEL_MARKERS + GENERAL_LABEL_MARKERS
                )
            )
            if is_taxonomy_topic:
                result = self._infer_group_members(subject)
            elif is_habitat_topic:
                result = self._infer_group_habitats(subject, topic_marker)
            else:
                self._reply(
                    action_node,
                    f"Для группы «{subject_label}» из графа можно вывести её состав "
                    "или общую среду обитания. Уточните один из этих аспектов.",
                )
                return
        else:
            is_supported_animal_topic = (
                normalized_topic_id
                in {
                    "hunting_research_topic_hunting_method",
                    "hunting_research_topic_equipment",
                    "hunting_research_topic_general",
                }
                or _contains_any(
                    topic_marker,
                    HUNTING_METHOD_LABEL_MARKERS
                    + EQUIPMENT_LABEL_MARKERS
                    + GENERAL_LABEL_MARKERS,
                )
            )
            if not is_supported_animal_topic:
                self._reply(
                    action_node,
                    f"Для «{subject_label}» граф поддерживает вывод связи метода охоты "
                    "со снаряжением. Попробуйте: «Выведи, какое снаряжение связано с охотой на лося».",
                )
                return
            result = self._infer_method_equipment(subject)
        if result is None:
            self._reply(
                action_node,
                "В текущем графе не нашлось полной цепочки связей для этого вывода. "
                "Добавьте недостающие факты или задайте другой объект.",
            )
            return
        content, basis, graph_elements = result
        payload = {
            "kind": "derived",
            "subject_system_id": subject_id,
            "subject_label": subject_label,
            "topic_system_id": topic_id,
            "topic_label": TOPIC_LABELS.get(topic_id, self._label(topic) if topic else "вывод из графа"),
            "query": "inference from current SC graph",
            "user_question": self._message_text(message_addr),
            "content": content,
            "source_title": "Текущий граф знаний NIKA",
            "source_url": "",
            "retrieved_at": _utc_now(),
            "derivation_basis": basis,
        }
        saved, created = self.store.save(payload)
        self._materialize_note(saved)
        self._replace_answer_structure(graph_elements)
        marker = "Сохранила вывод" if created else "Повторный вывод"
        self._reply(
            action_node,
            f"{marker} по текущему графу. {saved['content']} "
            "Это следствие учебных связей, заданных в базе NIKA.",
        )

    def _infer_method_equipment(
        self, subject: ScAddr
    ) -> tuple[str, list[str], list[ScAddr]] | None:
        methods = self._relation_targets(subject, "nrel_hunting_method")
        derivations: list[str] = []
        rows: list[str] = []
        graph_elements: list[ScAddr] = [subject]
        for method in methods:
            method_id = self._element_id(method)
            equipment = self._relation_targets(method, "nrel_equipment")
            equipment_relation = "nrel_equipment"
            if not equipment:
                equipment = self._relation_targets(method, "nrel_weapon")
                equipment_relation = "nrel_weapon"
            labels = [self._label(item) for item in equipment]
            if not labels:
                continue
            rows.append(f"{self._label(method)} → {', '.join(labels)}")
            graph_elements.extend(
                self._relation_graph_elements(subject, method, "nrel_hunting_method")
            )
            for item in equipment:
                graph_elements.extend(
                    self._relation_graph_elements(method, item, equipment_relation)
                )
            derivations.extend(
                f"{self._element_id(subject)} --nrel_hunting_method--> {method_id} "
                f"--nrel_equipment/nrel_weapon--> {self._element_id(item)}"
                for item in equipment
            )
        if not rows:
            return None
        content = (
            f"Для «{self._label(subject)}» граф указывает метод(ы): "
            + "; ".join(rows)
            + ". Следовательно, перечисленное снаряжение связано с животным через этот метод."
        )
        return content, derivations, graph_elements

    def _infer_group_habitats(
        self, group: ScAddr, topic_id: str
    ) -> tuple[str, list[str], list[ScAddr]] | None:
        normalized_topic_id = _normalize_sc_identifier(topic_id)
        if topic_id and normalized_topic_id not in {
            *(_normalize_sc_identifier(item) for item in HABITAT_TOPIC_IDS),
            *(_normalize_sc_identifier(item) for item in GENERAL_TOPIC_IDS),
        } and not _contains_any(
            topic_id, HABITAT_LABEL_MARKERS + GENERAL_LABEL_MARKERS
        ):
            return None
        group_id = self._element_id(group)
        membership_relation = (
            "nrel_taxonomic_family"
            if _normalize_sc_identifier(group_id)
            in {_normalize_sc_identifier(item) for item in CERVIDAE_GROUP_IDS}
            else "nrel_hunting_animal_group"
        )
        members = self._relation_sources(group, membership_relation)
        if not members:
            return None
        habitats_by_member = [
            (member, self._relation_targets(member, "nrel_habitat")) for member in members
        ]
        if any(not habitats for _, habitats in habitats_by_member):
            return None
        common_ids = {
            self._element_id(habitat) for habitat in habitats_by_member[0][1]
        }
        for _, habitats in habitats_by_member[1:]:
            common_ids.intersection_update(self._element_id(habitat) for habitat in habitats)
        if not common_ids:
            return None
        common_nodes = {
            self._element_id(habitat): habitat
            for _, habitats in habitats_by_member
            for habitat in habitats
            if self._element_id(habitat) in common_ids
        }
        labels = sorted({self._label(common_nodes[identifier]) for identifier in common_ids})
        member_labels = [self._label(member) for member, _ in habitats_by_member]
        basis = [
            f"{self._element_id(member)} --{membership_relation}--> {self._element_id(group)}; "
            f"nrel_habitat={','.join(self._element_id(item) for item in habitats)}"
            for member, habitats in habitats_by_member
        ]
        graph_elements: list[ScAddr] = [group]
        for member, habitats in habitats_by_member:
            graph_elements.extend(
                self._relation_graph_elements(member, group, membership_relation)
            )
            for habitat in habitats:
                graph_elements.extend(
                    self._relation_graph_elements(member, habitat, "nrel_habitat")
                )
        content = (
            f"У всех {len(member_labels)} представленных в графе представителей группы "
            f"«{self._label(group)}» общая указанная среда — {', '.join(labels)}. "
            f"В расчёт вошли: {', '.join(member_labels)}."
        )
        return content, basis, graph_elements

    def _infer_group_members(
        self, group: ScAddr
    ) -> tuple[str, list[str], list[ScAddr]] | None:
        group_id = self._element_id(group)
        membership_relation = (
            "nrel_taxonomic_family"
            if _normalize_sc_identifier(group_id)
            in {_normalize_sc_identifier(item) for item in CERVIDAE_GROUP_IDS}
            else "nrel_hunting_animal_group"
        )
        members = self._relation_sources(group, membership_relation)
        if not members:
            return None
        labels = sorted({self._label(member) for member in members})
        basis = [
            f"{self._element_id(member)} --{membership_relation}--> {group_id}"
            for member in members
        ]
        graph_elements: list[ScAddr] = [group]
        for member in members:
            graph_elements.extend(
                self._relation_graph_elements(member, group, membership_relation)
            )
        content = (
            f"В графе группа «{self._label(group)}» представлена {len(labels)} объектами: "
            f"{', '.join(labels)}. Это перечень узлов текущей учебной модели, а не полный список "
            "всех биологических видов группы."
        )
        return content, basis, graph_elements

    def _relation_graph_elements(
        self, source: ScAddr, target: ScAddr, relation_id: str
    ) -> list[ScAddr]:
        relation = ScKeynodes.resolve(relation_id, sc_type.CONST_NODE_NON_ROLE)
        template = ScTemplate()
        template.quintuple(
            source,
            sc_type.VAR_COMMON_ARC,
            target,
            sc_type.VAR_PERM_POS_ARC,
            relation,
        )
        elements: list[ScAddr] = [source, target, relation]
        for result in search_by_template(template):
            elements.extend((result[1], result[3]))
        return elements

    def _replace_answer_structure(self, elements: list[ScAddr]) -> None:
        structure = ScKeynodes.resolve(
            "answer_structure", sc_type.CONST_NODE_STRUCTURE
        )
        if not structure.is_valid():
            self.logger.info("Could not resolve answer_structure for the SC Web viewer")
            return

        template = ScTemplate()
        template.triple(
            structure, sc_type.VAR_PERM_POS_ARC, sc_type.VAR
        )
        old_arcs = [result[1] for result in search_by_template(template)]
        if old_arcs:
            erase_elements(*old_arcs)

        seen: set[Any] = set()
        for element in elements:
            if not element.is_valid() or element.value in seen:
                continue
            seen.add(element.value)
            generate_connector(
                sc_type.CONST_PERM_POS_ARC, structure, element
            )

    def _knowledge_note_graph_elements(
        self, subject: ScAddr, payloads: list[dict[str, Any]]
    ) -> list[ScAddr]:
        elements: list[ScAddr] = [subject]
        note_relations = (
            "nrel_knowledge_note_content",
            "nrel_knowledge_source_title",
            "nrel_knowledge_source_url",
            "nrel_knowledge_query",
            "nrel_knowledge_retrieved_at",
            "nrel_knowledge_derivation_basis",
        )
        for payload in payloads:
            note = self._materialize_note(payload)
            elements.extend(
                self._relation_graph_elements(
                    subject, note, "nrel_has_knowledge_note"
                )
            )
            topic_id = payload.get("topic_system_id", "")
            topic = ScKeynodes.resolve(topic_id, sc_type.CONST_NODE) if topic_id else None
            if topic is not None and topic.is_valid():
                elements.extend(
                    self._relation_graph_elements(note, topic, "nrel_knowledge_note_topic")
                )
            for relation_id in note_relations:
                for linked_element in self._relation_targets(note, relation_id):
                    elements.extend(
                        self._relation_graph_elements(note, linked_element, relation_id)
                    )
        return elements

    @staticmethod
    def _annotate_relation(source: ScAddr, target: ScAddr, relation_id: str) -> None:
        relation = ScKeynodes.resolve(relation_id, sc_type.CONST_NODE_NON_ROLE)
        template = ScTemplate()
        template.quintuple(
            source,
            sc_type.VAR_COMMON_ARC,
            target,
            sc_type.VAR_PERM_POS_ARC,
            relation,
        )
        if search_by_template(template):
            return
        connector = generate_connector(sc_type.CONST_COMMON_ARC, source, target)
        generate_connector(sc_type.CONST_PERM_POS_ARC, relation, connector)

    def _attach_text(self, note: ScAddr, relation_id: str, value: str) -> None:
        for link in self._relation_targets(note, relation_id):
            if str(get_link_content_data(link)).strip() == value:
                return
        link = generate_link(
            value, ScLinkContentType.STRING, link_type=sc_type.CONST_NODE_LINK
        )
        self._annotate_relation(note, link, relation_id)

    def _ensure_note_identifier(self, note: ScAddr, payload: dict[str, Any]) -> None:
        relation = ScKeynodes.resolve("nrel_main_idtf", sc_type.CONST_NODE_NON_ROLE)
        lang_ru = ScKeynodes.resolve("lang_ru", sc_type.CONST_NODE_CLASS)
        prefix = "Внешняя заметка" if payload.get("kind") == "external" else "Вывод из графа"
        subject = payload.get("subject_label") or payload.get("subject_system_id", "объект")
        topic = payload.get("topic_label") or "без отдельной темы"
        source = payload.get("source_title")
        label = f"{prefix}: {subject} — {topic}"
        if source:
            label += f" ({source})"

        template = ScTemplate()
        template.quintuple(
            note,
            sc_type.VAR_COMMON_ARC,
            sc_type.VAR_NODE_LINK,
            sc_type.VAR_PERM_POS_ARC,
            relation,
        )
        for result in search_by_template(template):
            if str(get_link_content_data(result[2])).strip() == label:
                if search_connector(lang_ru, result[2], sc_type.VAR_PERM_POS_ARC):
                    return

        identifier_link = generate_link(
            label, ScLinkContentType.STRING, link_type=sc_type.CONST_NODE_LINK
        )
        identifier_arc = generate_connector(
            sc_type.CONST_COMMON_ARC, note, identifier_link
        )
        generate_connector(sc_type.CONST_PERM_POS_ARC, relation, identifier_arc)
        generate_connector(sc_type.CONST_PERM_POS_ARC, lang_ru, identifier_link)

    def _materialize_note(self, payload: dict[str, Any]) -> ScAddr:
        kind = payload["kind"]
        note_id = f"hunting_knowledge_note_{payload['id']}"
        note_addr = ScKeynodes.resolve(note_id, sc_type.CONST_NODE)
        if not note_addr.is_valid():
            class_id = (
                "concept_hunting_research_note"
                if kind == "external"
                else "concept_hunting_derived_note"
            )
            generate_elements_by_scs([f"{note_id} <- {class_id};;"])
            note_addr = ScKeynodes.resolve(note_id, sc_type.CONST_NODE)
            if not note_addr.is_valid():
                raise RuntimeError("SC-memory did not return the created note node")

        self._ensure_note_identifier(note_addr, payload)

        subject_id = payload.get("subject_system_id", "")
        subject = ScKeynodes.resolve(subject_id, sc_type.CONST_NODE) if subject_id else None
        if subject is not None and subject.is_valid():
            self._annotate_relation(note_addr, subject, "nrel_knowledge_note_subject")
            self._annotate_relation(subject, note_addr, "nrel_has_knowledge_note")
        topic_id = payload.get("topic_system_id", "")
        topic = ScKeynodes.resolve(topic_id, sc_type.CONST_NODE) if topic_id else None
        if topic is not None and topic.is_valid():
            self._annotate_relation(note_addr, topic, "nrel_knowledge_note_topic")

        self._attach_text(note_addr, "nrel_knowledge_note_content", payload.get("content", ""))
        if payload.get("source_title"):
            self._attach_text(note_addr, "nrel_knowledge_source_title", payload["source_title"])
        if payload.get("source_url"):
            self._attach_text(note_addr, "nrel_knowledge_source_url", payload["source_url"])
        if payload.get("query"):
            self._attach_text(note_addr, "nrel_knowledge_query", payload["query"])
        if payload.get("retrieved_at"):
            self._attach_text(note_addr, "nrel_knowledge_retrieved_at", payload["retrieved_at"])
        basis = payload.get("derivation_basis")
        if basis:
            self._attach_text(
                note_addr,
                "nrel_knowledge_derivation_basis",
                json.dumps(basis, ensure_ascii=False),
            )
        return note_addr
