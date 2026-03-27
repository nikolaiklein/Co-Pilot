"""
Обработчик команды /admin — управление ботом (только для владельца).
"""

import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from telegram.constants import ParseMode

from services.state import OWNER_ID

logger = logging.getLogger(__name__)


def register_handlers(app, services: dict):
    """Регистрирует /admin."""
    db_service = services["db"]

    async def handle_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Админ-команды для управления ботом.
        Доступно только владельцу (OWNER_ID).

        Использование:
          /admin add <user_id>     — добавить пользователя
          /admin remove <user_id>  — удалить пользователя
          /admin list              — список разрешённых
          /admin stats             — статистика
        """
        user = update.effective_user
        if user.id != OWNER_ID:
            return  # Молча игнорируем

        state = context.bot_data.get("state")
        args = context.args if context.args else []

        if not args:
            help_text = (
                "🔧 <b>Админ-панель</b>\n\n"
                "Команды:\n"
                "<code>/admin add {user_id}</code> — добавить пользователя\n"
                "<code>/admin remove {user_id}</code> — удалить пользователя\n"
                "<code>/admin list</code> — список разрешённых\n"
                "<code>/admin stats</code> — статистика\n"
            )
            await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
            return

        action = args[0].lower()
        allowed_users = state.allowed_users if state else set()

        if action == "add" and len(args) >= 2:
            try:
                new_uid = int(args[1])
                if state:
                    state.add_user(new_uid)
                # Персистим в Firestore
                saved = await db_service.save_allowed_users(state.allowed_users if state else set())
                persist_status = "💾 Сохранено в Firestore." if saved else "⚠️ Не удалось сохранить в Firestore!"
                logger.info(f"Админ добавил пользователя {new_uid}. Текущий список: {allowed_users}")
                await update.message.reply_text(
                    f"✅ Пользователь <code>{new_uid}</code> добавлен.\n"
                    f"Всего разрешённых: {len(allowed_users)}\n"
                    f"{persist_status}",
                    parse_mode=ParseMode.HTML
                )
            except ValueError:
                await update.message.reply_text("❌ Неверный ID. Укажите числовой Telegram user_id.")

        elif action == "remove" and len(args) >= 2:
            try:
                rm_uid = int(args[1])
                if rm_uid == OWNER_ID:
                    await update.message.reply_text("❌ Нельзя удалить владельца.")
                    return
                if state:
                    state.remove_user(rm_uid)
                # Персистим в Firestore
                saved = await db_service.save_allowed_users(state.allowed_users if state else set())
                persist_status = "💾 Сохранено в Firestore." if saved else "⚠️ Не удалось сохранить в Firestore!"
                logger.info(f"Админ удалил пользователя {rm_uid}. Текущий список: {allowed_users}")
                await update.message.reply_text(
                    f"✅ Пользователь <code>{rm_uid}</code> удалён.\n"
                    f"Всего разрешённых: {len(allowed_users)}\n"
                    f"{persist_status}",
                    parse_mode=ParseMode.HTML
                )
            except ValueError:
                await update.message.reply_text("❌ Неверный ID. Укажите числовой Telegram user_id.")

        elif action == "list":
            if not allowed_users:
                await update.message.reply_text("🔓 Режим открытого доступа (ALLOWED_USERS пуст — все допущены).")
            else:
                lines = []
                for uid in sorted(allowed_users):
                    user_data = await db_service.get_user(uid)
                    if user_data:
                        name = user_data.get('first_name', '')
                        username = user_data.get('username', '')
                        label = f"{name} (@{username})" if username else name
                        lines.append(f"• <code>{uid}</code> — {label}")
                    else:
                        lines.append(f"• <code>{uid}</code>")
                users_list = "\n".join(lines)
                await update.message.reply_text(
                    f"👥 <b>Разрешённые пользователи ({len(allowed_users)}):</b>\n{users_list}",
                    parse_mode=ParseMode.HTML
                )

        elif action == "stats":
            total_allowed = len(allowed_users) if allowed_users else "∞ (все)"
            bulk_active = state.bulk_active_count if state else 0
            await update.message.reply_text(
                f"📊 <b>Статистика</b>\n\n"
                f"Разрешённых пользователей: {total_allowed}\n"
                f"В режиме bulk: {bulk_active}",
                parse_mode=ParseMode.HTML
            )

        else:
            await update.message.reply_text(
                "❓ Неизвестная команда. Используйте /admin без аргументов для справки."
            )

    app.add_handler(CommandHandler("admin", handle_admin))
