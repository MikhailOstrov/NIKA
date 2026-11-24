import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


ENTITY_RULES = [
    ("охотничья собака", "hunting_dog"),
    ("маскировочную одежду", "camouflage_clothing"),
    ("охотничью засидку", "hunting_stand"),
    ("охотничий нож", "hunting_knife"),
    ("гладкоствольное ружьё", "shotgun"),
    ("охотничью винтовку", "rifle"),
    ("безопасность навигации", "navigation_safety"),
    ("обращении с оружием", "firearm_safety"),
    ("охота с подхода", "stalking_hunt"),
    ("охоту из засады", "hunting_from_ambush"),
    ("загонная охота", "driven_hunt"),
    ("охота по следу", "tracking_hunt"),
    ("бинокль", "binoculars"),
    ("компас", "compass"),
    ("гончую", "hound"),
    ("лось", "elk"),
    ("лося", "elk"),
    ("кабан", "wild_boar"),
    ("кабане", "wild_boar"),
    ("косуля", "roe_deer"),
    ("олень", "red_deer"),
    ("заяц", "hare"),
    ("зайца", "hare"),
    ("лиса", "fox"),
    ("утка", "duck"),
    ("гуся", "goose"),
    ("гусь", "goose"),
    ("безопасность", "firearm_safety"),
    ("собака", "hunting_dog"),
    ("охоты с подхода", "stalking_hunt"),
    ("охоте с подхода", "stalking_hunt"),
    ("охота из засидки", "hunting_from_ambush"),
    ("охоты из засидки", "hunting_from_ambush"),
    ("загонной охоты", "driven_hunt"),
    ("загонную охоту", "driven_hunt"),
    ("охоты по следу", "tracking_hunt"),
    ("охота на птицу", "bird_hunting"), ("охоты на птицу", "bird_hunting"),
    ("охота с собакой", "hunting_with_dog"), ("охоты с собакой", "hunting_with_dog"),
    ("маскировочная одежда", "camouflage_clothing"), ("маскировочной одежды", "camouflage_clothing"),
    ("охотничья засидка", "hunting_stand"), ("охотничья винтовка", "rifle"),
    ("безопасность в группе", "group_safety"), ("гончая", "hound"),
    ("радиостанция", "radio_station"), ("радиостанции", "radio_station"),
    ("копытные", "group_ungulates"), ("копытным", "group_ungulates"), ("копытных", "group_ungulates"),
    ("боровая дичь", "group_forest_birds"), ("боровой дичи", "group_forest_birds"),
    ("боровые птицы", "group_forest_birds"), ("боровым птицам", "group_forest_birds"),
    ("водоплавающие птицы", "group_waterfowl"), ("водоплавающим птицам", "group_waterfowl"),
    ("птицы", "group_birds"), ("птицам", "group_birds"),
    ("млекопитающие", "group_mammals"), ("млекопитающим", "group_mammals"),
    ("лося", "elk"), ("лосе", "elk"), ("лосем", "elk"),
    ("кабана", "wild_boar"), ("кабане", "wild_boar"),
    ("косули", "roe_deer"), ("косулю", "roe_deer"),
    ("оленя", "red_deer"), ("олене", "red_deer"), ("оленем", "red_deer"),
    ("лисица", "fox"), ("лисицы", "fox"), ("лисы", "fox"),
    ("утки", "duck"), ("гуся", "goose"), ("гусях", "goose"),
    ("кряква", "mallard"), ("кряквы", "mallard"), ("крякве", "mallard"), ("кряквой", "mallard"),
    ("глухарь", "capercaillie"), ("глухаря", "capercaillie"),
    ("тетерев", "black_grouse"), ("тетерева", "black_grouse"),
    ("рябчик", "hazel_grouse"), ("рябчика", "hazel_grouse"),
    ("вальдшнеп", "woodcock"), ("вальдшнепа", "woodcock"),
]

