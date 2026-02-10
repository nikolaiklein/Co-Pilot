import os
import logging
import io
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram import Update
from services.db import DatabaseService

# Настройка логирования
logger = logging.getLogger(__name__)

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

                # 7. Отправляем ответ
                await context.bot.send_message(chat_id=chat_id, text=response_text)

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
