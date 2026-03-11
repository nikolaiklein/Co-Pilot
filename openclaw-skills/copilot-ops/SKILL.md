---
name: copilot-ops
description: "Мониторинг и диагностика Co-Pilot бота: Cloud Run статус, логи ошибок, здоровье webhook, Firestore метрики, статус деплоя. Используй при проверке здоровья сервиса, анализе ошибок, после деплоя."
metadata:
  {
    "openclaw":
      {
        "emoji": "🔧",
        "always": true,
        "requires": { "bins": ["gcloud", "curl", "gh"] },
      },
  }
---

# Co-Pilot Ops — Мониторинг и диагностика

Скилл для мониторинга здоровья Co-Pilot Telegram бота (@SergeiVladimirovich_bot).

## Ключевые данные проекта

- **Cloud Run сервис**: `co-pilot` в `europe-west1`, проект `co-pilot-bot`
- **URL**: `https://co-pilot-dcvd3baddq-ew.a.run.app`
- **Репозиторий**: `/root/Co-Pilot/` → `nikolaiklein/Co-Pilot` (ветка `test`)
- **Firestore**: база `(default)` в `co-pilot-bot`

## Команды диагностики

### 1. Быстрая проверка здоровья

```bash
# Health check
curl -s https://co-pilot-dcvd3baddq-ew.a.run.app/ | python3 -m json.tool

# Webhook статус
TOKEN=$(gcloud secrets versions access latest --secret=TELEGRAM_BOT_TOKEN --project=co-pilot-bot 2>/dev/null)
curl -s "https://api.telegram.org/bot${TOKEN}/getWebhookInfo" | python3 -m json.tool
```

### 2. Cloud Run статус и ресурсы

```bash
# Описание сервиса (память, CPU, инстансы)
gcloud run services describe co-pilot --region=europe-west1 --project=co-pilot-bot --format="yaml(spec.template)"

# Текущие ревизии
gcloud run revisions list --service=co-pilot --region=europe-west1 --project=co-pilot-bot --limit=5
```

### 3. Логи ошибок

```bash
# Последние 100 логов
gcloud run services logs read co-pilot --region=europe-west1 --project=co-pilot-bot --limit=100

# Только ошибки
gcloud run services logs read co-pilot --region=europe-west1 --project=co-pilot-bot --limit=200 2>/dev/null | grep -i "error\|exception\|traceback\|failed"

# Логи конкретного пользователя
gcloud run services logs read co-pilot --region=europe-west1 --project=co-pilot-bot --limit=100 2>/dev/null | grep "292628110"
```

### 4. Статус деплоя (GitHub Actions)

```bash
# Последние 5 раннов CI/CD
gh run list --repo nikolaiklein/Co-Pilot --branch test --limit 5

# Детали последнего ранна
gh run view --repo nikolaiklein/Co-Pilot $(gh run list --repo nikolaiklein/Co-Pilot --branch test --limit 1 --json databaseId -q '.[0].databaseId')
```

### 5. Проверка секретов

```bash
# Список секретов
gcloud secrets list --project=co-pilot-bot

# Проверить DEFAULT_MODEL
gcloud secrets versions access latest --secret=DEFAULT_MODEL --project=co-pilot-bot

# Проверить ALLOWED_USERS
gcloud secrets versions access latest --secret=ALLOWED_USERS --project=co-pilot-bot
```

### 6. Debug-эндпоинты

```bash
# Статистика памяти юзера
curl -s "https://co-pilot-dcvd3baddq-ew.a.run.app/debug/memory/292628110" | python3 -m json.tool

# Поиск по памяти юзера
curl -s "https://co-pilot-dcvd3baddq-ew.a.run.app/debug/memory/292628110?q=цели" | python3 -m json.tool
```

## Алерты и пороги

| Метрика | Нормально | Внимание | Критично |
|---------|-----------|----------|----------|
| Health check | 200 OK | — | Не отвечает |
| Webhook pending_update_count | 0-2 | 5-10 | >10 |
| Ошибки в логах (за час) | 0-2 | 3-10 | >10 |
| Cold start время | <3s | 3-8s | >8s |
| Инстансы | 0-1 | 2 | 3 (макс) |

## Формат отчёта

При выполнении диагностики, формируй отчёт:

```
🔧 CO-PILOT OPS REPORT — {дата}

Сервис: {статус}
Ревизия: {revision_name} (деплой: {время})
Ресурсы: {memory} / {cpu} / инстансы: {count}
Webhook: {ok/error} (pending: {N})

Ошибки (за 24ч): {count}
  P0: {список критичных}
  P1: {список важных}

Секреты: {все ОК / проблемы}
GitHub Actions: {последний статус}
```

## Действия при проблемах

### Сервис не отвечает
1. Проверь Cloud Run: `gcloud run services describe co-pilot ...`
2. Проверь логи запуска: ищи ошибки в `startup_event()`
3. Проверь секреты: все ли доступны

### Webhook не работает
1. Проверь `getWebhookInfo` — есть ли pending_update_count > 0
2. Переустанови webhook:
   ```bash
   curl "https://api.telegram.org/bot${TOKEN}/setWebhook?url=https://co-pilot-dcvd3baddq-ew.a.run.app/webhook"
   ```

### Деплой упал
1. `gh run list` — найди упавший ранн
2. `gh run view {id} --log-failed` — смотри логи
3. Откати: `gcloud run services update-traffic co-pilot --to-revisions={prev}=100 --region=europe-west1`
