"""
Обработчики команды /model и связанных колбэков (mc:, ms:, mswitch:, mback).
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

from services.ai_engine import OPENAI_COMPATIBLE_PROVIDERS, PROVIDER_MAP, parse_model_string
from services.model_data import (
    DEFAULT_MODELS, NVIDIA_MODELS, GEMINI_MODELS, MODEL_HINTS, FAMILY_ORDER,
    get_model_meta,
)
from services.state import authorized
from services.dialog_pipeline import resolve_and_switch_model

logger = logging.getLogger(__name__)


def register_handlers(app, services: dict):
    """Регистрирует /model и связанные колбэки."""
    db_service = services["db"]
    ai_engine = services["ai"]
    model_catalog = services.get("catalog")

    async def _get_current_model(user_id: int) -> str:
        """Возвращает текущую модель пользователя (litellm/model или provider/model)."""
        user_data = await db_service.get_user(user_id)
        return (user_data.get('selected_model') if user_data else None) or \
               f"{ai_engine.default_provider_name}/{ai_engine.default_model}"

    async def _build_categories_keyboard(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        """Строит клавиатуру категорий моделей (первый экран /model)."""
        current = await _get_current_model(user_id)

        if not model_catalog or not model_catalog.is_available:
            lines = [f"⚙️ <b>Твоя модель:</b> <code>{current}</code>\n"]
            lines.append("🔵 <b>Gemini:</b>")
            for short_name in GEMINI_MODELS:
                lines.append(f"  <code>/model {short_name}</code>")
            lines.append("\n🟢 <b>NVIDIA NIM:</b>")
            for short_name in NVIDIA_MODELS:
                lines.append(f"  <code>/model {short_name}</code>")
            return "\n".join(lines), None

        groups = await model_catalog.get_models_grouped()
        text = f"⚙️ <b>Твоя модель:</b> <code>{current}</code>\n\n🤖 <b>Выбери категорию:</b>"

        buttons = []
        for family in FAMILY_ORDER:
            if family not in groups:
                continue
            models = groups[family]
            # Получаем emoji для семейства
            meta = get_model_meta(models[0])
            emoji = meta.get("emoji", "⬜")
            count = len(models)
            # Проверяем, есть ли текущая модель в этой категории
            current_model = current.split("/", 1)[-1] if "/" in current else current
            has_current = any(m == current_model for m in models)
            check = " ✅" if has_current else ""
            btn_text = f"{emoji} {family} ({count}){check}"
            # callback data: mc:Family (mc = model category)
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"mc:{family}")])

        # Добавить оставшиеся группы не в FAMILY_ORDER
        for family, models in groups.items():
            if family in FAMILY_ORDER:
                continue
            meta = get_model_meta(models[0])
            emoji = meta.get("emoji", "⬜")
            count = len(models)
            buttons.append([InlineKeyboardButton(f"{emoji} {family} ({count})", callback_data=f"mc:{family}")])

        return text, InlineKeyboardMarkup(buttons)

    async def _build_models_keyboard(user_id: int, family: str) -> tuple[str, InlineKeyboardMarkup]:
        """Строит клавиатуру моделей внутри категории (второй экран)."""
        current = await _get_current_model(user_id)
        current_model = current.split("/", 1)[-1] if "/" in current else current

        if not model_catalog:
            return "❌ Каталог моделей недоступен.", InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="mback")]])

        groups = await model_catalog.get_models_grouped()
        models = groups.get(family, [])

        if not models:
            return f"❌ Категория {family} пуста.", InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="mback")]])

        meta = get_model_meta(models[0])
        emoji = meta.get("emoji", "⬜")
        text = f"{emoji} <b>{family}</b> — выбери модель:"

        buttons = []
        for model_id in models:
            hint = MODEL_HINTS.get(model_id, "")
            check = " ✅" if model_id == current_model else ""
            label = f"{model_id}{check}"
            if hint:
                label = f"{model_id} {hint}{check}"
            # callback data: ms:model_id (ms = model select)
            # Telegram limit: 64 bytes. model_id + prefix should fit
            cb_data = f"ms:{model_id}"
            if len(cb_data.encode('utf-8')) > 64:
                cb_data = f"ms:{model_id[:55]}"
            buttons.append([InlineKeyboardButton(label, callback_data=cb_data)])

        buttons.append([InlineKeyboardButton("« Назад", callback_data="mback")])
        return text, InlineKeyboardMarkup(buttons)

    # --- /model command ---
    @authorized
    async def handle_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Обрабатывает команду /model — переключение AI-модели.
        /model — показать inline-клавиатуру с категориями
        /model kimi-k2 — переключить по короткому имени (power-user shortcut)
        /model litellm/claude-opus-4.6 — переключить на конкретную LiteLLM модель
        """
        user = update.effective_user

        args = context.args

        if not args:
            # Inline keyboard UI
            text, keyboard = await _build_categories_keyboard(user.id)
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            return

        # Power-user shortcut: /model model-name
        model_string = " ".join(args).strip()

        # Сначала проверяем, есть ли модель в LiteLLM каталоге
        if model_catalog and model_catalog.is_available:
            models = await model_catalog.get_models()
            if model_string in models:
                # Прямое совпадение с LiteLLM моделью
                try:
                    await db_service.update_user(user.id, {"selected_model": f"litellm/{model_string}"})
                except Exception as db_err:
                    logger.error(f"Ошибка сохранения модели для {user.id}: {db_err}")
                    await update.message.reply_text("⚠️ Не удалось сохранить выбор модели. Попробуй ещё раз.")
                    return
                await update.message.reply_text(
                    f"✅ Модель: <code>litellm/{model_string}</code>",
                    parse_mode=ParseMode.HTML
                )
                return

        # Fallback: старый парсинг (gemini-3-flash, nvidia/kimi-k2 и т.д.)
        provider_name, model = parse_model_string(model_string)

        all_providers = set(PROVIDER_MAP.keys()) | set(OPENAI_COMPATIBLE_PROVIDERS.keys())
        if provider_name not in all_providers:
            await update.message.reply_text(
                f"❌ Неизвестная модель: <code>{model_string}</code>\n\n"
                f"Используй /model для списка.",
                parse_mode=ParseMode.HTML
            )
            return

        try:
            ai_engine.get_provider(provider_name, model)
            await db_service.update_user(user.id, {"selected_model": f"{provider_name}/{model}"})
            await update.message.reply_text(
                f"✅ Модель: <code>{provider_name}/{model}</code>",
                parse_mode=ParseMode.HTML
            )
        except ValueError as e:
            await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.HTML)
        except Exception as db_err:
            logger.error(f"Ошибка сохранения модели для {user.id}: {db_err}")
            await update.message.reply_text("⚠️ Не удалось сохранить выбор модели. Попробуй ещё раз.")

    app.add_handler(CommandHandler("model", handle_model))

    # --- Model callbacks (mc:, ms:, mback) ---
    async def handle_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает колбэки выбора модели: mc:*, ms:*, mback."""
        query = update.callback_query
        await query.answer()

        user = query.from_user
        data = query.data
        logger.info(f"Callback {data} от {user.id}")

        # Auth check for callbacks
        state = context.bot_data.get("state")
        if state and not state.is_authorized(user.id):
            return

        if data.startswith("mc:"):
            family = data[3:]
            if family == "categories":
                # Переход к полному каталогу категорий
                text, keyboard = await _build_categories_keyboard(user.id)
            else:
                # Категория выбрана — показываем модели внутри
                text, keyboard = await _build_models_keyboard(user.id, family)
        elif data == "mback":
            # Назад к категориям
            text, keyboard = await _build_categories_keyboard(user.id)
        else:
            # ms: — модель выбрана
            model_id = data[3:]
            # Проверяем, что модель ещё есть в каталоге
            if model_catalog and model_catalog.is_available:
                models = await model_catalog.get_models()
                if model_id not in models:
                    text = f"⚠️ Модель <code>{model_id}</code> больше недоступна.\n\nВыбери другую:"
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("« Все модели", callback_data="mback")]
                    ])
                    try:
                        await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
                    except Exception:
                        await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
                    return

            try:
                await db_service.update_user(user.id, {"selected_model": f"litellm/{model_id}"})
            except Exception as db_err:
                logger.error(f"Ошибка сохранения модели для {user.id}: {db_err}")
                text = "⚠️ Не удалось сохранить выбор модели. Попробуй ещё раз."
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("« Все модели", callback_data="mback")]
                ])
                try:
                    await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
                except Exception:
                    await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
                return
            logger.info(f"Пользователь {user.id} выбрал модель: litellm/{model_id}")
            meta = get_model_meta(model_id)
            hint = MODEL_HINTS.get(model_id, "")
            hint_str = f" ({hint})" if hint else ""
            text = f"✅ Модель переключена:\n\n{meta.get('emoji', '')} <code>{model_id}</code>{hint_str}"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("« Все модели", callback_data="mback")]
            ])

        try:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except Exception:
            await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    app.add_handler(CallbackQueryHandler(handle_model_callback, pattern=r'^(mc:|ms:|mback)'))

    # --- Recommendation switch callbacks (mswitch:) ---
    async def handle_mswitch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает колбэки рекомендации модели: mswitch:*."""
        query = update.callback_query
        await query.answer()

        user = query.from_user
        data = query.data
        logger.info(f"Callback {data} от {user.id}")

        # Auth check for callbacks
        state = context.bot_data.get("state")
        if state and not state.is_authorized(user.id):
            return

        choice = data[8:]  # after "mswitch:"

        if choice == "keep":
            try:
                await query.message.edit_text(
                    "👌 Оставляю текущую модель.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                await query.message.reply_text("👌 Оставляю текущую модель.")
            return

        # Маппинг алиасов для кнопок рекомендации
        _recommend_aliases = {
            "claude": ("anthropic", "claude-sonnet-4-20250514"),
            "gpt": ("openai", "gpt-4o"),
        }

        if choice in _recommend_aliases:
            prov, mdl = _recommend_aliases[choice]
            selected_str = f"{prov}/{mdl}"
        else:
            # Пробуем через стандартный резолвер
            resolved = await resolve_and_switch_model(user.id, choice, db_service, model_catalog, ai_engine)
            if resolved:
                meta = get_model_meta(choice)
                try:
                    await query.message.edit_text(
                        f"✅ Модель переключена: {meta.get('emoji', '')} <b>{meta.get('label', choice)}</b>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    await query.message.reply_text(
                        f"✅ Модель переключена: {meta.get('emoji', '')} <b>{meta.get('label', choice)}</b>",
                        parse_mode=ParseMode.HTML,
                    )
                return
            else:
                try:
                    await query.message.edit_text(
                        f"⚠️ Модель <code>{choice}</code> недоступна.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    await query.message.reply_text(
                        f"⚠️ Модель <code>{choice}</code> недоступна.",
                        parse_mode=ParseMode.HTML,
                    )
                return

        # Для алиасов (claude, gpt) — сохраняем напрямую
        try:
            await db_service.update_user(user.id, {"selected_model": selected_str})
        except Exception as db_err:
            logger.error(f"Ошибка сохранения модели для {user.id}: {db_err}")
            try:
                await query.message.edit_text(
                    "⚠️ Не удалось сохранить выбор модели. Попробуй ещё раз.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            return

        meta = get_model_meta(choice)
        logger.info(f"Пользователь {user.id} выбрал рекомендованную модель: {selected_str}")
        try:
            await query.message.edit_text(
                f"✅ Модель переключена: {meta.get('emoji', '')} <b>{meta.get('label', choice)}</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            await query.message.reply_text(
                f"✅ Модель переключена: {meta.get('emoji', '')} <b>{meta.get('label', choice)}</b>",
                parse_mode=ParseMode.HTML,
            )

    app.add_handler(CallbackQueryHandler(handle_mswitch_callback, pattern=r'^mswitch:'))