CANONICAL_NAMES = {
    "elk": "лось", "wild_boar": "кабан", "roe_deer": "косуля",
    "red_deer": "олень", "hare": "заяц", "fox": "лиса", "duck": "утка",
    "goose": "гусь", "mallard": "кряква", "capercaillie": "глухарь",
    "black_grouse": "тетерев", "hazel_grouse": "рябчик", "woodcock": "вальдшнеп",
    "stalking_hunt": "охота с подхода", "hunting_from_ambush": "охота из засидки",
    "driven_hunt": "загонная охота", "tracking_hunt": "охота по следу",
    "bird_hunting": "охота на птицу", "hunting_with_dog": "охота с собакой",
    "hunting_dog": "охотничья собака", "hound": "гончая",
    "camouflage_clothing": "маскировочная одежда", "hunting_stand": "охотничья засидка",
    "radio_station": "радиостанция",
    "hunting_knife": "охотничий нож", "shotgun": "гладкоствольное ружьё",
    "rifle": "охотничья винтовка", "binoculars": "бинокль", "compass": "компас",
    "firearm_safety": "безопасность при обращении с оружием", "navigation_safety": "безопасность навигации",
    "group_safety": "безопасность в группе",
    "group_ungulates": "копытное охотничье животное",
    "group_forest_birds": "боровая охотничья птица",
    "group_waterfowl": "водоплавающая птица", "group_birds": "птица",
    "group_mammals": "млекопитающее",
}

ANIMAL_IDS = {
    "elk", "wild_boar", "roe_deer", "red_deer", "hare", "fox", "duck", "goose",
    "mallard", "capercaillie", "black_grouse", "hazel_grouse", "woodcock",
}
METHOD_IDS = {
    "stalking_hunt", "hunting_from_ambush", "driven_hunt", "tracking_hunt",
    "bird_hunting", "hunting_with_dog",
}
GROUP_IDS = {"group_ungulates", "group_forest_birds", "group_waterfowl", "group_birds", "group_mammals"}


def classify(text: str) -> dict:
    lowered = text.lower()
    selected = None
    for expression, identifier in sorted(ENTITY_RULES, key=lambda item: len(item[0]), reverse=True):
        start = lowered.find(expression)
        if start >= 0:
            selected = (expression, identifier, start)
            break

    identifier = selected[1] if selected else None
    intent = None
    if identifier in GROUP_IDS and any(word in lowered for word in ("какие", "каких", "перечисли", "кто относится", "представител")):
        intent = "about_hunting_group_members"
    elif identifier in GROUP_IDS:
        intent = "about_hunting_group_definition"
    elif re.search(r"\bгон(?:а|е|у)?\b", lowered) or any(
        marker in lowered for marker in ("размнож", "токован", "току", "гнезд", "брачный период")
    ):
        intent = "about_hunting_breeding"
    elif any(marker in lowered for marker in ("к какой группе", "к какому классу", "к какому отряду", "классифиц", "группа животного")):
        intent = "about_hunting_group"
    elif any(marker in lowered for marker in ("место обитания", "обитает", "обитают", "живёт", "живет", "живут", "где водится")):
        intent = "about_hunting_habitat"
    elif identifier in METHOD_IDS and any(marker in lowered for marker in ("снаряж", "оборудован", "используют", "используется")):
        intent = "about_hunting_method_equipment"
    elif identifier in ANIMAL_IDS and any(marker in lowered for marker in ("метод охоты", "методы охоты", "способ охоты", "способы охоты")):
        intent = "about_hunting_animal_method"
    elif identifier in ANIMAL_IDS and any(marker in lowered for marker in ("особенност", "характеристик", "расскажи", "описание")):
        intent = "about_hunting_features"
    elif identifier in ANIMAL_IDS:
        intent = "about_hunting_animal"
    elif identifier in METHOD_IDS:
        intent = "about_hunting_method"
    elif identifier in {"hunting_dog", "hound"}:
        intent = "about_hunting_dog"
    elif identifier in {"shotgun", "rifle", "hunting_knife"}:
        intent = "about_hunting_weapon"
    elif identifier in {"binoculars", "camouflage_clothing", "hunting_stand", "radio_station", "compass"}:
        intent = "about_hunting_equipment"
    elif identifier in {"firearm_safety", "navigation_safety", "group_safety"}:
        intent = "about_hunting_safety"

    entities = {}
    if selected:
        expression, identifier, start = selected
        entities["rrel_entity:rrel_entity"] = [
            {
                "name": "rrel_entity",
                "role": "rrel_entity",
                "start": start,
                "end": start + len(expression),
                "body": expression,
                # NIKA matches Wit entity values against Russian nrel_main_idtf
                # values in the knowledge graph, so return the canonical Russian name.
                "value": CANONICAL_NAMES.get(identifier, expression),
                "type": "value",
            }
        ]

    return {
        "text": text,
        "intents": [{"name": intent, "confidence": 1.0}] if intent else [],
        "entities": entities,
        "traits": {},
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        query = parse_qs(urlparse(self.path).query)
        text = query.get("q", [""])[0]
        payload = classify(text)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):  # noqa: A002
        print(format % args, flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
