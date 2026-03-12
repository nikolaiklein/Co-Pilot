# Co-Pilot — Digital Heritage System

Telegram-бот "Ко-пилот" (@SergeiVladimirovich_bot): эмпатичный ассистент-биограф и коуч. Интервьюирует, выявляет навыки, структурирует опыт, помогает осваивать ИИ-инструменты.

## Stack

- **Python 3.11**, FastAPI 0.115, uvicorn 0.32
- **Telegram**: `python-telegram-bot` v21.7 (async, webhook mode)
- **Database**: Google Cloud Firestore (Native mode, europe-west1)
- **AI**: Multi-provider — Gemini, Claude, OpenAI, NVIDIA NIM (via `AIProvider` abstraction)
- **Memory**: RAG — Gemini Embedding API (768-dim) + Firestore Vector Search
- **Deploy**: Google Cloud Run (europe-west1), Docker, GitHub Actions auto-deploy from `test` branch
- **Auth**: Google Application Default Credentials (ADC) for Firebase/GCP

## Commands

```bash
# Local development
pip install -r requirements.txt
cp .env.example .env          # fill in your keys
uvicorn main:app --reload     # starts on :8080

# Docker
docker build -t co-pilot .
docker run -p 8080:8080 --env-file .env co-pilot

# Deploy (auto via GitHub Actions on push to test branch)
git push origin test

# Set Telegram webhook after deploy
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<CLOUD_RUN_URL>/webhook"
```

## Architecture

```
main.py                  — FastAPI app, webhook endpoint, cron endpoints
config/
  firebase_init.py       — Firebase Admin SDK initialization (ADC)
services/
  ai_engine.py           — AIProvider abstraction (Gemini/Claude/OpenAI/NVIDIA)
  telegram_bot.py        — Bot handlers: text, voice, photo, files, commands (1098 lines — needs refactoring)
  db.py                  — Firestore: users, messages, reports
  analyzer.py            — Background profile analysis via LLM (every 3 messages)
  memory.py              — Long-term memory with RAG (Gemini Embedding + Firestore Vector Search)
```

### Request Flow

```
Telegram webhook POST /webhook
  → python-telegram-bot processes Update
  → handle_message / handle_voice / handle_photo / handle_document
  → db.get_or_create_user + db.save_message
  → memory.search_memory (if trigger detected)
  → ai_engine.generate_response (with history + memory context)
  → db.save_message (assistant reply)
  → memory.store_memory (async queue)
  → analyzer.analyze (every 3rd message)
  → send response to Telegram
```

### Firestore Structure

```
users/{user_id}
  ├── id, username, first_name, created_at
  ├── bot_nickname, selected_model
  ├── profile_summary (map)
  │   ├── new_skills[], interests[], pain_points[], dreams[]
  │   └── summary (string)
  ├── messages/{auto_id}    — role, content, timestamp
  ├── reports/{auto_id}     — analysis_result, timestamp
  └── memory/{auto_id}      — content, role, embedding(Vector[768]), timestamp, summary_block
```

### Cron Endpoints

```
POST /cron/analyze         — analyze single user profile
POST /cron/analyze-all     — batch analyze all users
POST /cron/weekly-digest   — send weekly summaries
POST /cron/summarize-memory — compress old messages into memory
GET  /debug/memory/{uid}   — inspect user's memory (debug)
```

## AI Provider Architecture

### Provider Hierarchy
```
BaseProvider (ABC)
  ├── GeminiProvider      — google-genai SDK, default
  ├── ClaudeProvider      — anthropic SDK
  ├── OpenAIProvider      — openai SDK
  └── OpenAICompatibleProvider — any OpenAI-compatible API (NVIDIA NIM)
```

