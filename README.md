# NIKA — ТиИСПИС, вариант 138 «Охота»

Репозиторий содержит рабочую копию NIKA с реализацией лабораторных работ 1–4 и отдельную подборку SCs/GWF-файлов для просмотра. Отчётов здесь нет.

Проект основан на [NIKA](https://github.com/ostis-apps/nika) и использует технологию OSTIS. Основные изменения для предметной области «Охота» находятся в базе знаний и агентах проекта.

## Состав проекта

- `knowledge-base/extra/hunting/` — предметная область, факты, классы сообщений, фразы и GWF-правила.
- `jev-adapter/` — Python-адаптер Jev/OpenRouter. Он классифицирует запрос и возвращает NIKA JSON в совместимом с Wit формате.
- `problem-solver/py/modules/messageProcessingModule/HuntingKnowledgeAgent.py` — Python-агент внешнего поиска, сохранения заметок и вывода знаний.
- `Исходники_Охота/` — отдельные копии SCs и GWF для просмотра лабораторных 1–3. Это справочная подборка; активные файлы находятся в `knowledge-base/extra/hunting/`.
- `docker-compose.yml`, `docker-compose.lab.yml`, `docker-compose.jev.yml` — конфигурация запуска NIKA, лабораторного окружения и Jev.

## Запуск

Нужны Git, Docker Desktop с Compose и ключ OpenRouter для работы Jev.

Склонируйте репозиторий и загрузите закреплённые версии подмодулей:

```powershell
git config --global core.longpaths true
git clone -c core.autocrlf=true https://github.com/MikhailOstrov/NIKA.git
cd NIKA
git submodule update --init --recursive
```

Создайте локальные конфигурационные файлы из шаблонов. В корневой `.env` впишите свой ключ OpenRouter:

```powershell
Copy-Item .env.example .env
Copy-Item interface/.env.example interface/.env
notepad .env
```

Запустите NIKA с Jev:

```powershell
docker compose -f docker-compose.yml -f docker-compose.lab.yml -f docker-compose.jev.yml up --build
```

- Чат NIKA: [http://localhost:3033](http://localhost:3033)
- SC Web для просмотра графа: [http://localhost:8001](http://localhost:8001)
- SC-сервер: порт `8090`

Остановить контейнеры можно командой:

```powershell
docker compose -f docker-compose.yml -f docker-compose.lab.yml -f docker-compose.jev.yml down
```

Внешние заметки ЛР4 хранятся локально в `runtime-data/hunting/`: SQLite используется как основное хранилище, JSONL — как журнал восстановления. Эти данные создаются во время работы и в Git не добавляются.

## Что запускает Compose

- `problem-solver` — SC-machine и граф знаний NIKA;
- `py-sc-server` — Python SC-агенты, включая `HuntingKnowledgeAgent`;
- `nika-ui` — чат по порту 3033;
- `web` из подмодуля `sc-web` — визуализатор графа по порту 8001;
- `wit-mock` в лабораторном Compose-слое — совместимый интерфейс классификатора. При подключении `docker-compose.jev.yml` его заменяет Jev-адаптер.

Агент ЛР4 зарегистрирован в Python-модуле обработки сообщений и вызывается через SC-действие `action_hunting_knowledge`. Результат внешнего поиска сохраняется как заметка в графе с источником. Для устойчивого хранения агент использует локальные SQLite и JSONL-файл в `runtime-data/hunting/`.

## Где смотреть лабораторные

### Лабораторная 1 — предметная область и граф знаний

Для быстрого просмотра конечных определений откройте сводные [subject_domain.scs](Исходники_Охота/subject_domain.scs) и [relations.scs](Исходники_Охота/relations.scs). В рабочей базе NIKA понятия и отношения организованы модульно: [файлы понятий](knowledge-base/extra/hunting/concepts/) и [файлы отношений](knowledge-base/extra/hunting/relations/). Экземпляры и факты находятся в [instances.scs](knowledge-base/extra/hunting/instances.scs) и [book_facts.scs](knowledge-base/extra/hunting/book_facts.scs).

Покажите в SC Web, как экземпляр животного связан с классом и фактами: например, лося с группой копытных, местом обитания и периодом размножения.

### Лабораторная 2 — классификация сообщений

SCs-описания сообщений находятся в [entity_classification.scs](knowledge-base/extra/hunting/entity_classification.scs), [messages.scs](knowledge-base/extra/hunting/messages.scs) и `message_classes/`.

Jev-адаптер состоит из [catalog.py](jev-adapter/catalog.py) и [server.py](jev-adapter/server.py). Каталог задаёт допустимые категории запросов, объектов и тем. Jev возвращает выборы в JSON; адаптер переводит их в роли NIKA, включая `rrel_entity` и `rrel_topic`.

Примеры JSON:

- [запрос Jev](jev-adapter/examples/jev_decisions_request_example.json) — сокращённый пример структуры; в работе адаптер передаёт полные каталоги из `catalog.py`;
- [ответ Jev](jev-adapter/examples/jev_decisions_response.json);
- [JSON адаптера для NIKA](jev-adapter/examples/nika_compatible_response.json).

Адаптер не генерирует текст ответа пользователю. Он определяет класс запроса, сущность и при необходимости тему.

### Лабораторная 3 — GWF-правила и формирование ответа

Основное правило ответа о гоне: [lr_hunting_breeding_reply.gwf](knowledge-base/extra/hunting/logic_rules/lr_hunting_breeding_reply.gwf). Вариант для отсутствующего в графе факта: [lr_hunting_breeding_unknown_reply.gwf](knowledge-base/extra/hunting/logic_rules/lr_hunting_breeding_unknown_reply.gwf).

SCs-шаблоны фраз находятся в [phrases.scs](knowledge-base/extra/hunting/phrases.scs), а [rules.scs](knowledge-base/extra/hunting/rules.scs) связывает правила с базой знаний.

Для сквозного показа отправьте: **«Когда гон у лося?»**. Jev распознаёт тип вопроса и животное, NIKA находит факт в графе, GWF-правило выбирает ответный шаблон.

### Лабораторная 4 — внешний поиск и вывод знаний

Python-агент: [HuntingKnowledgeAgent.py](problem-solver/py/modules/messageProcessingModule/HuntingKnowledgeAgent.py). Его действие описано в [action_hunting_knowledge.scs](knowledge-base/section_subject_domain_of_actions_and_tasks/concepts/action_hunting_knowledge.scs), а запуск включён в [message_processing_program.gwf](knowledge-base/section_subject_domain_of_dialogues/section_subject_domain_of_dialog_control/examples/message_processing_program.gwf).

Связанные SCs-файлы:

- классы запросов — `knowledge-base/extra/hunting/message_classes/concept_message_about_hunting_research.scs` и `concept_message_about_hunting_inference.scs`;
- узлы заметок — [concept_hunting_knowledge_notes.scs](knowledge-base/extra/hunting/concepts/concept_hunting_knowledge_notes.scs);
- отношения заметок — [knowledge_note_relations.scs](knowledge-base/extra/hunting/relations/knowledge_note_relations.scs).

Команда **«Найди общую информацию о [животном] и добавь её в базу знаний»** запускает поиск во внешнем источнике, сохраняет текст и источник, затем добавляет заметку в граф. Команда **«Выведи общую среду обитания боровых птиц»** запускает вывод по уже имеющимся связям членов группы со средой.

Внешняя заметка сохраняется как текст с источником. Вывод агента опирается на типизированные связи, уже представленные в графе; произвольный текст страницы автоматически не превращается в набор биологических отношений.

## Сквозной маршрут сообщения

```text
сообщение → Jev → JSON-классификация → NIKA и роли SC-графа
           → GWF-правило (ЛР3) или Python-действие (ЛР4)
           → чтение/запись графа → ответ в чате
```

Исходники SCs/GWF для лабораторных 1–3 и код работающей системы лежат в репозитории рядом, чтобы можно было перейти от демонстрационной копии к активному файлу.
