import os
import logging
from telegram.ext import Application
from telegram import Update

# Настройка логирования
logger = logging.getLogger(__name__)

async def create_bot_app() -> Application:
    """
    Создает и настраивает приложение Telegram бота.

    Returns:
        Application: Настроенное приложение python-telegram-bot.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN не найден в переменных окружения.")
        # Для локального запуска без токена, чтобы приложение не падало сразу,
        # но бот работать не будет. В продакшене это критическая ошибка.
        # В данном случае просто вернем None или можно кинуть ошибку.
        # Решим вернуть None, а вызывающий код должен это обработать.
        return None

    try:
        # Создаем билдер приложения
        builder = Application.builder().token(token)

        # Здесь можно добавить persistence, если нужно, но у нас Firestore
        # builder.persistence(...)

        # Строим приложение
        application = builder.build()

        # Инициализируем приложение (важно для работы в режиме webhook без polling)
        await application.initialize()

        logger.info("Telegram Bot Application успешно создано и инициализировано.")
        return application

    except Exception as e:
        logger.error(f"Ошибка при создании Telegram Bot Application: {e}")
        raise e