### Available Models (tested, working)
```python
# Gemini (провайдер: "gemini")
"gemini-3-flash"       → "gemini-3-flash-preview"        # default
"gemini-3-pro"         → "gemini-3-pro-preview"
"gemini-3.1-pro"       → "gemini-3.1-pro-preview"
"gemini-3.1-flash-lite"→ "gemini-3.1-flash-lite-preview"
"gemini-2.5-flash"     → "gemini-2.5-flash"
"gemini-2.5-pro"       → "gemini-2.5-pro"
"gemini-2.5-flash-lite"→ "gemini-2.5-flash-lite"
"gemini-2.0-flash"     → "gemini-2.0-flash"              # used for transcription

# NVIDIA NIM (провайдер: "nvidia")
"llama-4-maverick"     → "meta/llama-4-maverick-17b-128e-instruct"
"kimi-k2"              → "moonshotai/kimi-k2-instruct"
"kimi-k2.5"            → "moonshotai/kimi-k2.5"
"deepseek-v3.2"        → "deepseek-ai/deepseek-v3.2"
"qwen3.5-397b"         → "qwen/qwen3.5-397b-a17b"
"nemotron-ultra"       → "nvidia/llama-3.1-nemotron-ultra-253b-v1"
"mistral-large-3"      → "mistralai/mistral-large-3-675b-instruct-2512"
"minimax-m2.5"         → "minimaxai/minimax-m2.5"

# Anthropic: "claude-sonnet-4-20250514"
# OpenAI: "gpt-4o"
```

### Model String Parsing
```
"gemini/gemini-2.5-flash" → provider="gemini", model="gemini-2.5-flash"
"kimi-k2"                 → provider="nvidia", model="moonshotai/kimi-k2-instruct"
"gemini-3-flash"          → provider="gemini", model="gemini-3-flash-preview"
```

## Telegram Bot Commands (13 total)

| Command | Description | Access |
|---------|-------------|--------|
| `/start` | Онбординг / меню | all |
| `/help` | Список команд | all |
| `/model` | Показать/сменить AI-модель | all |
| `/myprofile` | Показать досье пользователя | all |
| `/name` | Дать боту имя | all |
| `/correct` | Исправить ошибку в профиле через LLM | all |
| `/clear` | Очистить историю диалога | all |
| `/memory` | Статистика RAG-памяти | all |
| `/bulk` | Режим массовой загрузки данных | all |
| `/api` | Генерация API-ключа (placeholder) | all |
| `/admin` | Админ-команды | owner only (292628110) |

## Environment Variables

See `.env.example` for full list. Required:
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `GEMINI_API_KEY` — Google AI Studio key
- `ALLOWED_USERS` — comma-separated Telegram user IDs (empty = allow all)
- `DEFAULT_MODEL` — format: "provider/model" (default: gemini/gemini-2.5-flash)

Optional:
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `NVIDIA_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS` — local dev only
- `PORT` (default: 8080), `LOG_LEVEL` (default: INFO)

## Code Style

- Python 3.11+, async/await everywhere
- Type hints on all functions
- logging module (not print)
- Docstrings in Russian (project language)
- Keep services loosely coupled — pass dependencies via constructor
- All AI calls go through AIProvider abstraction — never hardcode model names

## Key Principles

- Bot speaks Russian, respectful male tone
- Multi-layer system prompt: role + user profile + conversation history
- Profile auto-updates after each dialog turn (every 3 messages via analyzer)
- Memory trigger: regex patterns ("вспомни", "помнишь", "мы обсуждали" etc.)
- Webhook mode only (no polling) — Cloud Run requirement
- Current users: 2 (premium tier)

## Current State & Next Steps

**Branch**: `test` (active, deploys to Cloud Run) | `main` (stable)
**Code size**: ~2,870 lines Python across 11 files
**Biggest file**: `telegram_bot.py` (1,098 lines) — scheduled for refactoring

### Immediate Priorities (from ROADMAP Phase 1)
1. Split `telegram_bot.py` into modular `services/bot/` package
2. Replace global vars in `main.py` with `app.state` + lifespan
3. Improve analyzer: smart merge instead of full overwrite
4. Add structured JSON logging
5. Add pytest test suite

### Known Technical Debt
- `telegram_bot.py` uses nested closures inside `create_bot_app()` — needs BotContext dataclass
- `main.py` has 5 global variables (lines 29-34) — deprecated pattern
- `analyzer.py` line 95 fully overwrites `profile_summary` — loses manual corrections from `/correct`
- No error handler registered via `application.add_error_handler()`
- Memory queue can be lost on Cloud Run scale-down
- No tests

## Deployment

- **CI/CD**: GitHub Actions on push to `test` branch
- **Registry**: Google Artifact Registry (europe-west1)
- **Cloud Run**: 512Mi memory, 0-3 instances autoscale
- **Secrets**: GCP Secret Manager (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, NVIDIA_API_KEY, ALLOWED_USERS, DEFAULT_MODEL)
