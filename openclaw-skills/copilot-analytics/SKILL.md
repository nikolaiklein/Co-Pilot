---
name: copilot-analytics
description: "Аналитика Co-Pilot бота: метрики пользователей, использование AI моделей, статистика памяти, стоимость, рост. Используй для отчётов, анализа метрик, планирования масштабирования."
metadata:
  {
    "openclaw":
      {
        "emoji": "📊",
        "always": true,
        "requires": { "bins": ["gcloud", "curl"] },
      },
  }
---

# Co-Pilot Analytics — Метрики и аналитика

Скилл для сбора и анализа метрик проекта Co-Pilot.

## Источники данных

### 1. Firestore (пользователи, сообщения, память)

```bash
# Проект
PROJECT="co-pilot-bot"

# Список всех юзеров
gcloud firestore documents list \
  --collection-ids=users \
  --project=$PROJECT \
  --format="table(name, fields.username.stringValue, fields.first_name.stringValue, fields.selected_model.stringValue)"
```

### 2. Debug API (память)

```bash
BASE="https://co-pilot-dcvd3baddq-ew.a.run.app"

# Статистика памяти конкретного юзера
curl -s "$BASE/debug/memory/292628110" | python3 -m json.tool

# Статистика всех известных юзеров (получить ID из Firestore, запросить каждого)
for uid in 292628110 718768526; do
  echo "=== User $uid ==="
  curl -s "$BASE/debug/memory/$uid" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    s = d.get('stats', {})
    print(f'  Records: {s.get(\"total\",0)}, Embeddings: {s.get(\"with_embedding\",0)}, Summaries: {s.get(\"summaries\",0)}')
except: print('  Error reading stats')
"
done
```

### 3. Cloud Run метрики

```bash
# Количество запросов и ошибок за последний час
gcloud run services logs read co-pilot \
  --region=europe-west1 \
  --project=co-pilot-bot \
  --limit=500 2>/dev/null | python3 -c "
import sys
lines = sys.stdin.readlines()
total = len([l for l in lines if 'POST /webhook' in l])
errors = len([l for l in lines if 'error' in l.lower() or 'Error' in l])
print(f'Webhook запросов: {total}')
print(f'Ошибок в логах: {errors}')
print(f'Error rate: {errors/max(total,1)*100:.1f}%')
"
```

### 4. GitHub Actions статистика

```bash
# Последние 10 деплоев
gh run list --repo nikolaiklein/Co-Pilot --branch test --limit 10 \
  --json status,conclusion,createdAt,displayTitle \
  --jq '.[] | "\(.conclusion)\t\(.createdAt)\t\(.displayTitle)"'
```

### 5. AI модели использование

```bash
# Какие модели выбраны юзерами
gcloud firestore documents list \
  --collection-ids=users \
  --project=co-pilot-bot \
  --format=json 2>/dev/null | python3 -c "
import sys, json
try:
    docs = json.load(sys.stdin)
    for doc in docs:
        fields = doc.get('fields', {})
        uid = fields.get('id', {}).get('integerValue', '?')
        model = fields.get('selected_model', {}).get('stringValue', 'default')
        name = fields.get('first_name', {}).get('stringValue', '?')
        print(f'  User {uid} ({name}): {model}')
except: print('  Error parsing')
"
```

## Метрики для отслеживания

### Пользователи
| Метрика | Как считать | Частота |
|---------|-------------|---------|
| Всего юзеров | Firestore `users/` count | Ежедневно |
| Новых за неделю | `created_at > 7d ago` | Еженедельно |
| Активных за 24ч | Логи: уникальные user_id в webhook | Ежедневно |
| По тарифам | Firestore `users/{id}.tier` | Ежедневно |
| Churn 30д | Нет сообщений 30+ дней | Еженедельно |

### Использование AI
| Метрика | Как считать | Частота |
|---------|-------------|---------|
| Запросов к AI/день | Логи: `generate_response` calls | Ежедневно |
| По провайдерам | Логи: `GeminiProvider`, `OpenAICompatibleProvider` | Еженедельно |
| Транскрипций/день | Логи: `Транскрипция для` | Ежедневно |
| Embedding вызовов | Логи: `Memory stored` count | Ежедневно |
| Средн. время ответа | Логи: timestamp diff webhook→sendMessage | Еженедельно |

### Память (RAG)
| Метрика | Как считать | Частота |
|---------|-------------|---------|
| Записей в памяти | Debug API: `stats.total` | Ежедневно |
| С эмбеддингами | Debug API: `stats.with_embedding` | Ежедневно |
| Конспектов | Debug API: `stats.summaries` | Еженедельно |
| Trigger срабатываний | Логи: `trigger detected` | Еженедельно |

### Стоимость (оценка)
| Ресурс | Бесплатный лимит | Текущее использование |
|--------|-------------------|----------------------|
| Firestore reads | 50K/день | Оценить из логов |
| Firestore writes | 20K/день | ~10-20 на сообщение |
| Cloud Run | 2M requests/мес | Считать из логов |
| Gemini API | Бесплатный tier | Зависит от модели |
| NVIDIA NIM | Лимит по ключу | Запросить `/v1/usage` |

## Формат аналитического отчёта

```
📊 CO-PILOT ANALYTICS — {период}

👥 ПОЛЬЗОВАТЕЛИ
  Всего: {N} | Новых: {N} | Активных (24ч): {N}
  По тарифам: Free: {N} | Basic: {N} | Premium: {N}

💬 АКТИВНОСТЬ
  Сообщений: {N} | Голосовых: {N} | Фото: {N} | Файлов: {N}
  Среднее на юзера: {N}/день

🤖 AI МОДЕЛИ
  {model_1}: {N} запросов ({X}%)
  {model_2}: {N} запросов ({X}%)
  Транскрипций: {N}

🧠 ПАМЯТЬ (RAG)
  Всего записей: {N} | С эмбеддингами: {N}
  Trigger-поисков: {N} | Суммаризаций: {N}

💰 СТОИМОСТЬ (оценка)
  Firestore: ${X} | Cloud Run: ${X} | AI API: ${X}
  Итого: ~${X}/мес

📈 ТРЕНДЫ
  {рост/падение активности}
  {самый активный юзер}
  {самая популярная модель}

💡 РЕКОМЕНДАЦИИ
  {конкретные предложения на основе данных}
```

## Проактивные алерты

Автоматически предупреждай владельца если:

1. **Нет активности 48ч** — все юзеры молчат (возможно сервис сломан)
2. **Error rate > 10%** — слишком много ошибок в webhook
3. **Память растёт быстро** — >100 записей/день на юзера (возможен bulk без /bulk)
4. **Новый юзер без ответа** — /start без follow-up (проблема с ALLOWED_USERS)
5. **AI модель таймаутит** — NVIDIA NIM >30s ответ (предложить переключить)
