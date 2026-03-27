"""
Тонкий оркестратор Telegram-бота.
Создаёт Application, собирает services dict, регистрирует хендлеры из модулей.
"""

import os
import logging

from telegram import Update
from telegram.ext import Application, ContextTypes

from services.db import DatabaseService
from services.state import BotState
from services.dialog_pipeline import DialogPipeline

# Реэкспорт утилит форматирования для обратной совместимости
# (используется в services/dialog_pipeline.py)
from services.formatting import markdown_to_telegram_html, split_message  # noqa: F401

logger = logging.getLogger(__name__)


async def create_bot_app(
    db_service: DatabaseService,
    ai_engine,
    analyzer_service=None,
    memory_service=None,
    model_catalog=None,
    graph=None,
) -> Application:
    """
    Создаёт и настраивает приложение Telegram-бота.
    Регистрирует обработчики из handler-модулей.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN не найден в переменных окружения.")
        return None

    # 1. Список разрешённых пользователей (Firestore -> fallback на env var)
    allowed_users = set()
    firestore_users = await db_service.get_allowed_users() if db_service else None
    if firestore_users is not None:
        allowed_users = firestore_users
        logger.info(f"Авторизация загружена из Firestore. Разрешённые пользователи: {allowed_users}")
    else:
        allowed_users_str = os.getenv("ALLOWED_USERS", "")
        if allowed_users_str.strip():
            allowed_users = {int(uid.strip()) for uid in allowed_users_str.split(",") if uid.strip()}
            if db_service and allowed_users:
                await db_service.save_allowed_users(allowed_users)
                logger.info(f"Мигрировали ALLOWED_USERS из env в Firestore: {allowed_users}")
        if allowed_users:
            logger.info(f"Авторизация из env. Разрешённые пользователи: {allowed_users}")

    try:
        # 2. Создаём Application
        application = Application.builder().token(token).build()

        # 3. Создаём BotState и DialogPipeline
        state = BotState(allowed_users)
        pipeline = DialogPipeline(db_service, ai_engine, memory_service, analyzer_service, model_catalog, graph=graph)

        # 4. Сохраняем в bot_data
        application.bot_data["state"] = state

        # 5. Собираем services dict
        services = {
            "db": db_service,
            "ai": ai_engine,
            "memory": memory_service,
            "analyzer": analyzer_service,
            "catalog": model_catalog,
            "pipeline": pipeline,
            "state": state,
            "allowed_users": allowed_users,
        }

        # 6. Регистрируем хендлеры из модулей
        from handlers import commands, model_commands, vault_commands, admin, media, dialog

        commands.register_handlers(application, services)
        model_commands.register_handlers(application, services)
        vault_commands.register_handlers(application, services)
        admin.register_handlers(application, services)
        media.register_handlers(application, services)
        dialog.register_handlers(application, services)

        # 7. Глобальный обработчик ошибок
        async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            """Логирует ошибки и уведомляет пользователя."""
            logger.error(f"Исключение при обработке апдейта: {context.error}", exc_info=context.error)
            if isinstance(update, Update) and update.effective_chat:
                try:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="⚠️ Произошла внутренняя ошибка. Попробуйте ещё раз или напишите /start."
                    )
                except Exception:
                    logger.error("Не удалось отправить сообщение об ошибке пользователю.")

        application.add_error_handler(error_handler)

        # 8. Инициализируем и возвращаем
        await application.initialize()
        logger.info("Telegram Bot Application успешно создано, хендлеры зарегистрированы.")
        return application

    except Exception as e:
        logger.error(f"Ошибка при создании Telegram Bot Application: {e}")
        raise e
