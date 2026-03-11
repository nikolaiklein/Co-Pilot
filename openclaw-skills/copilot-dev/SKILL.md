---
name: copilot-dev
description: "Управление разработкой Co-Pilot: анализ кода, прогресс ROADMAP, поиск улучшений, code review, предложение рефакторинга. Используй для планирования спринтов, ревью кода, проверки технического долга."
metadata:
  {
    "openclaw":
      {
        "emoji": "💻",
        "always": true,
        "requires": { "bins": ["git", "python3"] },
      },
  }
---

# Co-Pilot Dev — Управление разработкой

Скилл для анализа кодовой базы, трекинга ROADMAP, и проактивных улучшений проекта Co-Pilot.

## Проект

- **Путь**: `/root/Co-Pilot/`
- **Репо**: `nikolaiklein/Co-Pilot`
- **Ветки**: `test` (деплой), `main` (стабильная)
- **Размер**: ~2,870 строк Python, 11 файлов

## Структура проекта

```
/root/Co-Pilot/
├── main.py                    — FastAPI, эндпоинты, lifecycle (355 строк)
├── services/
│   ├── ai_engine.py           — Мульти-провайдер AI (595 строк)
│   ├── telegram_bot.py        — Все хендлеры бота (1098 строк) ⚠️ НУЖЕН РЕФАКТОРИНГ
│   ├── db.py                  — Firestore CRUD (254 строки)
│   ├── analyzer.py            — Анализ профиля (106 строк) ⚠️ БАГ: перезапись профиля
│   └── memory.py              — RAG память (316 строк)
├── ROADMAP.md                 — План 5 фаз эволюции
├── CLAUDE.md                  — Инструкции для разработки
└── DOCS.md                    — Документация
```

## ROADMAP — Трекинг прогресса

### Фаза 1: Рефакторинг (Сложность: L) — статус: 0%
- [ ] 1.1 Разбить `telegram_bot.py` на `services/bot/` пакет
- [ ] 1.2 Убрать глобальные переменные из `main.py` (строки 29-34)
- [ ] 1.3 Исправить `analyzer.py` — smart merge вместо перезаписи (строка 95)
- [ ] 1.4 JSON-логирование + error handler
- [ ] 1.5 Тесты (pytest)

### Фаза 2: Мультитенант (Сложность: XL) — статус: 0%
- [ ] 2.1 Firestore-based доступ (убрать ALLOWED_USERS env)
- [ ] 2.2 Тарифные планы (free/basic/premium)
- [ ] 2.3 Telegram Stars + Stripe
- [ ] 2.4 Админ-команды

### Фаза 3: API (Сложность: M) — статус: 0%
- [ ] 3.1 REST API для экспорта
- [ ] 3.2 API-ключи
- [ ] 3.3 Вебхуки

### Фаза 4: Скиллы (Сложность: XL) — статус: 0%
- [ ] 4.1 BaseSkill + Registry
- [ ] 4.2 Встроенные скиллы (напоминания, заметки, цели)
- [ ] 4.3 Интеграция в пайплайн

### Фаза 5: Проактивность (Сложность: L) — статус: 0%
- [ ] 5.1 Запланированные чекины
- [ ] 5.2 Очередь проактивных сообщений
- [ ] 5.3 Улучшенный дайджест

## Команды анализа кода

### Общая статистика

```bash
cd /root/Co-Pilot

# Размер файлов
wc -l *.py services/*.py config/*.py

# Git статус
git log --oneline -20
git diff main..test --stat

# Последние изменения
git log --since="7 days ago" --oneline
```

### Поиск технического долга

```bash
cd /root/Co-Pilot

# TODO/FIXME/HACK
grep -rn "TODO\|FIXME\|HACK\|XXX\|WARN" services/ main.py --include="*.py"

# Длинные функции (>50 строк)
python3 -c "
import ast, sys
for f in ['main.py','services/ai_engine.py','services/telegram_bot.py','services/db.py','services/analyzer.py','services/memory.py']:
    try:
        tree = ast.parse(open(f).read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines = node.end_lineno - node.lineno
                if lines > 50:
                    print(f'{f}:{node.lineno} {node.name}() — {lines} строк')
    except: pass
"

# Дублирование кода
grep -rn "import asyncio" services/ --include="*.py"
grep -rn "asyncio.create_task" services/ --include="*.py"
```

### Анализ зависимостей

```bash
cd /root/Co-Pilot

# Текущие зависимости
cat requirements.txt

# Проверка уязвимостей (если pip-audit установлен)
pip-audit -r requirements.txt 2>/dev/null || echo "pip-audit не установлен"

# Неиспользуемые импорты
python3 -c "
import ast
for f in ['main.py','services/ai_engine.py','services/telegram_bot.py']:
    tree = ast.parse(open(f).read())
    imports = [n.name if isinstance(n, ast.Import) else n.module for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    print(f'{f}: {len(imports)} импортов')
"
```

## Проактивные проверки

При каждой сессии проверяй:

1. **Не вырос ли telegram_bot.py?** Если >1200 строк — срочно предложи рефакторинг
2. **Есть ли новые коммиты без тестов?** `git log --since=7d` vs `ls tests/`
3. **Незамерженные изменения?** `git diff main..test --stat`
4. **Обновлены ли зависимости?** Проверь даты последних версий в requirements.txt

## Формат рекомендаций

```
💻 CO-PILOT DEV REPORT — {дата}

Код: {total_lines} строк в {file_count} файлах
Коммитов за неделю: {count}
Ветка test vs main: {ahead_count} коммитов впереди

ROADMAP прогресс:
  Фаза 1: {X}% ({выполнено}/{всего} задач)
  Фаза 2-5: не начаты

⚠️ Технический долг:
  P1: {список}
  P2: {список}

📋 Рекомендация на следующий спринт:
  1. {задача} — {файл} — {оценка сложности}
  2. ...
```

## Шаблоны решений

### Рефакторинг telegram_bot.py
Текущая структура: всё в `create_bot_app()` как вложенные замыкания.
Целевая структура: `services/bot/` пакет с `BotContext` dataclass.
Подробный план: см. ROADMAP.md Фаза 1.1

### Исправление analyzer.py
Проблема: строка 95 — `update_user(user_id, {"profile_summary": profile_data})` — полная перезапись.
Решение: smart merge — объединять массивы, не удалять ручные правки от `/correct`.

### Добавление тестов
Нет тестов. Приоритет:
1. `test_utils.py` — markdown_to_html, split_message (чистые функции, легко тестить)
2. `test_ai_engine.py` — parse_model_string (сложная логика с 6+ ветвями)
3. `test_memory.py` — embedding mock, search mock
