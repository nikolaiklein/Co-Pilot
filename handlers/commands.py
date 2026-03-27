"""
Обработчики команд: /start, /help, /name, /clear, /myprofile, /correct,
а также onboarding-колбэки (cmd_myprofile, cmd_help, cmd_continue, cmd_name_hint,
start_interview, start_freeform).
"""

import json
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

from services.state import authorized

logger = logging.getLogger(__name__)


def register_handlers(app, services: dict):
    """Регистрирует команды и onboarding-колбэки."""
    db_service = services["db"]
    ai_engine = services["ai"]

    # --- /start (открытый, без auth) ---
    async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает команду /start."""
        user = update.effective_user
        logger.info(f"Команда /start от {user.id}")

        # Создаем или получаем пользователя
        user_data = {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "language_code": user.language_code,
            "is_bot": user.is_bot
        }
        existing_user = await db_service.get_or_create_user(user.id, user_data)

        # Проверяем, есть ли уже профиль (повторный /start)
        has_profile = existing_user and existing_user.get('profile_summary')

        if has_profile:
            # Пользователь уже общался — приветствуем с кнопками
            bot_name = existing_user.get('bot_nickname', 'Правильный Помощник')
            welcome_message = f"С возвращением, {user.first_name}! 👋\n\nЯ {bot_name}, готов продолжить работу."

            keyboard = [
                [
                    InlineKeyboardButton("📋 Мой профиль", callback_data="cmd_myprofile"),
                    InlineKeyboardButton("💬 Продолжить", callback_data="cmd_continue")
                ],
                [
                    InlineKeyboardButton("❓ Помощь", callback_data="cmd_help"),
                    InlineKeyboardButton("⚙️ Дать имя", callback_data="cmd_name_hint")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            # Новый пользователь — онбординг с кнопками
            welcome_message = f"""Привет, {user.first_name}! 👋

Я подключен к нейросети нового поколения. Прямо сейчас я — чистый лист.

