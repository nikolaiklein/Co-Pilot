# Co-Pilot — Документация проекта

> Персональный ИИ-ассистент «Правильный Помощник» — Telegram-бот, который интервьюирует, анализирует и помогает раскрыть потенциал пользователя.

---

## Оглавление

- [Обзор](#обзор)
- [Архитектура](#архитектура)
- [Схема обработки сообщения](#схема-обработки-сообщения)
- [AI-движок и модели](#ai-движок-и-модели)
- [Профилирование пользователя](#профилирование-пользователя)
- [База данных (Firestore)](#база-данных-firestore)
- [Telegram-бот: команды и обработчики](#telegram-бот-команды-и-обработчики)
- [Деплой и CI/CD](#деплой-и-cicd)
- [Конфигурация и секреты](#конфигурация-и-секреты)
- [Структура файлов](#структура-файлов)
- [Локальная разработка](#локальная-разработка)

---

## Обзор

**Co-Pilot** — это Telegram-бот на Python (FastAPI + python-telegram-bot), развёрнутый в Google Cloud Run. Бот выполняет три роли:

| Роль | Описание |
|------|----------|
| **Биограф** | Мягко интервьюирует, выявляет навыки, интересы и мечты |
| **Второй пилот** | Коуч, критик или исполнитель — адаптируется под ситуацию |
| **Аналитик** | Строит «Карту Личности», находит точки роста |

Бот автоматически определяет режим работы:
- **INTERVIEW** — профиль пуст → задаёт вопросы, собирает информацию
- **COACHING** — пользователь просит помощи → объясняет пошагово
- **EXECUTION** — конкретная задача → выполняет сразу

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│                        Пользователь Telegram                     │
│                  (текст / голос / фото / команды)                 │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  Telegram Bot API    │
                    │    (webhook POST)    │
                    └──────────┬──────────┘
                               │
                               ▼
              ┌────────────────────────────────────┐
              │         FastAPI (main.py)           │
              │                                    │
              │  GET  /            → Health check   │
              │  POST /webhook     → Обработка      │
              │  POST /cron/analyze → Анализ (cron) │
              │  POST /cron/analyze-all → Batch     │
              │  POST /cron/weekly-digest → Дайджест│
              └───┬────────┬────────┬────────┬─────┘
                  │        │        │        │
          ┌───────┘   ┌────┘   ┌────┘   ┌────┘
          ▼           ▼        ▼        ▼
    ┌──────────┐ ┌────────┐ ┌──────┐ ┌───────────┐
    │ Telegram │ │   AI   │ │  DB  │ │ Analyzer  │
    │   Bot    │ │ Engine │ │Svc   │ │ Service   │
    │ Handler  │ │        │ │      │ │           │
    └────┬─────┘ └───┬────┘ └──┬───┘ └─────┬─────┘
         │           │         │            │
         │      ┌────┴──────┐  │            │
         │      │ Провайдеры│  │            │
         │      │           │  │            │
         │      │ • Gemini  │  │            │
         │      │ • Claude  │  │            │
         │      │ • OpenAI  │  │            │
         │      │ • NVIDIA  │  │            │
         │      │   NIM     │  │            │
         │      └───────────┘  │            │
         │                     ▼            │
         │           ┌──────────────┐       │
         │           │   Firestore  │◄──────┘
         │           │              │
         │           │ users/{id}   │
         │           │ ├─ profile   │
         │           │ ├─ messages/ │
         │           │ └─ reports/  │
         │           └──────────────┘
         │
         ▼
┌──────────────────────────────────────────────────┐
│              Внешние AI-сервисы                    │
│                                                    │
│  Google Gemini API    │  Anthropic Claude API       │
│  OpenAI API (GPT)     │  NVIDIA NIM (7+ моделей)   │
└──────────────────────────────────────────────────┘
```

### Компоненты

| Компонент | Файл | Назначение |
|-----------|------|------------|
| **FastAPI** | `main.py` | Точка входа, webhook, cron-эндпоинты |
| **Telegram Bot** | `services/telegram_bot.py` | Обработчики команд и сообщений |
| **AI Engine** | `services/ai_engine.py` | Мульти-провайдерный ИИ-движок |
| **Database** | `services/db.py` | Firestore: пользователи, история, отчёты |
| **Analyzer** | `services/analyzer.py` | Фоновый анализ профиля через LLM |
| **Firebase Init** | `config/firebase_init.py` | Инициализация Firebase Admin SDK |

---

## Схема обработки сообщения

### Текстовое сообщение

```
Пользователь отправляет текст
         │
         ▼
┌─ handle_message() ──────────────────────────────────────────┐
│                                                              │
│  1. Проверка авторизации (ALLOWED_USERS)                     │
│         │                                                    │
│         ▼                                                    │
│  2. process_dialog_turn()                                    │
│     ┌────────────────────────────────────────────────────┐   │
│     │ a) get_or_create_user() → Firestore                │   │
│     │ b) save_message(role="user") → Firestore           │   │
│     │ c) send_chat_action("typing") → Telegram           │   │
│     │ d) get_last_messages(limit=20) → история           │   │
│     │ e) Читаем selected_model из профиля пользователя   │   │
│     │ f) ai_engine.generate_response(                    │   │
│     │      text, history, profile, provider, model       │   │
│     │    )                                               │   │
│     │    ┌──────────────────────────────────────┐        │   │
│     │    │ • build_system_prompt(profile, name)  │        │   │
│     │    │ • get_provider(provider, model)        │        │   │
│     │    │ • provider.generate(messages, prompt)  │        │   │
│     │    └──────────────────────────────────────┘        │   │
│     │ g) save_message(role="assistant") → Firestore      │   │
│     │ h) Каждые 3 сообщения → analyzer.analyze() (фон)   │   │
│     └────────────────────────────────────────────────────┘   │
│         │                                                    │
│         ▼                                                    │
│  3. markdown_to_telegram_html() → форматирование             │
│  4. split_message() → разбивка если > 4096 символов          │
│  5. send_message(parse_mode=HTML) → Telegram                 │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Голосовое сообщение

```
Пользователь отправляет голосовое
         │
         ▼
┌─ handle_voice() ────────────────────────┐
│  1. send_chat_action("upload_voice")     │
│  2. get_file() → скачать OGG в память    │
│  3. ai_engine.transcribe_audio(bytes)    │
│     └─ GeminiProvider: мультимодальный   │
│     └─ OpenAIProvider: Whisper API       │
│  4. text = "[Голосовое]: {транскрипция}" │
│  5. process_dialog_turn(text)            │
│     └─ (далее как текстовое)             │
└──────────────────────────────────────────┘
```

### Фото

```
Пользователь отправляет фото (+ caption)
         │
         ▼
┌─ handle_photo() ───────────────────────────────┐
│  1. Берём максимальный размер фото              │
│  2. download_as_bytearray()                     │
│  3. ai_engine.analyze_image(bytes, caption)     │
│     └─ provider.analyze_image() (Gemini/Claude) │
│  4. Отправляем ответ пользователю               │
└─────────────────────────────────────────────────┘
```

---

## AI-движок и модели

### Абстракция провайдеров

```
              BaseProvider (ABC)
              ┌─────────────────┐
              │ generate()      │
              │ analyze()       │
              │ transcribe()    │
              │ analyze_image() │
              └────────┬────────┘
                       │
         ┌─────────────┼──────────────┬──────────────────┐
         ▼             ▼              ▼                  ▼
   GeminiProvider ClaudeProvider OpenAIProvider  OpenAICompatibleProvider
   (google-genai) (anthropic)   (openai)        (openai + custom base_url)
                                                  └─ NVIDIA NIM
```

### Доступные модели (15 штук, все протестированы)

#### Gemini (8 моделей)

| Короткое имя | Полное имя | Описание |
|--------------|-----------|----------|
| `gemini-3-flash` | gemini-3-flash-preview | **По умолчанию.** Новейший, быстрый |
| `gemini-3-pro` | gemini-3-pro-preview | Максимальное качество (3-е поколение) |
| `gemini-3.1-pro` | gemini-3.1-pro-preview | Самый новый Pro |
| `gemini-3.1-flash-lite` | gemini-3.1-flash-lite-preview | Ультра-лёгкий |
| `gemini-2.5-flash` | gemini-2.5-flash | Баланс скорости и качества |
| `gemini-2.5-pro` | gemini-2.5-pro | Глубокий анализ |
| `gemini-2.5-flash-lite` | gemini-2.5-flash-lite | Быстрый и экономный |
| `gemini-2.0-flash` | gemini-2.0-flash | Стабильный, проверенный |

#### NVIDIA NIM (7 моделей)

| Короткое имя | Полное имя | Описание |
|--------------|-----------|----------|
| `llama-4-maverick` | meta/llama-4-maverick-17b-128e-instruct | Meta Llama 4 |
| `kimi-k2` | moonshotai/kimi-k2-instruct | Moonshot Kimi K2 |
| `kimi-k2.5` | moonshotai/kimi-k2.5 | Kimi K2.5 (улучшенный) |
| `qwen3.5-397b` | qwen/qwen3.5-397b-a17b | Alibaba Qwen 3.5 (397B) |
| `nemotron-ultra` | nvidia/llama-3.1-nemotron-ultra-253b-v1 | NVIDIA Nemotron Ultra (253B) |
| `mistral-large-3` | mistralai/mistral-large-3-675b-instruct-2512 | Mistral Large 3 (675B) |
| `minimax-m2.5` | minimaxai/minimax-m2.5 | MiniMax M2.5 |

### Переключение модели

Каждый пользователь выбирает свою модель через `/model`. Выбор сохраняется в Firestore (`selected_model`) и используется при каждом запросе.

```
/model                    → показать текущую модель и список всех
/model kimi-k2            → переключить по короткому имени
/model gemini/gemini-3-pro-preview → переключить по полному имени
```

### Системный промпт

Промпт генерируется динамически в `build_system_prompt()`:

```
┌──────────────────────────────────────────────┐
│ Базовый промпт                               │
│ • Роль: персональный ассистент               │
│ • Миссия: раскрыть потенциал                 │
│ • Роли: Биограф / Пилот / Аналитик          │
│ • Режимы: INTERVIEW / COACHING / EXECUTION   │
│ • Правила общения                            │
│ • Доступные команды                          │
├──────────────────────────────────────────────┤
│ + Подсказка режима (если профиль пуст)       │
│   "ТЕКУЩИЙ РЕЖИМ: INTERVIEW"                 │
├──────────────────────────────────────────────┤
│ + Досье пользователя (если есть)             │
│   📌 Портрет: ...                            │
│   🎯 Интересы: AI, Python, ...               │
│   🛠 Навыки: ...                              │
│   ⚠️ Боли: ...                                │
│   💭 Мечты: ...                               │
└──────────────────────────────────────────────┘
```

---

## Профилирование пользователя

### Как работает анализ

```
Каждые 3 сообщения пользователя
         │
         ▼
┌─ AnalyzerService.analyze_user_profile() ──────────┐
│                                                     │
│  1. Получить последние 50 сообщений из Firestore    │
│  2. Сформировать лог диалога:                       │
│     "Пользователь: ..."                             │
│     "Ассистент: ..."                                │
│  3. Загрузить текущий профиль (если есть)            │
│  4. Отправить в LLM с промптом:                     │
│     "Проанализируй диалог, найди навыки,            │
│      интересы, боли, мечты. Верни JSON."            │
│  5. Распарсить JSON-ответ                           │
│  6. Обновить users/{id}/profile_summary             │
│  7. Сохранить снимок в users/{id}/reports/          │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### Структура профиля

```json
{
  "new_skills": ["Python", "Анализ данных", "Prompt Engineering"],
  "interests": ["Искусственный интеллект", "Автоматизация", "Инвестиции"],
  "pain_points": ["Нехватка времени", "Прокрастинация"],
  "dreams": ["Создать свой AI-продукт", "Финансовая свобода"],
  "summary": "Технический специалист 54 лет с опытом в IT и интересом к AI..."
}
```

### Триггеры анализа

| Триггер | Когда | Метод |
|---------|-------|-------|
| После диалога | Каждые 3 сообщения пользователя | `asyncio.create_task()` (фоново) |
| Cron (один) | `POST /cron/analyze?user_id=123` | Cloud Scheduler |
| Cron (все) | `POST /cron/analyze-all` | Cloud Scheduler (ежедневно) |

---

## База данных (Firestore)

### Структура

```
Firestore (Native Mode)
│
└── users/                          ← Коллекция
    └── {user_id}/                  ← Документ (Telegram User ID)
        │
        ├── id: number              ← Telegram ID
        ├── username: string        ← @username
        ├── first_name: string
        ├── last_name: string
        ├── language_code: string   ← "ru", "en"
        ├── is_bot: boolean
        ├── created_at: timestamp   ← Дата регистрации
        ├── bot_nickname: string    ← Имя, которое дал боту (/name)
        ├── selected_model: string  ← "nvidia/moonshotai/kimi-k2-instruct"
        │
        ├── profile_summary: map    ← Результат анализа
        │   ├── new_skills: array
        │   ├── interests: array
        │   ├── pain_points: array
        │   ├── dreams: array
        │   └── summary: string
        │
        ├── messages/               ← Подколлекция: история диалога
        │   └── {auto_id}/
        │       ├── role: "user" | "assistant"
        │       ├── content: string
        │       └── timestamp: timestamp
        │
        └── reports/                ← Подколлекция: снимки анализа
            └── {auto_id}/
                ├── new_skills: array
                ├── interests: array
                ├── pain_points: array
                ├── dreams: array
                ├── summary: string
                └── timestamp: timestamp
```

### Операции

| Метод | Описание |
|-------|----------|
| `get_or_create_user()` | Создать пользователя при первом обращении |
| `get_user()` | Получить данные пользователя |
| `update_user()` | Обновить поля (профиль, имя бота, модель) |
| `save_message()` | Сохранить сообщение в историю |
| `get_last_messages(limit)` | Последние N сообщений (хронологически) |
| `clear_messages()` | Очистить всю историю (/clear) |
| `save_report()` | Сохранить снимок анализа |
| `get_all_user_ids()` | Все ID для batch-обработки |

---

## Telegram-бот: команды и обработчики

### Команды

| Команда | Описание |
|---------|----------|
| `/start` | Онбординг: интервью / свободный диалог (новый) или меню (возвращающийся) |
| `/help` | Список всех команд |
| `/model` | Показать/переключить AI-модель |
| `/myprofile` | Показать накопленное досье (навыки, интересы, мечты) |
| `/name Макс` | Дать боту персональное имя |
| `/correct ...` | Исправить ошибку в профиле через LLM |
| `/clear` | Очистить историю диалога |

### Обработчики

| Тип | Фильтр | Обработчик |
|-----|--------|------------|
| Команда | `/start` | `handle_start()` |
| Команда | `/help` | `handle_help()` |
| Команда | `/model` | `handle_model()` |
| Команда | `/myprofile` | `handle_myprofile()` |
| Команда | `/name` | `handle_name()` |
| Команда | `/correct` | `handle_correct()` |
| Команда | `/clear` | `handle_clear()` |
| Текст | `TEXT & ~COMMAND` | `handle_message()` |
| Голос | `VOICE` | `handle_voice()` |
| Фото | `PHOTO` | `handle_photo()` |
| Кнопки | `CallbackQuery` | `handle_callback()` |

### Inline-кнопки

При `/start` для нового пользователя:
```
┌─────────────┬─────────────────────┐
│ 🎤 Интервью │ 💬 Свободный диалог │
├─────────────┴─────────────────────┤
│      ❓ Что ты умеешь?             │
└───────────────────────────────────┘
```

При `/start` для возвращающегося:
```
┌────────────────┬──────────────┐
│ 📋 Мой профиль │ 💬 Продолжить│
├────────────────┼──────────────┤
│ ❓ Помощь      │ ⚙️ Дать имя  │
└────────────────┴──────────────┘
```

---

## Деплой и CI/CD

### Инфраструктура

```
┌───────────────────────────────────────────────┐
│              GitHub (nikolaiklein/Co-Pilot)     │
│                                               │
│  push to "test" branch                        │
│         │                                     │
│         ▼                                     │
│  GitHub Actions (.github/workflows/deploy.yml)│
│  ┌────────────────────────────────────────┐   │
│  │ 1. Checkout code                       │   │
│  │ 2. Auth GCP (service account key)      │   │
│  │ 3. Build Docker image                  │   │
│  │ 4. Push to Artifact Registry           │   │
│  │ 5. Deploy to Cloud Run                 │   │
│  └────────────────────────────────────────┘   │
└───────────────────────────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────┐
│           Google Cloud Platform                │
│                                               │
│  Project: co-pilot-bot                        │
│  Region: europe-west1                         │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │ Artifact Registry                       │  │
│  │ co-pilot/ → Docker images               │  │
│  └─────────────────────────────────────────┘  │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │ Cloud Run: co-pilot                     │  │
│  │ • URL: co-pilot-dcvd3baddq-ew.a.run.app│  │
│  │ • Memory: 512Mi                         │  │
│  │ • Instances: 0–3 (autoscale)            │  │
│  │ • Port: 8080                            │  │
│  │ • Public (allow-unauthenticated)        │  │
│  └─────────────────────────────────────────┘  │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │ Secret Manager (8 секретов)             │  │
│  │ • TELEGRAM_BOT_TOKEN                    │  │
│  │ • GEMINI_API_KEY                        │  │
│  │ • NVIDIA_API_KEY                        │  │
│  │ • ANTHROPIC_API_KEY (placeholder)       │  │
│  │ • OPENAI_API_KEY (placeholder)          │  │
│  │ • MINIMAX_API_KEY                       │  │
│  │ • ALLOWED_USERS                         │  │
│  │ • DEFAULT_MODEL                         │  │
│  └─────────────────────────────────────────┘  │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │ Firestore (Native Mode)                 │  │
│  │ └── users/ collection                   │  │
│  └─────────────────────────────────────────┘  │
└───────────────────────────────────────────────┘
```

### Как деплоить

```bash
# Автоматический деплой — просто пуш в test:
git push origin test

# GitHub Actions выполнит:
# 1. docker build → europe-west1-docker.pkg.dev/co-pilot-bot/co-pilot/co-pilot:{SHA}
# 2. docker push → Artifact Registry
# 3. gcloud run deploy → Cloud Run
```

### Webhook Telegram

```bash
# Установка (уже настроен):
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://co-pilot-dcvd3baddq-ew.a.run.app/webhook"

# Проверка:
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

---

## Конфигурация и секреты

### Переменные окружения

| Переменная | Обязательная | Описание |
|-----------|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Токен от @BotFather |
| `GEMINI_API_KEY` | ✅ | Ключ Google AI Studio |
| `NVIDIA_API_KEY` | ❌ | Ключ NVIDIA NIM |
| `ANTHROPIC_API_KEY` | ❌ | Ключ Anthropic |
| `OPENAI_API_KEY` | ❌ | Ключ OpenAI |
| `ALLOWED_USERS` | ❌ | ID пользователей через запятую (пусто = все) |
| `DEFAULT_MODEL` | ❌ | Модель по умолчанию (`gemini/gemini-3-flash-preview`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | ❌ | Путь к JSON-ключу (только локально) |
| `PORT` | ❌ | Порт сервера (по умолчанию `8080`) |
| `LOG_LEVEL` | ❌ | Уровень логирования (`INFO`) |

### GitHub Secrets

| Секрет | Описание |
|--------|----------|
| `GCP_PROJECT_ID` | `co-pilot-bot` |
| `GCP_SA_KEY` | JSON-ключ сервис-аккаунта `github-deployer@co-pilot-bot` |

---

## Структура файлов

```
Co-Pilot/
├── .github/
│   └── workflows/
│       └── deploy.yml          ← CI/CD: push test → Cloud Run
├── config/
│   ├── __init__.py
│   └── firebase_init.py        ← Инициализация Firebase Admin SDK
├── services/
│   ├── __init__.py
│   ├── ai_engine.py            ← Мульти-провайдерный AI-движок (592 строки)
│   │   ├── BaseProvider         (абстрактный класс)
│   │   ├── GeminiProvider       (Google Gemini API)
│   │   ├── ClaudeProvider       (Anthropic Claude)
│   │   ├── OpenAIProvider       (OpenAI GPT + Whisper)
│   │   ├── OpenAICompatibleProvider (NVIDIA NIM)
│   │   └── AIEngine             (фабрика + оркестратор)
│   ├── telegram_bot.py         ← Обработчики Telegram (766 строк)
│   │   ├── create_bot_app()     (фабрика приложения)
│   │   ├── process_dialog_turn()(основной пайплайн)
│   │   └── handle_*()          (обработчики команд)
│   ├── db.py                   ← Firestore CRUD (253 строки)
│   │   └── DatabaseService      (async Firestore wrapper)
│   └── analyzer.py             ← Анализ профиля через LLM (105 строк)
│       └── AnalyzerService      (фоновый анализ)
├── .env.example                ← Шаблон переменных окружения
├── .gcloudignore               ← Исключения для Cloud deploy
├── .gitignore
├── CLAUDE.md                   ← Инструкции для Claude Code
├── Dockerfile                  ← python:3.11-slim + uvicorn
├── firebase.json               ← Firebase CLI конфиг
├── firestore.indexes.json      ← Индексы Firestore
├── firestore.rules             ← Правила безопасности Firestore
├── main.py                     ← Точка входа FastAPI (271 строка)
├── requirements.txt            ← Python-зависимости (10 пакетов)
├── sync_instructions.md        ← Инструкция синхронизации
├── test_ai.py                  ← Тесты AI Engine
└── verify_creds.py             ← Проверка GCP credentials
```

---

## Локальная разработка

```bash
# 1. Клонировать
git clone https://github.com/nikolaiklein/Co-Pilot.git
cd Co-Pilot

# 2. Создать .env
cp .env.example .env
# Заполнить: TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, GOOGLE_APPLICATION_CREDENTIALS

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Запустить
uvicorn main:app --reload --port 8080

# 5. Настроить webhook (через ngrok или аналог)
ngrok http 8080
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<NGROK_URL>/webhook"
```

### Docker (локально)

```bash
docker build -t co-pilot .
docker run -p 8080:8080 --env-file .env co-pilot
```

---

## Стек технологий

| Технология | Версия | Назначение |
|-----------|--------|-----------|
| Python | 3.11 | Язык |
| FastAPI | 0.115 | Async web framework |
| Uvicorn | 0.32 | ASGI-сервер |
| python-telegram-bot | 21.7 | Telegram API (async v20+) |
| Firebase Admin | 6.6 | Firebase SDK |
| google-cloud-firestore | 2.19 | Firestore async client |
| google-genai | 1.0 | Google Gemini API |
| anthropic | 0.40+ | Claude API |
| openai | 1.50+ | OpenAI / NVIDIA NIM |
| Google Cloud Run | — | Serverless hosting |
| GitHub Actions | — | CI/CD pipeline |

---

*Бот: [@SergeiVladimirovich_bot](https://t.me/SergeiVladimirovich_bot) • Репозиторий: [nikolaiklein/Co-Pilot](https://github.com/nikolaiklein/Co-Pilot)*
