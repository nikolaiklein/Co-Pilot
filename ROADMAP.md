# Co-Pilot: План эволюции в SaaS-продукт

## Контекст

Co-Pilot (@SergeiVladimirovich_bot) — Telegram-бот "Правильный Помощник": эмпатичный AI-ассистент, биограф и коуч. Сейчас обслуживает 2 пользователей на Cloud Run + Firestore. Цель — превратить в SaaS-продукт с тысячами пользователей, подпиской, API для экспорта данных, системой скиллов и проактивным поведением.

**Текущий стек:** Python 3.11 + FastAPI + Firestore + Cloud Run + Multi-AI (Gemini/Claude/OpenAI/NVIDIA)

---

## Фаза 1: Рефакторинг и фундамент (Сложность: L)

**Цель:** Сделать код поддерживаемым, тестируемым, готовым к масштабированию.

### 1.1 Разбить telegram_bot.py (1098 строк) на модули

Сейчас всё — вложенные замыкания внутри одной функции `create_bot_app()`. Разбить на:

```
services/
  bot/
    __init__.py            — экспорт create_bot_app
    app.py                 — create_bot_app(), регистрация хендлеров
    context.py             — BotContext dataclass (db, ai_engine, memory, analyzer, bulk_users)
    middleware.py           — декоратор @authorized, обработка ошибок
    dialog.py              — process_dialog_turn() (ядро пайплайна)
    utils.py               — markdown_to_html, split_message, extract_text_from_file
    handlers/
      commands.py           — /start, /help, /clear, /name, /correct, /myprofile
      model.py              — /model
      memory_cmd.py         — /memory, /bulk
      messages.py           — текст, голос, фото, документы
      callbacks.py          — inline-кнопки
```

**Ключевое изменение:** `BotContext` dataclass вместо замыканий. Передаётся через `context.bot_data["ctx"]`.

### 1.2 Убрать глобальные переменные из main.py

Заменить 5 глобальных переменных (строки 29-34) на `app.state` + FastAPI lifespan context manager (текущий `@app.on_event("startup")` deprecated с FastAPI 0.109).

### 1.3 Улучшить анализатор профиля

`analyzer.py` строка 95 — полная перезапись `profile_summary`. Заменить на интеллектуальное слияние: объединять массивы, сохранять ручные правки от `/correct`, не удалять подтверждённые факты.

### 1.4 Логирование и мониторинг

- Структурированные JSON-логи (`python-json-logger`) для Cloud Run
- Telegram error handler через `application.add_error_handler()` (сейчас ошибки в хендлерах проглатываются молча)

### 1.5 Тесты

```
tests/
  conftest.py              — фикстуры: mock_db, mock_ai_engine
  test_ai_engine.py        — parse_model_string(), create_provider()
  test_analyzer.py         — парсинг JSON, слияние профилей
  test_memory.py           — embedding, search
  test_bot/
    test_commands.py
    test_dialog.py
    test_utils.py           — markdown_to_html, split_message
```

**Зависимости:** `pytest`, `pytest-asyncio`, `python-json-logger`, `ruff`

---

## Фаза 2: Мультитенант и подписки (Сложность: XL)

**Цель:** Заменить ALLOWED_USERS на Firestore-based контроль, добавить подписочные уровни.

### 2.1 Контроль доступа через Firestore

Убрать `ALLOWED_USERS` env var. Добавить поля в `users/{user_id}`:

```
status: "active" | "banned" | "pending"
tier: "free" | "basic" | "premium"
tier_expires_at: timestamp | null
daily_messages_count: number
daily_messages_reset_at: timestamp
total_messages: number
referral_code: string
referred_by: string | null
```

Новый пользователь при `/start` → `tier: "free"`, `status: "active"`.

### 2.2 Лимиты по тарифам

**Файл:** `services/subscription.py`

