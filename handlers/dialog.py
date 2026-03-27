"""
Обработчик текстовых сообщений — делегирует в DialogPipeline.
"""

import logging

from telegram import Update
from telegram.ext import MessageHandler, filters, ContextTypes

from services.state import authorized

logger = logging.getLogger(__name__)


def register_handlers(app, services: dict):
    """Регистрирует обработчик текстовых сообщений."""
    pipeline = services["pipeline"]

    @authorized
    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает входящие текстовые сообщения."""
        user = update.effective_user

        message_text = update.message.text
        if not message_text:
            return

        logger.info(f"Получено текстовое сообщение от {user.id}")
        state = context.bot_data.get("state")
        await pipeline.process_turn(user, update.effective_chat.id, message_text, context, state)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
