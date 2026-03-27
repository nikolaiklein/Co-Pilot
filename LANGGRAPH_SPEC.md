# LANGGRAPH_SPEC.md — Графовая маршрутизация Co-Pilot

## Проблема

Текущая маршрутизация режимов (INTERVIEW / COACHING / EXECUTION) реализована как таблица в системном промпте (`prompt_builder.py:63-68`). LLM сам угадывает режим — это приводит к непредсказуемому переключению и смешиванию стилей. Анализатор профиля (`analyzer.py`) срабатывает каждые 3 сообщения вслепую, даже если пользователь просто задал вопрос про погоду.

**Цель**: Заменить неявную маршрутизацию на явную графовую архитектуру LangGraph с выделенными узлами для каждого режима и умным триггером анализа профиля.

## Scope

**В scope:**
- State (общее состояние графа)
- 5 узлов: router, interview, coaching, execution, analyzer
- Conditional edges от router
- Рефакторинг `dialog_pipeline.py` → вызов графа
- Умный analyzer (по содержанию, не по счётчику)

**Вне scope (отдельные спеки):**
- Tool calling в execution_node (будущее)
- Миграция Mem0 / Qdrant
- Изменение tag-парсинга ([SWITCH_MODEL], [VAULT_SAVE])
- Изменение model rotation / fallback
- Рефакторинг telegram_bot.py (отдельная задача из ROADMAP)

---

## 1. State — общее состояние графа

Данные, которые циркулируют между узлами за один диалоговый ход:

| Поле | Тип | Источник | Описание |
|------|-----|----------|----------|
| `user_id` | `int` | input | Telegram user ID |
| `user_name` | `str` | input | Display name |
| `user_message` | `str` | input | Текущее сообщение пользователя |
| `messages` | `list[dict]` | input (Firestore) | История — последние 20 сообщений |
| `user_profile` | `dict` | input (Firestore) | Профиль: profile_summary, bot_nickname, selected_model |
| `memory_context` | `str` | input (Mem0) | Контекст из долговременной памяти |
| `model_context` | `str \| None` | input | Контекст доступных моделей (если model-intent) |
| `selected_provider` | `str` | input | Провайдер LLM (gemini, anthropic, nvidia...) |
| `selected_model` | `str` | input | Конкретная модель |
| `intent` | `str` | router_node | Классифицированный интент: `interview`, `coaching`, `execution`, `chit_chat` |
| `response` | `str` | mode node | Сгенерированный ответ LLM |
| `actual_provider` | `str` | mode node | Фактический провайдер (после fallback) |
| `actual_model` | `str` | mode node | Фактическая модель (после fallback) |
| `needs_profile_update` | `bool` | analyzer_node | Флаг: нужно ли обновить профиль |

**Input-поля** заполняются ДО запуска графа (в `dialog_pipeline.py`). Узлы пишут только в свои output-поля.

---

## 2. Узлы (Nodes)

### 2.1 router_node

**Вход**: `user_message`, `user_profile`, `messages` (последние 3-5 для контекста)

**Выход**: `intent`

**Поведение**:
- Использует быстрый/дешёвый LLM-вызов со structured output (JSON: `{"intent": "..."}`)
- Классификация по 4 категориям: `interview`, `coaching`, `execution`, `chit_chat`
- Если `user_profile.profile_summary` пуст или скуден → сильный bias к `interview`
- Если пользователь делится личной информацией (навыки, мечты, интересы) → `interview`
- Если вопрос "как сделать?", "научи", "объясни" → `coaching`
- Если прямая задача "напиши", "сделай", "создай" → `execution`
- Если ни одно из вышеперечисленного → `chit_chat`
- При ошибке LLM → fallback на `execution` (наиболее общий режим)

**Задержка**: Не более 2 секунд. Использует самую быструю доступную модель (Gemini Flash).

### 2.2 interview_node

**Вход**: полный State

**Выход**: `response`, `actual_provider`, `actual_model`

**Поведение**:
- Специализированный системный промпт: роль Биографа
- Задаёт ровно 1 уточняющий вопрос за раз
- Мягко вытягивает информацию о навыках, интересах, мечтах, болевых точках
- Не перегружает пользователя множеством вопросов
- Использует профиль и memory_context чтобы не переспрашивать известное
- Генерация через `generate_with_fallback` (сохраняет текущую ротацию)

### 2.3 coaching_node

**Вход**: полный State

**Выход**: `response`, `actual_provider`, `actual_model`

**Поведение**:
- Специализированный системный промпт: роль Второго Пилота / Коуча
- Объясняет пошагово, показывает на примерах
- Стиль "давай сделаем это вместе"
- Предлагает автоматизацию через ИИ, если уместно
- Генерация через `generate_with_fallback`

### 2.4 execution_node

**Вход**: полный State