```python
TIER_LIMITS = {
    "free":    {"daily_messages": 10,  "models": ["gemini"], "memory_docs": 100},
    "basic":   {"daily_messages": 100, "models": ["gemini", "nvidia"], "memory_docs": 5000, "features": ["bulk", "export"]},
    "premium": {"daily_messages": -1,  "models": "all", "memory_docs": -1, "features": "all"},
}
```

Счётчик через Firestore atomic increment. Сброс через `/cron/reset-limits` (ежедневно).

### 2.3 Оплата: Telegram Stars + Stripe

- **Telegram Stars** (приоритет) — нативно через python-telegram-bot, без мерчант-аккаунта
- **Stripe** (позже) — для веб-платежей
- Команда `/subscribe` — сравнение тарифов и кнопки оплаты
- Новая подколлекция `users/{user_id}/payments/{auto_id}`

### 2.4 Админ-команды (через Telegram, без панели)

**Файл:** `services/bot/handlers/admin.py`

- `/admin stats` — сколько юзеров, по тарифам, активных сегодня
- `/admin user {id}` — инфо о юзере
- `/admin ban/unban {id}`
- `/admin gift {id} premium 30` — подарить подписку

Доступ: только user_id 292628110.

**Миграция:** Существующим 2 юзерам ставим `tier: "premium"`. Новые получают `tier: "free"`.

---

## Фаза 3: API и экспорт данных (Сложность: M)

**Цель:** REST API для экспорта данных, вебхуки для интеграций.

### 3.1 REST API

**Файл:** `api/routes.py`

```
GET  /api/v1/me/profile       — профиль (JSON)
GET  /api/v1/me/messages      — история сообщений (JSON, пагинация)
GET  /api/v1/me/memory        — записи памяти (JSON, пагинация)
GET  /api/v1/me/export        — полный экспорт (ZIP)
POST /api/v1/me/import        — импорт данных
```

### 3.2 Аутентификация API

**Файл:** `api/auth.py`

- API-ключ генерируется командой `/api` в Telegram
- Хеш (SHA-256) хранится в Firestore `users/{user_id}.api_key`
- Передаётся в заголовке `Authorization: Bearer <key>`

### 3.3 Вебхуки

Пользователи регистрируют URL для получения событий:
- `profile.updated`, `message.received`, `digest.sent`
- HMAC-подпись для безопасности
- Подколлекция `users/{user_id}/webhooks/{auto_id}`

---

## Фаза 4: Скиллы и плагины (Сложность: XL)

**Цель:** Модульная система расширяемых возможностей. Вдохновлено OpenClaw.

### 4.1 Архитектура скиллов

**Структура:**

```
skills/
  base.py                  — BaseSkill ABC
  registry.py              — SkillRegistry (discover, enable/disable)
  builtin/
    reminders.py           — напоминания (/remind 14:00 позвонить маме)
    notes.py               — заметки (/note, /notes)
    goals.py               — цели с дедлайнами (/goal)
    web_search.py          — веб-поиск через Perplexica
    daily_wisdom.py        — ежедневная мудрость по профилю
```

**BaseSkill интерфейс:**

```python
class BaseSkill(ABC):
    name: str               # "reminders"
    display_name: str       # "Напоминания"
    commands: list[str]     # ["/remind"]
    triggers: list[Pattern] # авто-детект паттернов
    tier_required: str      # "free" | "basic" | "premium"

    async def handle_command(self, cmd, args, user_id, ctx) -> str
    async def handle_trigger(self, text, user_id, ctx) -> str | None
    async def on_schedule(self, user_id, ctx) -> str | None  # для cron
```

### 4.2 Встроенные скиллы

- **Напоминания** — парсинг времени через LLM, доставка через `/cron/check-reminders` каждые 5 мин
- **Заметки** — структурированное хранение, поиск
- **Цели** — трекинг с дедлайнами, авто-детект упоминаний в разговоре
- **Веб-поиск** — интеграция с Perplexica (`http://100.76.93.4:3000/api/search`)

Данные скиллов: `users/{user_id}/skill_data/{skill_name}`

### 4.3 Интеграция в пайплайн

