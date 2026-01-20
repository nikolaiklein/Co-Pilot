import os
import logging
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

        # Определяем обработчик сообщений внутри функции create_bot_app,
        # чтобы иметь доступ к db_service и ai_engine через замыкание.
        # Альтернатива: передавать сервисы через bot_data.

        async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """
            Обрабатывает входящие текстовые сообщения.
            """
            user = update.effective_user
            message_text = update.message.text

            if not message_text:
                return

            logger.info(f"Получено сообщение от {user.id}: {message_text}")

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
                await db_service.save_message(user.id, "user", message_text)

                # 3. Отправляем действие "печатает" (typing), чтобы пользователь видел активность
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

                # 4. Получаем историю сообщений для контекста (последние 10-20 сообщений)
                history = await db_service.get_last_messages(user.id, limit=20)

                # 5. Генерируем ответ с помощью ИИ
                # Передаем историю, исключая текущее сообщение, так как мы его уже добавили в БД,
                # но ai_engine.generate_response ожидает, что последнее сообщение пользователя
                # передается отдельным аргументом user_text.
                # Поэтому из history нужно исключить только что добавленное сообщение, если оно туда попало.
                # Метод get_last_messages сортирует от старых к новым.
                # Если firestore работает быстро, наше сообщение уже там.
                # Проверим: generate_response добавляет user_text в конец contents.
                # Значит, в history не должно быть дубля этого сообщения.

                # Простой вариант: берем историю, если последнее сообщение совпадает с текущим - убираем его.
                if history and history[-1]['content'] == message_text and history[-1]['role'] == 'user':
                     history_for_ai = history[:-1]
                else:
                     history_for_ai = history

                response_text = await ai_engine.generate_response(user.id, message_text, history_for_ai)

                # 6. Сохраняем ответ ассистента в БД
                await db_service.save_message(user.id, "assistant", response_text)

                # 7. Отправляем ответ пользователю
                await update.message.reply_text(response_text)

            except Exception as e:
                logger.error(f"Ошибка при обработке сообщения от {user.id}: {e}")
                await update.message.reply_text("Произошла ошибка при обработке вашего сообщения. Попробуйте позже.")

        # Регистрируем обработчик текстовых сообщений (исключая команды)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Инициализируем приложение
        await application.initialize()

        logger.info("Telegram Bot Application успешно создано, хендлеры зарегистрированы.")
        return application

    except Exception as e:
        logger.error(f"Ошибка при создании Telegram Bot Application: {e}")
        raise e