**Выход**: `response`, `actual_provider`, `actual_model`

**Поведение**:
- Специализированный системный промпт: роль Исполнителя
- Выполняет задачу с минимальными объяснениями
- Обрабатывает также `chit_chat` — обычный разговор без специального режима
- В будущем: сюда добавятся Tools/инструменты (вне scope этой спеки)
- Генерация через `generate_with_fallback`

### 2.5 analyzer_node

**Вход**: `user_message`, `response`, `user_profile`, `messages`

**Выход**: `needs_profile_update`

**Поведение**:
- Анализирует `user_message` и `response` на наличие новой личной информации
- Использует быстрый LLM-вызов со structured output: `{"has_new_info": true/false}`
- Если `true` → запускает `AnalyzerService.analyze_user_profile()` через fire_and_forget
- Если `false` → ничего не делает
- Заменяет тупой счётчик "каждые 3 сообщения" на контент-анализ
- При ошибке LLM → НЕ блокирует ответ пользователю, логирует и продолжает

---

## 3. Рёбра (Edges)

```
START → router_node

router_node ──[intent == "interview"]──→ interview_node
router_node ──[intent == "coaching"]───→ coaching_node
router_node ──[intent == "execution"]──→ execution_node
router_node ──[intent == "chit_chat"]──→ execution_node

interview_node ──→ analyzer_node
coaching_node  ──→ analyzer_node
execution_node ──→ analyzer_node

analyzer_node ──→ END
```

**Conditional edge** от `router_node`: решение на основе `state["intent"]`.

**Все остальные рёбра** — прямые (unconditional).

---

## 4. Что остаётся ВНЕ графа

Следующие операции выполняются в `dialog_pipeline.py` до/после вызова графа:

| Фаза | Операция | Почему вне графа |
|------|----------|-----------------|
| **Pre-graph** | Bulk-mode check | Полный bypass, граф не нужен |
| **Pre-graph** | Vault quicksave | Regex-match, early return |
| **Pre-graph** | get_or_create_user, save_message | DB-операции для подготовки State input |
| **Pre-graph** | Load history, resolve model, get memory_context | Заполнение State input |
| **Pre-graph** | Detect model intent (keyword-based) | Ортогонально режиму, модифицирует промпт |
| **Post-graph** | Tag parsing ([SWITCH_MODEL], [VAULT_SAVE]) | Механическая обработка, не LLM-решение |
| **Post-graph** | Tag action execution | DB-операции по результатам тегов |
| **Post-graph** | Memory storage (Mem0 queue) | Async, fire-and-forget |
| **Post-graph** | Formatting (Markdown → HTML) + send | Презентационный слой |

---

## 5. Job Stories и Acceptance Criteria

### Story 1: Маршрутизация по интенту

**When** пользователь отправляет сообщение, **I want** систему классифицировать интент и направить к специализированному агенту, **so I can** получать ответы в наиболее подходящем стиле.

```gherkin
Scenario: Новый пользователь получает interview-режим
  Given пользователь с пустым profile_summary
  When он отправляет любое сообщение
  Then router классифицирует intent как "interview"
  And ответ содержит ровно 1 уточняющий вопрос

Scenario: Вопрос "как сделать" → coaching
  Given пользователь с заполненным профилем
  When он спрашивает "как сделать автоматизацию в n8n?"
  Then router классифицирует intent как "coaching"
  And ответ содержит пошаговую структуру

Scenario: Прямая задача → execution
  Given пользователь с заполненным профилем
  When он говорит "напиши промпт для анализа данных"
  Then router классифицирует intent как "execution"
  And ответ выполняет задачу без лишних объяснений

Scenario: Router fallback при ошибке
  Given LLM router недоступен или вернул невалидный JSON
  When обрабатывается любое сообщение
  Then intent устанавливается в "execution"
  And пользователь получает ответ без задержки
```

### Story 2: Умный анализ профиля

**When** в разговоре появляется новая личная информация, **I want** анализатор обнаружить это и обновить профиль, **so I can** не тратить ресурсы на пустой анализ и не терять новые данные.

```gherkin
Scenario: Пользователь делится новым навыком
  Given пользователь говорит "я 5 лет пишу на Python"
  When analyzer_node обрабатывает ход
  Then needs_profile_update == true
  And AnalyzerService запускается асинхронно

Scenario: Фактический вопрос без личной информации
  Given пользователь спрашивает "какая погода в Москве?"
  When analyzer_node обрабатывает ход
  Then needs_profile_update == false
  And AnalyzerService НЕ запускается

Scenario: Ошибка analyzer не блокирует ответ
  Given analyzer_node выбрасывает исключение
  When пользователь ждёт ответ
  Then ответ доставляется без задержки
  And ошибка логируется
```

### Story 3: Прозрачная миграция