В `process_dialog_turn()`:
1. Перед LLM: проверить триггеры скиллов → обработать или обогатить промпт
2. После LLM: пост-обработка (детект поставленных целей, напоминаний)

Команда `/skills` — список доступных скиллов, вкл/выкл.

---

## Фаза 5: Проактивность и интеллект (Сложность: L)

**Цель:** Бот сам инициирует разговоры, отслеживает прогресс, отправляет умные напоминания.

### 5.1 Запланированные чекины

Крон `/cron/scheduled-checkins` (ежедневно):
- Если юзер не писал 3+ дней и `tier != "free"`
- LLM генерирует персонализированное сообщение по профилю
- "Привет {name}, в прошлый раз мы обсуждали {topic}. Как дела с {goal}?"

Поля в `users/{user_id}`:
```
last_interaction_at: timestamp
checkin_preferences: { enabled, frequency_days, preferred_time, timezone }
```

### 5.2 Очередь проактивных сообщений

**Файл:** `services/proactive.py`

Топ-уровневая коллекция `proactive_queue/{auto_id}`:
```
user_id, message, source ("checkin"|"reminder"|"goal"|"digest"),
priority, scheduled_at, status ("pending"|"delivered"|"failed")
```

Крон `/cron/deliver-proactive` каждые 5 минут. Лимит: 1 проактивное сообщение/день на юзера.

### 5.3 Улучшенный дайджест

Заменить шаблонный дайджест на LLM-генерированный:
- Персонализированное резюме недели
- Прогресс по целям
- Статистика памяти (сколько бот узнал за неделю)
- Предложения микро-задач

---

## Приоритет и зависимости

```
Фаза 1 (фундамент) ──→ Фаза 2 (подписки) ──→ Фаза 3 (API)
                                    │
                                    └──→ Фаза 4 (скиллы) ──→ Фаза 5 (проактивность)
```

Фаза 1 — обязательна перед всем. Фазы 3 и 4 могут идти параллельно после Фазы 2.

## Ключевые риски

| Риск | Решение |
|------|---------|
| Потеря очереди memory при scale-down Cloud Run | Перевести на Firestore-based очередь (Фаза 1) |
| Стоимость Firestore при тысячах юзеров | Free-тир: cheapest модель, лимит контекста |
| Cold start Cloud Run для проактивных сообщений | min-instances=1 для платных тарифов |
| Сложность Telegram Stars интеграции | Начать с ручных `/admin gift`, Stars добавить вторым шагом |

## Верификация

После каждой фазы:
1. Прогнать тесты (`pytest tests/`)
2. Задеплоить на test-бранч, проверить в Telegram
3. Проверить логи Cloud Run на ошибки
4. Для Фазы 2: протестировать регистрацию нового юзера (free tier)
5. Для Фазы 3: вызвать API endpoints через curl
6. Для Фазы 4: протестировать `/remind`, `/note`, `/goal`
7. Для Фазы 5: дождаться проактивного сообщения

---

## Открытые вопросы для проработки

### Ценностное предложение и целевая аудитория

**Ключевой вопрос:** В чём уникальная ценность Co-Pilot и как её подавать разным аудиториям?

**Потенциальные сегменты:**

1. **Пожилые люди** — мягкое обучение использованию AI через диалог. Бот как "цифровой помощник", который не пугает, а постепенно знакомит с возможностями. Сохранение жизненного опыта, воспоминаний, мудрости (цифровое наследие). Голосовой ввод критически важен.

2. **Дети и подростки** — обучающий AI-наставник. Помогает структурировать мысли, ставить цели, развивать навыки. Безопасная среда для общения с AI. Родительский контроль.

3. **Профессионалы** — персональный AI-коуч с глубоким знанием контекста пользователя. Автоматизация рутины, трекинг целей, экспорт данных для работы.

4. **Образование** — учебный ассистент с долговременной памятью. Помнит прогресс ученика, адаптирует подход.

**TODO:** Определить MVP-сегмент, сформулировать ценностное предложение, продумать онбординг для каждой аудитории.
