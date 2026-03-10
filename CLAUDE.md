# Co-Pilot — Digital Heritage System

Telegram-бот "Ко-пилот": эмпатичный ассистент-биограф и коуч. Интервьюирует, выявляет навыки, структурирует опыт, помогает осваивать ИИ-инструменты.

## Stack

- **Python 3.11**, FastAPI, uvicorn
- **Telegram**: `python-telegram-bot` v20+ (async, webhook mode)
- **Database**: Google Cloud Firestore (Native mode)
- **AI**: Multi-provider — Gemini, Claude, OpenAI (via `AIProvider` abstraction)
- **Deploy**: Google Cloud Run, Docker, GitHub Actions auto-deploy from `test` branch
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
main.py                  — FastAPI app, webhook endpoint, cron endpoint
config/
  firebase_init.py       — Firebase Admin SDK initialization (ADC)
services/
  ai_engine.py           — AIProvider abstraction, Gemini/Claude/OpenAI
  telegram_bot.py        — Bot handlers: text, voice, commands
  db.py                  — Firestore: users, messages, reports
  analyzer.py            — Background profile analysis via LLM
```

### Request Flow

```
Telegram webhook POST /webhook
  → python-telegram-bot processes Update
  → handle_message / handle_voice
  → db.get_or_create_user + db.save_message
  → ai_engine.generate_response (with history context)
  → db.save_message (assistant reply)
  → send response to Telegram
```

### Firestore Structure

```
users/{user_id}
  ├── profile fields (username, first_name, created_at, profile_summary)
  ├── messages/{auto_id}    — role, content, timestamp
  └── reports/{auto_id}     — analysis results, timestamp
```

## Environment Variables

See `.env.example` for full list. Required:
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `GEMINI_API_KEY` — Google AI Studio key
- At least one AI provider key

## Code Style

- Python 3.11+, async/await everywhere
- Type hints on all functions
- logging module (not print)
- Docstrings in Russian (project language)
- Keep services loosely coupled — pass dependencies via constructor

## Key Principles

- Bot speaks Russian, respectful male tone
- Multi-layer system prompt: role + user profile + conversation history
- Profile auto-updates after each dialog turn (not just cron)
- All AI calls go through AIProvider abstraction — never hardcode model names
- Webhook mode only (no polling) — Cloud Run requirement
