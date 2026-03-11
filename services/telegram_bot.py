import os
import logging
import io
import re
import csv
import json
import html
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from services.db import DatabaseService

# Настройка логирования
logger = logging.getLogger(__name__)

# Лимит символов в одном сообщении Telegram
TELEGRAM_MESSAGE_LIMIT = 4096


def markdown_to_telegram_html(text: str) -> str:
    """
    Конвертирует Markdown от AI в HTML-формат Telegram.
    Telegram поддерживает: <b>, <i>, <code>, <pre>, <a>, <s>, <u>
    """
    # Экранируем HTML-спецсимволы
    text = html.escape(text)
    
    # Конвертируем **жирный** -> <b>жирный</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    
    # Конвертируем *курсив* -> <i>курсив</i>
    text = re.sub(r'\*([^*]+?)\*', r'<i>\1</i>', text)
    
    # Конвертируем `код` -> <code>код</code>
    text = re.sub(r'`([^`]+?)`', r'<code>\1</code>', text)
    
    # Конвертируем ~~зачёркнутый~~ -> <s>зачёркнутый</s>
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)
    
    return text


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list:
    """
    Разбивает длинное сообщение на части, не превышающие лимит.
    Старается разбивать по абзацам или предложениям.
    """
    if len(text) <= limit:
        return [text]
    
    parts = []
    current_part = ""
    
    # Разбиваем по абзацам
    paragraphs = text.split('\n\n')
    
    for paragraph in paragraphs:
        # Если абзац сам по себе слишком длинный
        if len(paragraph) > limit:
            # Сохраняем текущую часть если есть
            if current_part:
                parts.append(current_part.strip())
                current_part = ""
            
            # Разбиваем длинный абзац по предложениям
            sentences = re.split(r'(?<=[.!?])\s+', paragraph)
            for sentence in sentences:
                if len(current_part) + len(sentence) + 1 <= limit:
                    current_part += sentence + " "
                else:
                    if current_part:
                        parts.append(current_part.strip())
                    current_part = sentence + " "
        elif len(current_part) + len(paragraph) + 2 <= limit:
            current_part += paragraph + "\n\n"
        else:
            parts.append(current_part.strip())
            current_part = paragraph + "\n\n"
    
    if current_part.strip():
        parts.append(current_part.strip())
    
    return parts if parts else [text[:limit]]

# Мы не можем использовать аннотацию типа AIEngine здесь из-за циклического импорта,
# если бы ai_engine импортировал telegram_bot, но здесь это безопасно.
# Однако, для чистоты, будем использовать Any или просто duck typing.
# from services.ai_engine import AIEngine