Чтобы стать твоим идеальным ассистентом, мне нужно узнать тебя. С чего начнём?"""

            keyboard = [
                [
                    InlineKeyboardButton("🎤 Интервью", callback_data="start_interview"),
                    InlineKeyboardButton("💬 Свободный диалог", callback_data="start_freeform")
                ],
                [
                    InlineKeyboardButton("❓ Что ты умеешь?", callback_data="cmd_help")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(welcome_message, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

    app.add_handler(CommandHandler("start", handle_start))

    # --- /help (открытый, без auth) ---
    async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает команду /help."""
        help_text = """📚 <b>Список команд:</b>

/start — начать работу с ботом
/help — показать это сообщение
/myprofile — посмотреть моё досье (навыки, интересы, мечты)
/model — переключить AI-модель (Gemini, Claude, GPT, NVIDIA, MiniMax)
/vault — персональное хранилище (промпты, идеи, заметки)
/memory — статистика и поиск по долговременной памяти
/bulk — режим массовой загрузки данных (текст, файлы)
/name — дать мне имя (например: /name Макс)
/correct — исправить ошибку в профиле
/clear — очистить историю диалога

💬 Просто напишите мне сообщение, и я постараюсь помочь!
🎤 Также вы можете отправить голосовое сообщение."""
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

    app.add_handler(CommandHandler("help", handle_help))

    # --- /name (с auth) ---
    @authorized
    async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает команду /name — позволяет дать боту имя."""
        user = update.effective_user
        logger.info(f"Команда /name от {user.id}")

        args = context.args

        if not args:
            await update.message.reply_text(
                "💡 Чтобы дать мне имя, напиши:\n<code>/name Твоё_имя_для_меня</code>\n\nНапример: /name Макс",
                parse_mode=ParseMode.HTML
            )
            return

        new_name = " ".join(args).strip()

        if len(new_name) > 50:
            await update.message.reply_text("❌ Слишком длинное имя. Максимум 50 символов.")
            return

        try:
            await db_service.update_user(user.id, {"bot_nickname": new_name})

            await update.message.reply_text(
                f"✅ Отлично! Теперь я буду откликаться на имя <b>{new_name}</b>.\n\n"
                f"Приятно познакомиться, {user.first_name}! 🤝",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка сохранения имени бота для {user.id}: {e}")
            await update.message.reply_text("❌ Не удалось сохранить имя. Попробуй ещё раз.")

    app.add_handler(CommandHandler("name", handle_name))

    # --- /clear (с auth) ---
    @authorized
    async def handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает команду /clear — очищает историю диалога."""
        user = update.effective_user
        logger.info(f"Команда /clear от {user.id}")

        try:
            count = await db_service.clear_messages(user.id)
            await update.message.reply_text(f"✅ История очищена! Удалено сообщений: {count}")
        except Exception as e:
            logger.error(f"Ошибка очистки истории для {user.id}: {e}")
            await update.message.reply_text("❌ Не удалось очистить историю.")

    app.add_handler(CommandHandler("clear", handle_clear))

    # --- /myprofile (с auth) ---
    @authorized
    async def handle_myprofile(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает команду /myprofile — показывает накопленное досье."""
        user = update.effective_user
        logger.info(f"Команда /myprofile от {user.id}")

        try:
            user_data = await db_service.get_user(user.id)

            if not user_data or 'profile_summary' not in user_data:
                await update.message.reply_text(
                    "📋 <b>Профиль пока пуст</b>\n\n"
                    "Пообщайся со мной, и я постепенно соберу информацию о твоих навыках, "
                    "интересах и целях!",
                    parse_mode=ParseMode.HTML
                )
                return

            profile = user_data['profile_summary']

            # Форматируем профиль
            text = "📋 <b>Твой профиль Co-Pilot</b>\n\n"

            if isinstance(profile, dict):
                if profile.get('summary'):
                    text += f"📝 <b>Портрет:</b>\n{profile['summary']}\n\n"

                if profile.get('new_skills'):
                    text += "🛠 <b>Навыки:</b>\n"
                    for skill in profile['new_skills']:
                        text += f"  • {skill}\n"
                    text += "\n"

                if profile.get('interests'):
                    text += "🎯 <b>Интересы:</b>\n"
                    for interest in profile['interests']:
                        text += f"  • {interest}\n"
                    text += "\n"

                if profile.get('pain_points'):
                    text += "⚠️ <b>Точки роста:</b>\n"
                    for pain in profile['pain_points']:
                        text += f"  • {pain}\n"
                    text += "\n"

                if profile.get('dreams'):
                    text += "💭 <b>Мечты и идеи:</b>\n"
                    for dream in profile['dreams']:
                        text += f"  • {dream}\n"
                    text += "\n"
            else:
                text += str(profile)

            await update.message.reply_text(text, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.error(f"Ошибка получения профиля для {user.id}: {e}")
            await update.message.reply_text("❌ Не удалось загрузить профиль.")

    app.add_handler(CommandHandler("myprofile", handle_myprofile))

    # --- /correct (с auth) ---
    @authorized
    async def handle_correct(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает команду /correct — исправляет ошибки в профиле."""
        user = update.effective_user
        logger.info(f"Команда /correct от {user.id}")

        args = context.args

        if not args:
            await update.message.reply_text(
                "✏️ <b>Исправление профиля</b>\n\n"
                "Напиши что нужно исправить:\n"
                "<code>/correct убери что я не люблю Python</code>\n"
                "<code>/correct добавь что я увлекаюсь шахматами</code>",
                parse_mode=ParseMode.HTML
            )
            return

        correction_request = " ".join(args).strip()

        try:
            # Получаем текущий профиль
            user_data = await db_service.get_user(user.id)
            current_profile = user_data.get('profile_summary', {}) if user_data else {}

            # Формируем промпт для ИИ на исправление
            correction_prompt = f"""
Текущий профиль пользователя:
{current_profile}

Запрос на исправление: "{correction_request}"

Задача: Внеси исправление в профиль согласно запросу пользователя.
Верни исправленный JSON профиля в формате:
{{
  "new_skills": [...],
  "interests": [...],
  "pain_points": [...],
  "dreams": [...],
  "summary": "..."
}}

Если нужно удалить элемент — убери его из списка.
Если нужно добавить — добавь.
Ответ должен содержать только JSON без markdown.
"""

            # Отправляем запрос к ИИ
            corrected_json = await ai_engine.analyze_content(correction_prompt)

            # Парсим и сохраняем
            corrected_json = corrected_json.replace("```json", "").replace("```", "").strip()

            try:
                corrected_profile = json.loads(corrected_json)
                await db_service.update_user(user.id, {"profile_summary": corrected_profile})

                await update.message.reply_text(
                    "✅ <b>Профиль обновлён!</b>\n\n"
                    f"Применено: {correction_request}\n\n"
                    "Проверь изменения: /myprofile",
                    parse_mode=ParseMode.HTML
                )
            except json.JSONDecodeError:
                await update.message.reply_text(
                    "⚠️ Не удалось обработать запрос. Попробуй сформулировать иначе."
                )
            except Exception as db_err:
                logger.error(f"Ошибка сохранения профиля для {user.id}: {db_err}")
                await update.message.reply_text(
                    "⚠️ Не удалось сохранить изменения профиля. Попробуй ещё раз."
                )

        except Exception as e:
            logger.error(f"Ошибка исправления профиля для {user.id}: {e}")
            await update.message.reply_text("❌ Произошла ошибка. Попробуй позже.")

    app.add_handler(CommandHandler("correct", handle_correct))

    # --- /memory (с auth) ---
    memory_service = services.get("memory")

    @authorized
    async def handle_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Обрабатывает команду /memory — тест и статистика долговременной памяти.
        /memory — показать статистику
        /memory search запрос — поиск по памяти
        """
        user = update.effective_user

        if not memory_service:
            await update.message.reply_text("❌ Memory Service не инициализирован.")
            return

        args = context.args or []

        if args and args[0] == "search" and len(args) > 1:
            # Поиск по памяти
            query = " ".join(args[1:])
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

            results = await memory_service.search_memory(user.id, query, limit=5)

            if not results:
                await update.message.reply_text(f"🔍 По запросу «{query}» ничего не найдено в памяти.")
                return

            text = f"🔍 <b>Результаты поиска:</b> «{query}»\n\n"
            for i, r in enumerate(results, 1):
                score = r.get('score', 0)
                content_preview = r['content'][:200]
                text += f"{i}. 🧠 (score: {score:.2f}) {content_preview}\n\n"

            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        else:
            # Статистика памяти (через Mem0 API)
            try:
                all_memories = await memory_service.get_all_memories(user.id, limit=500)
                total = len(all_memories)

                text = f"""🧠 <b>Долговременная память (Mem0)</b>

📊 <b>Статистика:</b>
  Извлечённых фактов: {total}

💡 <b>Команды:</b>
  <code>/memory search запрос</code> — поиск по памяти

ℹ️ Mem0 автоматически извлекает факты из каждого разговора, дедуплицирует и обновляет существующие.
Поиск по памяти происходит при каждом сообщении — триггерные слова не нужны."""

                # Показать последние 5 фактов
                if all_memories:
                    text += "\n\n📝 <b>Последние факты:</b>\n"
                    for m in all_memories[:5]:
                        memory_text = m.get("memory", "")[:150]
                        text += f"  • {memory_text}\n"

                await update.message.reply_text(text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Ошибка получения статистики памяти для {user.id}: {e}")
                await update.message.reply_text(f"❌ Ошибка: {e}")

    app.add_handler(CommandHandler("memory", handle_memory))

    # --- /bulk (с auth) ---
    @authorized
    async def handle_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Обрабатывает команду /bulk — режим массовой загрузки данных.
        /bulk — включить/выключить режим
        """
        user = update.effective_user

        if not memory_service:
            await update.message.reply_text("❌ Memory Service не инициализирован.")
            return

        state = context.bot_data.get("state")

        if state.is_bulk(user.id):
            # Выключаем режим
            count = state.stop_bulk(user.id)
            await update.message.reply_text(
                f"📴 <b>Режим загрузки выключен</b>\n\n"
                f"📊 Загружено записей: {count}\n"
                f"Все данные сохранены с эмбеддингами и доступны для поиска.\n\n"
                f"Проверь: /memory",
                parse_mode=ParseMode.HTML
            )
        else:
            # Включаем режим
            state.start_bulk(user.id)
            await update.message.reply_text(
                "📥 <b>Режим массовой загрузки ВКЛЮЧЁН</b>\n\n"
                "Теперь можешь отправлять данные пачкой — текст, голосовые, файлы.\n"
                "ИИ не будет отвечать, всё сохраняется напрямую в долговременную память "
                "с векторными эмбеддингами.\n\n"
                "📎 <b>Поддерживаемые файлы:</b>\n"
                "  • TXT — текстовые файлы\n"
                "  • PDF — документы\n"
                "  • DOCX — Word-документы\n"
                "  • CSV — таблицы (сохраняются построчно)\n"
                "  • JSON — данные\n\n"
                "Для выхода из режима: /bulk",
                parse_mode=ParseMode.HTML
            )

    app.add_handler(CommandHandler("bulk", handle_bulk))

    # --- Onboarding callbacks ---
    _ONBOARDING_PATTERNS = r'^(cmd_myprofile|cmd_help|cmd_continue|cmd_name_hint|start_interview|start_freeform)$'

    async def handle_onboarding_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает onboarding-колбэки."""
        query = update.callback_query
        await query.answer()

        user = query.from_user
        data = query.data
        logger.info(f"Callback {data} от {user.id}")

        if data == "cmd_myprofile":
            # Показать профиль
            user_data = await db_service.get_user(user.id)
            if not user_data or 'profile_summary' not in user_data:
                await query.message.reply_text(
                    "📋 <b>Профиль пока пуст</b>\n\nПообщайся со мной, чтобы я узнал тебя лучше!",
                    parse_mode=ParseMode.HTML
                )
            else:
                profile = user_data['profile_summary']
                text = "📋 <b>Твой профиль</b>\n\n"
                if isinstance(profile, dict):
                    if profile.get('summary'):
                        text += f"📝 {profile['summary']}\n\n"
                    if profile.get('interests'):
                        text += f"🎯 <b>Интересы:</b> {', '.join(profile['interests'][:5])}\n"
                    if profile.get('dreams'):
                        text += f"💭 <b>Цели:</b> {', '.join(profile['dreams'][:3])}\n"
                await query.message.reply_text(text, parse_mode=ParseMode.HTML)

        elif data == "cmd_help":
            help_text = """📚 <b>Что я умею:</b>

🎯 <b>Учусь понимать тебя</b> — собираю профиль из диалогов
📋 /myprofile — твоё досье
📦 /vault — хранилище промптов и идей
✏️ /correct — исправить ошибку в профиле
🏷 /name — дать мне имя
🗑 /clear — очистить историю

💬 Просто пиши или 🎤 отправляй голосовые!"""
            await query.message.reply_text(help_text, parse_mode=ParseMode.HTML)

        elif data == "cmd_continue":
            await query.message.reply_text("Слушаю тебя! О чём поговорим? 💬")

        elif data == "cmd_name_hint":
            await query.message.reply_text(
                "🏷 <b>Дай мне имя!</b>\n\nНапиши: /name <i>Твоё_имя_для_меня</i>\n\nНапример: /name Макс",
                parse_mode=ParseMode.HTML
            )

        elif data == "start_interview":
            await query.message.reply_text(
                "🎤 <b>Давай познакомимся!</b>\n\n"
                "Расскажи немного о себе:\n"
                "— Чем занимаешься?\n"
                "— Какая главная цель на ближайший месяц?\n\n"
                "Можешь написать текстом или записать голосовое 🎙",
                parse_mode=ParseMode.HTML
            )

        elif data == "start_freeform":
            await query.message.reply_text(
                "💬 Отлично! Просто пиши мне о чём угодно.\n\n"
                "Я буду постепенно узнавать тебя из наших диалогов. Начинай! 🚀"
            )

    app.add_handler(CallbackQueryHandler(handle_onboarding_callback, pattern=_ONBOARDING_PATTERNS))
