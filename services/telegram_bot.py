import os
import logging
import io
import re
import html
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram import Update
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

async def create_bot_app(db_service: DatabaseService, ai_engine) -> Application:
    """
    Создает и настраивает приложение Telegram бота.
    Регистрирует обработчики сообщений.

    Args:
        db_service (DatabaseService): Инициализированный сервис базы данных.
        ai_engine (AIEngine): Инициализированный сервис ИИ.

    Returns:
        Application: Настроенное приложение python-telegram-bot.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN не найден в переменных окружения.")
        return None

    try:
        # Создаем билдер приложения
        builder = Application.builder().token(token)
        application = builder.build()

        # Общая функция для обработки логики диалога (используется для текста и голоса)
        async def process_dialog_turn(user, chat_id, user_text, context):
            try:
                # 1. Получаем или создаем пользователя в БД
                user_data = {
                    "id": user.id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "language_code": user.language_code,
                    "is_bot": user.is_bot
                }
                await db_service.get_or_create_user(user.id, user_data)

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

                # 5. Генерируем ответ
                response_text = await ai_engine.generate_response(user.id, user_text, history_for_ai)

                # 6. Сохраняем ответ ассистента
                await db_service.save_message(user.id, "assistant", response_text)

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
            await db_service.get_or_create_user(user.id, user_data)
            
            welcome_message = f"Привет, {user.first_name}! 👋\n\nЯ ваш AI-ассистент. Напишите мне что-нибудь, и я постараюсь помочь!"
            await update.message.reply_text(welcome_message)

        # Регистрируем обработчик команды /start
        application.add_handler(CommandHandler("start", handle_start))

        async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает команду /help.
            """
            help_text = """📚 **Список команд:**

/start — начать работу с ботом
/help — показать это сообщение
/clear — очистить историю диалога

💬 Просто напишите мне сообщение, и я постараюсь помочь!
🎤 Также вы можете отправить голосовое сообщение."""
            await update.message.reply_text(help_text)

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

        # Регистрируем обработчик текстовых сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Регистрируем обработчик голосовых сообщений
        application.add_handler(MessageHandler(filters.VOICE, handle_voice))

        # Инициализируем приложение
        await application.initialize()

        logger.info("Telegram Bot Application успешно создано, хендлеры зарегистрированы.")
        return application

    except Exception as e:
        logger.error(f"Ошибка при создании Telegram Bot Application: {e}")
        raise e