async def create_bot_app(db_service: DatabaseService, ai_engine, analyzer_service=None, memory_service=None) -> Application:
    """
    Создает и настраивает приложение Telegram бота.
    Регистрирует обработчики сообщений.

    Args:
        db_service (DatabaseService): Инициализированный сервис базы данных.
        ai_engine (AIEngine): Инициализированный сервис ИИ.
        analyzer_service: Сервис анализа профиля (опционально).

    Returns:
        Application: Настроенное приложение python-telegram-bot.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN не найден в переменных окружения.")
        return None

    # Список разрешённых пользователей (пустой = все разрешены)
    allowed_users_str = os.getenv("ALLOWED_USERS", "")
    allowed_users = set()
    if allowed_users_str.strip():
        allowed_users = {int(uid.strip()) for uid in allowed_users_str.split(",") if uid.strip()}
    if allowed_users:
        logger.info(f"Авторизация включена. Разрешённые пользователи: {allowed_users}")

    try:
        # Создаем билдер приложения
        builder = Application.builder().token(token)
        application = builder.build()

        def is_authorized(user_id: int) -> bool:
            """Проверяет, авторизован ли пользователь."""
            if not allowed_users:
                return True
            return user_id in allowed_users

        # Множество пользователей в режиме bulk-загрузки
        bulk_mode_users: dict[int, int] = {}  # user_id -> count загруженных записей

        # Общая функция для обработки логики диалога (используется для текста и голоса)
        async def process_dialog_turn(user, chat_id, user_text, context):
            try:
                # 0. Режим bulk-загрузки: сохраняем в память без AI
                if user.id in bulk_mode_users:
                    if memory_service:
                        import asyncio
                        asyncio.create_task(memory_service.store_message(user.id, "user", user_text))
                        bulk_mode_users[user.id] = bulk_mode_users.get(user.id, 0) + 1
                        count = bulk_mode_users[user.id]
                        # Отвечаем кратко каждые 5 сообщений, иначе тихо
                        if count % 5 == 0:
                            await context.bot.send_message(chat_id=chat_id, text=f"✅ Загружено: {count}")
                        else:
                            await context.bot.send_message(chat_id=chat_id, text="✅")
                    else:
                        await context.bot.send_message(chat_id=chat_id, text="❌ Memory Service не доступен.")
                    return

                # 1. Получаем или создаем пользователя в БД
                user_data = {
                    "id": user.id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "language_code": user.language_code,
                    "is_bot": user.is_bot
                }
                db_user = await db_service.get_or_create_user(user.id, user_data)

                # 2. Сохраняем сообщение пользователя
                await db_service.save_message(user.id, "user", user_text)

                # 3. Отправляем действие "печатает" (typing)
                await context.bot.send_chat_action(chat_id=chat_id, action="typing")

                # 4. Получаем историю
                history = await db_service.get_last_messages(user.id, limit=20)

                # Исключаем текущее сообщение из истории, если оно там уже есть
                if history and history[-1]['content'] == user_text and history[-1]['role'] == 'user':
                     history_for_ai = history[:-1]
                else:
                     history_for_ai = history

                # 5. Определяем провайдер пользователя (сохранённый в БД)
                from services.ai_engine import parse_model_string
                user_provider = None
                user_model = None
                user_model_str = db_user.get('selected_model') if db_user else None
                if user_model_str:
                    user_provider, user_model = parse_model_string(user_model_str)

                # 5.1. Долговременная память: поиск релевантного контекста
                memory_context = ""
                if memory_service:
                    try:
                        memory_context = await memory_service.get_memory_context(user.id, user_text)
                    except Exception as mem_err:
                        logger.warning(f"Memory search error: {mem_err}")

                # 6. Генерируем ответ (с контекстом памяти)
                augmented_text = user_text
                if memory_context:
                    augmented_text = f"{memory_context}\n{user_text}"

                response_text = await ai_engine.generate_response(
                    user_id=user.id,
                    user_text=augmented_text,
                    history=history_for_ai,
                    user_profile=db_user,
                    user_name=user.first_name,
                    provider_name=user_provider,
                    model=user_model,
                )

                # 6. Сохраняем ответ ассистента
                await db_service.save_message(user.id, "assistant", response_text)

                # 6.1 Сохраняем в долговременную память (фоново)
                if memory_service:
                    import asyncio
                    asyncio.create_task(memory_service.store_message(user.id, "user", user_text))
                    asyncio.create_task(memory_service.store_message(user.id, "assistant", response_text))

                # 6.2 Запускаем анализ профиля после каждых 3 сообщений пользователя
                if analyzer_service:
                    try:
                        # Считаем сообщения пользователя в истории
                        user_messages_count = len([m for m in history if m.get('role') == 'user'])
                        if user_messages_count > 0 and user_messages_count % 3 == 0:
                            logger.info(f"Запускаем анализ профиля для {user.id} (после {user_messages_count} сообщений)")
                            # Запускаем в фоне, не ждём результата
                            import asyncio
                            asyncio.create_task(analyzer_service.analyze_user_profile(user.id))
                    except Exception as analyzer_error:
                        logger.warning(f"Не удалось запустить анализ профиля: {analyzer_error}")

                # 7. Форматируем и отправляем ответ
                formatted_response = markdown_to_telegram_html(response_text)
                message_parts = split_message(formatted_response)
                
                for part in message_parts:
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id, 
                            text=part, 
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as send_error:
                        # Если не удалось отправить с HTML, отправляем без форматирования
                        logger.warning(f"Ошибка отправки с HTML: {send_error}, отправляем без форматирования")
                        await context.bot.send_message(chat_id=chat_id, text=response_text[:4096])

            except Exception as e:
                logger.error(f"Ошибка при обработке диалога с {user.id}: {e}")
                await context.bot.send_message(chat_id=chat_id, text="Произошла ошибка при обработке вашего сообщения.")

        async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает входящие текстовые сообщения.
            """
            user = update.effective_user
            if not is_authorized(user.id):
                return

            message_text = update.message.text
            if not message_text:
                return

            logger.info(f"Получено текстовое сообщение от {user.id}")
            await process_dialog_turn(user, update.effective_chat.id, message_text, context)

        async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает входящие голосовые сообщения.
            """
            user = update.effective_user
            voice = update.message.voice

            if not voice:
                return

            logger.info(f"Получено голосовое сообщение от {user.id}")

            try:
                # Уведомляем пользователя, что слушаем
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_voice")

                # Получаем файл
                voice_file = await context.bot.get_file(voice.file_id)

                # Скачиваем файл в память (byte array)
                # python-telegram-bot поддерживает download_to_memory
                # Создаем буфер
                with io.BytesIO() as buffer:
                    await voice_file.download_to_memory(out=buffer)
                    buffer.seek(0)
                    file_bytes = buffer.read()

                # Транскрибируем аудио
                transcribed_text = await ai_engine.transcribe_audio(file_bytes)
                logger.info(f"Транскрипция для {user.id}: {transcribed_text}")

                # Формируем текст сообщения с пометкой
                user_text = f"[Голосовое сообщение]: {transcribed_text}"

                # Запускаем стандартный диалоговый пайплайн
                await process_dialog_turn(user, update.effective_chat.id, user_text, context)

            except Exception as e:
                logger.error(f"Ошибка при обработке голосового сообщения от {user.id}: {e}")
                await update.message.reply_text("Не удалось обработать голосовое сообщение.")

        async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает команду /start.
            """
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

        # Регистрируем обработчик команды /start
        application.add_handler(CommandHandler("start", handle_start))

        async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает команду /help.
            """
            help_text = """📚 <b>Список команд:</b>

