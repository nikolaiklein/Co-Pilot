"""
Обработчики команды /vault и связанных колбэков (vault:*).
"""

import html
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

from services.state import authorized

logger = logging.getLogger(__name__)


def register_handlers(app, services: dict):
    """Регистрирует /vault и связанные колбэки."""
    db_service = services["db"]

    # --- /vault command ---
    @authorized
    async def handle_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает команду /vault."""
        user = update.effective_user

        args = context.args

        # /vault save <title> — быстрое сохранение последнего ответа
        if args and args[0].lower() == "save":
            title = " ".join(args[1:]).strip() if len(args) > 1 else ""
            if not title:
                await update.message.reply_text(
                    "⚠️ Укажи заголовок: <code>/vault save Название</code>",
                    parse_mode=ParseMode.HTML,
                )
                return

            # Берём последнее сообщение ассистента
            history = await db_service.get_last_messages(user.id, limit=5)
            last_assistant = None
            for msg in reversed(history):
                if msg.get('role') == 'assistant':
                    last_assistant = msg.get('content', '')
                    break

            if not last_assistant:
                await update.message.reply_text("⚠️ Нет недавних ответов для сохранения.")
                return

            try:
                doc_id = await db_service.vault_save(
                    user.id, title, last_assistant, item_type="note"
                )
                preview = last_assistant[:100] + "..." if len(last_assistant) > 100 else last_assistant
                await update.message.reply_text(
                    f"✅ Сохранено в хранилище\n\n"
                    f"<b>{html.escape(title)}</b>\n"
                    f"<i>{html.escape(preview)}</i>",
                    parse_mode=ParseMode.HTML,
                )
            except ValueError as e:
                await update.message.reply_text(f"⚠️ {e}")
            except Exception as e:
                logger.error(f"Vault save error for {user.id}: {e}")
                await update.message.reply_text("⚠️ Не удалось сохранить. Попробуй позже.")
            return

        # /vault — показать список или empty state
        try:
            items = await db_service.vault_list(user.id, limit=10)
        except Exception as e:
            logger.error(f"Vault list error for {user.id}: {e}")
            await update.message.reply_text("⚠️ Ошибка загрузки хранилища.")
            return

        if not items:
            await update.message.reply_text(
                "📦 <b>Хранилище пусто</b>\n\n"
                "Здесь можно сохранять промпты, идеи и заметки.\n\n"
                "👉 <code>/vault save Название</code> — сохранить последний ответ\n"
                "👉 Скажи мне «сохрани это как промпт» — я помогу\n"
                "👉 <code>Запиши идею: текст идеи</code> — быстрая заметка",
                parse_mode=ParseMode.HTML,
            )
            return

        # Отображаем список
        type_emoji = {"prompt": "📝", "idea": "💡", "note": "📌"}
        lines = ["📦 <b>Твоё хранилище</b>\n"]
        buttons = []
        for item in items:
            emoji = type_emoji.get(item.get('type', 'note'), '📌')
            title = item.get('title', 'Без названия')[:40]
            date_str = ""
            if item.get('created_at'):
                try:
                    dt = datetime.fromisoformat(item['created_at'].replace('+00:00', ''))
                    date_str = dt.strftime("%d.%m")
                except Exception:
                    pass
            preview = (item.get('content', '')[:50] + "...") if len(item.get('content', '')) > 50 else item.get('content', '')
            lines.append(f"{emoji} <b>{html.escape(title)}</b> {date_str}\n<i>{html.escape(preview)}</i>\n")
            buttons.append([InlineKeyboardButton(
                f"{emoji} {title}",
                callback_data=f"vault:view:{item['id'][:20]}"
            )])

        # Кнопка пагинации если 10 элементов (может быть ещё)
        if len(items) == 10:
            last_id = items[-1]['id']
            buttons.append([InlineKeyboardButton("Ещё ▶️", callback_data=f"vault:page:{last_id[:20]}")])

        text = "\n".join(lines)
        keyboard = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    app.add_handler(CommandHandler("vault", handle_vault))

    # --- Vault callbacks ---
    async def handle_vault_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает колбэки vault:*."""
        query = update.callback_query
        await query.answer()

        user = query.from_user
        data = query.data
        logger.info(f"Callback {data} от {user.id}")

        # Auth check for callbacks
        state = context.bot_data.get("state")
        if state and not state.is_authorized(user.id):
            return

        parts = data.split(":", 2)
        action = parts[1] if len(parts) > 1 else ""
        param = parts[2] if len(parts) > 2 else ""

        if action == "view" and param:
            # Показать полное содержимое элемента
            item = await db_service.vault_get(user.id, param)
            if not item:
                try:
                    await query.message.edit_text("⚠️ Элемент не найден.")
                except Exception:
                    await query.message.reply_text("⚠️ Элемент не найден.")
                return

            type_emoji = {"prompt": "📝", "idea": "💡", "note": "📌"}
            emoji = type_emoji.get(item.get('type', 'note'), '📌')
            title = item.get('title', 'Без названия')
            content = item.get('content', '')
            # Обрезаем контент если слишком длинный для Telegram
            if len(content) > 3000:
                content = content[:3000] + "\n\n<i>... (обрезано)</i>"

            text = (
                f"{emoji} <b>{html.escape(title)}</b>\n\n"
                f"{html.escape(content)}"
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("❌ Удалить", callback_data=f"vault:del:{param}"),
                    InlineKeyboardButton("« Назад", callback_data="vault:back"),
                ]
            ])
            try:
                await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            except Exception:
                await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

        elif action == "del" and param:
            # Подтверждение удаления
            item = await db_service.vault_get(user.id, param)
            title = item.get('title', 'элемент') if item else 'элемент'
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("❌ Да, удалить", callback_data=f"vault:confirm_del:{param}"),
                    InlineKeyboardButton("« Отмена", callback_data="vault:back"),
                ]
            ])
            try:
                await query.message.edit_text(
                    f"Удалить <b>{html.escape(title)}</b>?",
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            except Exception:
                pass

        elif action == "confirm_del" and param:
            # Удаление
            try:
                deleted = await db_service.vault_delete(user.id, param)
                if deleted:
                    text = "✅ Удалено."
                else:
                    text = "⚠️ Элемент уже удалён."
            except Exception as e:
                logger.error(f"Vault delete error for {user.id}: {e}")
                text = "⚠️ Ошибка удаления."
            try:
                await query.message.edit_text(text)
            except Exception:
                await query.message.reply_text(text)

        elif action == "page" and param:
            # Пагинация — следующая страница
            try:
                items = await db_service.vault_list(user.id, limit=10, cursor=param)
            except Exception as e:
                logger.error(f"Vault page error: {e}")
                return

            if not items:
                try:
                    await query.message.edit_text("📦 Больше элементов нет.")
                except Exception:
                    pass
                return

            type_emoji = {"prompt": "📝", "idea": "💡", "note": "📌"}
            lines = ["📦 <b>Хранилище (продолжение)</b>\n"]
            buttons = []
            for item in items:
                emoji = type_emoji.get(item.get('type', 'note'), '📌')
                t = item.get('title', 'Без названия')[:40]
                preview = (item.get('content', '')[:50] + "...") if len(item.get('content', '')) > 50 else item.get('content', '')
                lines.append(f"{emoji} <b>{html.escape(t)}</b>\n<i>{html.escape(preview)}</i>\n")
                buttons.append([InlineKeyboardButton(
                    f"{emoji} {t}",
                    callback_data=f"vault:view:{item['id'][:20]}"
                )])

            if len(items) == 10:
                last_id = items[-1]['id']
                buttons.append([InlineKeyboardButton("Ещё ▶️", callback_data=f"vault:page:{last_id[:20]}")])

            text = "\n".join(lines)
            keyboard = InlineKeyboardMarkup(buttons)
            try:
                await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            except Exception:
                await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

        elif action == "back":
            # Назад к списку
            try:
                items = await db_service.vault_list(user.id, limit=10)
            except Exception:
                return

            if not items:
                try:
                    await query.message.edit_text("📦 Хранилище пусто.")
                except Exception:
                    pass
                return

            type_emoji = {"prompt": "📝", "idea": "💡", "note": "📌"}
            lines = ["📦 <b>Твоё хранилище</b>\n"]
            buttons = []
            for item in items:
                emoji = type_emoji.get(item.get('type', 'note'), '📌')
                t = item.get('title', 'Без названия')[:40]
                preview = (item.get('content', '')[:50] + "...") if len(item.get('content', '')) > 50 else item.get('content', '')
                lines.append(f"{emoji} <b>{html.escape(t)}</b>\n<i>{html.escape(preview)}</i>\n")
                buttons.append([InlineKeyboardButton(
                    f"{emoji} {t}",
                    callback_data=f"vault:view:{item['id'][:20]}"
                )])

            if len(items) == 10:
                last_id = items[-1]['id']
                buttons.append([InlineKeyboardButton("Ещё ▶️", callback_data=f"vault:page:{last_id[:20]}")])

            text = "\n".join(lines)
            keyboard = InlineKeyboardMarkup(buttons)
            try:
                await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            except Exception:
                await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

        elif action == "save_confirm" and param:
            # Подтверждение сохранения из диалога (param = doc_id уже сохранённого)
            try:
                await query.message.edit_text("✅ Сохранено в хранилище.")
            except Exception:
                pass

        elif action == "save_cancel":
            try:
                await query.message.edit_text("👌 Не сохраняю.")
            except Exception:
                pass

    app.add_handler(CallbackQueryHandler(handle_vault_callback, pattern=r'^vault:'))