**When** система переходит на LangGraph, **I want** все существующие функции работать идентично, **so I can** не замечать миграцию.

```gherkin
Scenario: Теги работают после миграции
  Given новая графовая система
  When LLM генерирует ответ с тегом [SWITCH_MODEL: claude]
  Then тег парсится и модель переключается идентично текущему поведению

Scenario: Форматирование не ломается
  Given ответ содержит markdown-разметку
  When ответ проходит через pipeline
  Then Telegram получает корректный HTML

Scenario: Bulk-mode не затрагивается
  Given пользователь в bulk-режиме
  When он отправляет сообщение
  Then граф НЕ вызывается, сообщение уходит напрямую в память
```

---

## 6. План рефакторинга

### 6.1 Зависимости (requirements.txt)

**Добавить:**
- `langgraph` — графовый фреймворк для оркестрации
- `langchain-core` — базовые абстракции (требуется langgraph)

**Не менять**: все существующие зависимости остаются (google-genai, python-telegram-bot, firebase-admin, mem0ai, anthropic, openai).

### 6.2 Новые файлы

| Файл | Содержимое |
|------|-----------|
| `services/graph/__init__.py` | Экспорт графа |
| `services/graph/state.py` | TypedDict с полями из раздела 1 |
| `services/graph/nodes.py` | 5 async-функций (router, interview, coaching, execution, analyzer) |
| `services/graph/edges.py` | Функция conditional routing по intent |
| `services/graph/builder.py` | Сборка StateGraph, компиляция в runnable |
| `services/graph/prompts.py` | Режим-специфичные промпты (вынесены из prompt_builder.py) |

### 6.3 Изменяемые файлы

**`services/dialog_pipeline.py`** — главное изменение:
- `process_turn()` заполняет State input → вызывает скомпилированный граф → читает output
- Убирается: прямой вызов `build_system_prompt` + `generate_with_fallback` (это теперь внутри узлов)
- Остаётся: pre-graph (bulk, vault, DB, memory) и post-graph (tags, formatting, send)

**`services/prompt_builder.py`**:
- Общие части (vault rules, formatting rules, commands, honesty rules) → переиспользуемый base prompt
- Режим-специфичные части → `services/graph/prompts.py`
- `build_system_prompt()` сохраняется для обратной совместимости, но внутри делегирует в graph/prompts

**`services/analyzer.py`**:
- `analyze_user_profile()` — без изменений (вызывается из analyzer_node через fire_and_forget)
- Известный баг с перезаписью profile_summary — фиксится отдельно (вне scope)

### 6.4 Неизменяемые файлы

`ai_engine.py`, `db.py`, `memory.py`, `response_tags.py`, `model_rotation.py`, `formatting.py`, `state.py`, `telegram_bot.py`, все handlers/, `main.py`.

---

## 7. Boundaries

**✅ Always:**
- Полная асинхронность (async/await) во всех узлах и edges
- Генерация через `generate_with_fallback` (сохраняем ротацию и timeout)
- Firestore для всех persistent данных (никаких новых хранилищ)
- Markdown → HTML конвертация через `formatting.py` (не менять)
- Логирование intent в pipeline-level log

**⚠️ Ask first:**
- Изменение структуры State после утверждения спеки
- Добавление новых LLM-вызовов помимо router и analyzer (бюджет токенов)
- Изменение формата profile_summary в Firestore

**🚫 Never:**
- Не менять webhook flow и handler registration
- Не менять формат сообщений в Firestore (messages/{auto_id})
- Не добавлять синхронные блокирующие вызовы
- Не хардкодить имена моделей в узлах — всё через AIEngine
- Не ломать tag-парсинг ([SWITCH_MODEL], [VAULT_SAVE])
- Не удалять существующие команды бота

---

## 8. Constraints

- **Latency budget**: router_node + mode_node + analyzer_node ≤ 35 секунд суммарно (текущий таймаут — 30с на генерацию). Router и analyzer используют быстрые модели и не должны добавлять более 3-4 секунд overhead.
- **Token budget**: router_node и analyzer_node — минимальные промпты (<500 токенов input каждый). Основной бюджет — на mode_node.
- **Совместимость**: Python 3.11+, Cloud Run, Docker.
- **Тестируемость**: каждый узел — чистая async-функция, принимающая State и возвращающая partial State. Можно тестировать изолированно.

---

## Самопроверка

После реализации сверить каждый acceptance criterion из раздела 5 и подтвердить:
- [ ] Router корректно классифицирует 4 типа интента
- [ ] Router fallback работает при ошибке LLM
- [ ] Каждый mode_node генерирует ответ в своём стиле
- [ ] Analyzer запускается только при наличии новой личной информации
- [ ] Analyzer не блокирует доставку ответа
- [ ] Теги, форматирование, bulk-mode работают идентично
- [ ] Общий latency не деградировал более чем на 4 секунды