/start — начать работу с ботом
/help — показать это сообщение
/myprofile — посмотреть моё досье (навыки, интересы, мечты)
/model — переключить AI-модель (Gemini, Claude, GPT, NVIDIA, MiniMax)
/memory — статистика и поиск по долговременной памяти
/bulk — режим массовой загрузки данных (текст, файлы)
/name — дать мне имя (например: /name Макс)
/correct — исправить ошибку в профиле
/clear — очистить историю диалога

💬 Просто напишите мне сообщение, и я постараюсь помочь!
🎤 Также вы можете отправить голосовое сообщение."""
            await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

        # Регистрируем обработчик команды /help
        application.add_handler(CommandHandler("help", handle_help))

        async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает команду /name — позволяет дать боту имя.
            """
            user = update.effective_user
            logger.info(f"Команда /name от {user.id}")
            
            # Получаем имя из аргументов команды
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
                # Сохраняем имя бота в профиль пользователя
                await db_service.update_user(user.id, {"bot_nickname": new_name})
                
                await update.message.reply_text(
                    f"✅ Отлично! Теперь я буду откликаться на имя <b>{new_name}</b>.\n\n"
                    f"Приятно познакомиться, {user.first_name}! 🤝",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Ошибка сохранения имени бота для {user.id}: {e}")
                await update.message.reply_text("❌ Не удалось сохранить имя. Попробуй ещё раз.")

        # Регистрируем обработчик команды /name
        application.add_handler(CommandHandler("name", handle_name))

        # Регистрируем обработчик команды /help
        application.add_handler(CommandHandler("help", handle_help))

        async def handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает команду /clear — очищает историю диалога.
            """
            user = update.effective_user
            logger.info(f"Команда /clear от {user.id}")
            
            try:
                count = await db_service.clear_messages(user.id)
                await update.message.reply_text(f"✅ История очищена! Удалено сообщений: {count}")
            except Exception as e:
                logger.error(f"Ошибка очистки истории для {user.id}: {e}")
                await update.message.reply_text("❌ Не удалось очистить историю.")

        # Регистрируем обработчик команды /clear
        application.add_handler(CommandHandler("clear", handle_clear))

        async def handle_myprofile(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает команду /myprofile — показывает накопленное досье.
            """
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

        # Регистрируем обработчик команды /myprofile
        application.add_handler(CommandHandler("myprofile", handle_myprofile))

        async def handle_correct(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает команду /correct — исправляет ошибки в профиле.
            Пример: /correct убери что я не люблю Python
            """
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
                import json
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
                    
            except Exception as e:
                logger.error(f"Ошибка исправления профиля для {user.id}: {e}")
                await update.message.reply_text("❌ Произошла ошибка. Попробуй позже.")

        # Регистрируем обработчик команды /correct
        application.add_handler(CommandHandler("correct", handle_correct))

        async def handle_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает команду /model — переключение AI-модели.
            /model — показать текущую и доступные
            /model kimi-k2 — переключить по короткому имени
            /model gemini/gemini-2.5-pro — переключить на конкретную
            """
            from services.ai_engine import (
                DEFAULT_MODELS, OPENAI_COMPATIBLE_PROVIDERS, PROVIDER_MAP,
                NVIDIA_MODELS, GEMINI_MODELS, parse_model_string, create_provider,
            )
            user = update.effective_user
            if not is_authorized(user.id):
                return

            args = context.args

            if not args:
                # Получаем персональную модель пользователя из БД
                user_data = await db_service.get_user(user.id)
                user_selected = user_data.get('selected_model') if user_data else None
                current = user_selected or f"{ai_engine.default_provider_name}/{ai_engine.default_model}"
                suffix = "" if user_selected else " (по умолчанию)"

                lines = [f"⚙️ <b>Твоя модель:</b> <code>{current}</code>{suffix}\n"]

                # Gemini
                lines.append("🔵 <b>Gemini:</b>")
                for short_name, full_name in GEMINI_MODELS.items():
                    marker = " ✅" if current.endswith(full_name) else ""
                    lines.append(f"  <code>/model {short_name}</code>{marker}")

                # NVIDIA NIM models
                lines.append("\n🟢 <b>NVIDIA NIM:</b>")
                for short_name, full_name in NVIDIA_MODELS.items():
                    marker = " ✅" if ai_engine.default_model == full_name else ""
                    lines.append(f"  <code>/model {short_name}</code> → {full_name}{marker}")

                # Anthropic / OpenAI (если ключи есть)
                if os.getenv("ANTHROPIC_API_KEY") and os.getenv("ANTHROPIC_API_KEY") != "placeholder":
                    lines.append("\n🟣 <b>Anthropic:</b>")
                    lines.append("  <code>/model anthropic</code>")
                if os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_API_KEY") != "placeholder":
                    lines.append("\n⚪ <b>OpenAI:</b>")
                    lines.append("  <code>/model openai</code>")

                await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
                return

            model_string = " ".join(args).strip()
            provider_name, model = parse_model_string(model_string)

            # Проверяем, что провайдер существует
            all_providers = set(PROVIDER_MAP.keys()) | set(OPENAI_COMPATIBLE_PROVIDERS.keys())
            if provider_name not in all_providers:
                await update.message.reply_text(
                    f"❌ Неизвестный провайдер или модель: <code>{model_string}</code>\n\n"
                    f"Используй /model чтобы увидеть список.",
                    parse_mode=ParseMode.HTML
                )
                return

            try:
                # Пробуем создать провайдер (проверяем наличие ключа)
                ai_engine.get_provider(provider_name, model)

                # Сохраняем выбор пользователя в БД
                await db_service.update_user(user.id, {"selected_model": f"{provider_name}/{model}"})

                await update.message.reply_text(
                    f"✅ Модель переключена на <code>{provider_name}/{model}</code>",
                    parse_mode=ParseMode.HTML
                )
            except ValueError as e:
                await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.HTML)

        application.add_handler(CommandHandler("model", handle_model))

        async def handle_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает команду /memory — тест и статистика долговременной памяти.
            /memory — показать статистику
            /memory search запрос — поиск по памяти
            """
            user = update.effective_user
            if not is_authorized(user.id):
                return

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
                    role_label = "👤" if r['role'] == 'user' else "🤖"
                    prefix = "[📋 Конспект] " if r.get('summary_block') else ""
                    content_preview = r['content'][:200]
                    text += f"{i}. {role_label} {prefix}{content_preview}\n\n"

                await update.message.reply_text(text, parse_mode=ParseMode.HTML)
            else:
                # Статистика памяти
                try:
                    from google.cloud import firestore as fs
                    memory_ref = memory_service.db.collection('users').document(str(user.id)).collection('memory')

                    # Считаем документы
                    all_docs = await memory_ref.limit(500).get()
                    total = len(all_docs)

                    user_msgs = sum(1 for d in all_docs if d.to_dict().get('role') == 'user')
                    assistant_msgs = sum(1 for d in all_docs if d.to_dict().get('role') == 'assistant')
                    summaries = sum(1 for d in all_docs if d.to_dict().get('summary_block'))
                    with_embedding = sum(1 for d in all_docs if d.to_dict().get('embedding'))

                    text = f"""🧠 <b>Долговременная память</b>

📊 <b>Статистика:</b>
  Всего записей: {total}
  👤 Пользователь: {user_msgs}
  🤖 Ассистент: {assistant_msgs}
  📋 Конспектов: {summaries}
  🔢 С эмбеддингом: {with_embedding}

💡 <b>Команды:</b>
  <code>/memory search запрос</code> — поиск по памяти

ℹ️ Память автоматически сохраняет все сообщения и ищет релевантный контекст при триггерных словах (вспомни, помнишь, мы обсуждали...)"""

                    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.error(f"Ошибка получения статистики памяти для {user.id}: {e}")
                    await update.message.reply_text(f"❌ Ошибка: {e}")

        application.add_handler(CommandHandler("memory", handle_memory))

        async def handle_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает команду /bulk — режим массовой загрузки данных.
            /bulk — включить/выключить режим
            """
            user = update.effective_user
            if not is_authorized(user.id):
                return

            if not memory_service:
                await update.message.reply_text("❌ Memory Service не инициализирован.")
                return

            if user.id in bulk_mode_users:
                # Выключаем режим
                count = bulk_mode_users.pop(user.id, 0)
                await update.message.reply_text(
                    f"📴 <b>Режим загрузки выключен</b>\n\n"
                    f"📊 Загружено записей: {count}\n"
                    f"Все данные сохранены с эмбеддингами и доступны для поиска.\n\n"
                    f"Проверь: /memory",
                    parse_mode=ParseMode.HTML
                )
            else:
                # Включаем режим
                bulk_mode_users[user.id] = 0
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

        application.add_handler(CommandHandler("bulk", handle_bulk))

        async def extract_text_from_file(file_bytes: bytes, file_name: str) -> str | None:
            """Извлекает текст из файла по расширению."""
            ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''

            try:
                if ext == 'txt':
                    return file_bytes.decode('utf-8', errors='replace')

                elif ext == 'pdf':
                    try:
                        from PyPDF2 import PdfReader
                        reader = PdfReader(io.BytesIO(file_bytes))
                        pages = []
                        for page in reader.pages:
                            text = page.extract_text()
                            if text:
                                pages.append(text)
                        return '\n\n'.join(pages) if pages else None
                    except ImportError:
                        logger.warning("PyPDF2 не установлен, PDF не поддерживается")
                        return None

                elif ext == 'docx':
                    try:
                        from docx import Document
                        doc = Document(io.BytesIO(file_bytes))
                        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                        return '\n\n'.join(paragraphs) if paragraphs else None
                    except ImportError:
                        logger.warning("python-docx не установлен, DOCX не поддерживается")
                        return None

                elif ext == 'csv':
                    text = file_bytes.decode('utf-8', errors='replace')
                    return text  # Сохраняем CSV как текст

                elif ext == 'json':
                    data = json.loads(file_bytes.decode('utf-8', errors='replace'))
                    return json.dumps(data, ensure_ascii=False, indent=2)

                else:
                    # Пробуем как текст
                    decoded = file_bytes.decode('utf-8', errors='strict')
                    return decoded
            except Exception as e:
                logger.error(f"Ошибка извлечения текста из {file_name}: {e}")
                return None

        async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает загруженные файлы/документы.
            В bulk-режиме: извлекает текст и сохраняет в память.
            В обычном режиме: извлекает текст и обрабатывает как сообщение.
            """
            user = update.effective_user
            if not is_authorized(user.id):
                return

            document = update.message.document
            if not document:
                return

            file_name = document.file_name or "unknown"
            file_size = document.file_size or 0
            logger.info(f"Получен документ от {user.id}: {file_name} ({file_size} bytes)")

            # Ограничение размера (10MB)
            if file_size > 10 * 1024 * 1024:
                await update.message.reply_text("❌ Файл слишком большой (макс. 10 МБ).")
                return

            try:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

                # Скачиваем файл
                file = await document.get_file()
                with io.BytesIO() as buffer:
                    await file.download_to_memory(out=buffer)
                    buffer.seek(0)
                    file_bytes = buffer.read()

                # Извлекаем текст
                text = await extract_text_from_file(file_bytes, file_name)

                if not text or len(text.strip()) < 5:
                    await update.message.reply_text(
                        f"⚠️ Не удалось извлечь текст из <code>{file_name}</code>.\n"
                        "Поддерживаемые форматы: TXT, PDF, DOCX, CSV, JSON",
                        parse_mode=ParseMode.HTML
                    )
                    return

                caption = update.message.caption or ""

                if user.id in bulk_mode_users:
                    # Bulk-режим: сохраняем в память напрямую
                    if memory_service:
                        import asyncio
                        # Разбиваем длинные тексты на чанки по ~1500 символов
                        chunks = _split_text_to_chunks(text, max_len=1500)
                        for chunk in chunks:
                            content = f"[Файл: {file_name}] {chunk}"
                            asyncio.create_task(memory_service.store_message(user.id, "user", content))

                        bulk_mode_users[user.id] = bulk_mode_users.get(user.id, 0) + len(chunks)
                        await update.message.reply_text(
                            f"✅ <code>{file_name}</code> — {len(chunks)} фрагмент(ов), "
                            f"{len(text)} символов",
                            parse_mode=ParseMode.HTML
                        )
                    else:
                        await update.message.reply_text("❌ Memory Service не доступен.")
                else:
                    # Обычный режим: обрабатываем как текстовое сообщение
                    # Обрезаем для AI (макс ~3000 символов)
                    truncated = text[:3000]
                    user_text = f"[Файл: {file_name}] {caption}\n\n{truncated}" if caption else f"[Файл: {file_name}]\n\n{truncated}"
                    if len(text) > 3000:
                        user_text += f"\n\n... (обрезано, всего {len(text)} символов)"
                    await process_dialog_turn(user, update.effective_chat.id, user_text, context)

            except Exception as e:
                logger.error(f"Ошибка обработки документа от {user.id}: {e}")
                await update.message.reply_text(f"❌ Ошибка обработки файла: {e}")

        def _split_text_to_chunks(text: str, max_len: int = 1500) -> list[str]:
            """Разбивает текст на чанки для эмбеддинга."""
            if len(text) <= max_len:
                return [text]

            chunks = []
            paragraphs = text.split('\n\n')
            current = ""

            for para in paragraphs:
                if len(current) + len(para) + 2 <= max_len:
                    current += para + "\n\n"
                else:
                    if current.strip():
                        chunks.append(current.strip())
                    # Если абзац сам длиннее max_len — нарезаем по предложениям
                    if len(para) > max_len:
                        words = para.split()
                        current = ""
                        for word in words:
                            if len(current) + len(word) + 1 <= max_len:
                                current += word + " "
                            else:
                                if current.strip():
                                    chunks.append(current.strip())
                                current = word + " "
                    else:
                        current = para + "\n\n"

            if current.strip():
                chunks.append(current.strip())

            return chunks if chunks else [text[:max_len]]

        async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает нажатия на inline-кнопки.
            """
            query = update.callback_query
            await query.answer()  # Убираем "часики" на кнопке
            
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

        # Регистрируем обработчик callback query (inline кнопки)
        application.add_handler(CallbackQueryHandler(handle_callback))

        # Регистрируем обработчик текстовых сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Регистрируем обработчик голосовых сообщений
        application.add_handler(MessageHandler(filters.VOICE, handle_voice))

        async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает изображения от пользователя.
            """
            user = update.effective_user
            logger.info(f"Получено фото от {user.id}")
            
            if not ai_engine:
                await update.message.reply_text("❌ Сервис ИИ временно недоступен.")
                return
            
            # Показываем индикатор "печатает"
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            
            try:
                # Получаем самое большое изображение
                photo = update.message.photo[-1]  # Последний элемент = максимальный размер
                file = await photo.get_file()
                
                # Скачиваем изображение
                image_bytes = await file.download_as_bytearray()
                
                # Получаем caption (текст к фото) если есть
                caption = update.message.caption or ""
                
                # Получаем профиль пользователя для контекста
                user_data = await db_service.get_user(user.id)
                user_profile = user_data if user_data else None
                
                # Анализируем изображение
                response_text = await ai_engine.analyze_image(
                    bytes(image_bytes),
                    user_message=caption,
                    user_profile=user_profile,
                    user_name=user.first_name
                )
                
                # Форматируем и отправляем ответ
                formatted_response = markdown_to_telegram_html(response_text)
                await send_long_message(update.message, formatted_response)
                
            except Exception as e:
                logger.error(f"Ошибка обработки фото от {user.id}: {e}")
                await update.message.reply_text("❌ Не удалось обработать изображение.")

        async def send_long_message(message, text: str):
            """Отправляет длинное сообщение, разбивая на части."""
            parts = split_message(text)
            for part in parts:
                try:
                    await message.reply_text(part, parse_mode=ParseMode.HTML)
                except Exception:
                    await message.reply_text(part)

        # Регистрируем обработчик фото
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

        # Регистрируем обработчик документов/файлов
        application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

        # Инициализируем приложение
        await application.initialize()

        logger.info("Telegram Bot Application успешно создано, хендлеры зарегистрированы.")
        return application

    except Exception as e:
        logger.error(f"Ошибка при создании Telegram Bot Application: {e}")
        raise e
