"""
Обработчики медиа: голосовые сообщения, документы, фотографии.
"""

import io
import logging

from telegram import Update
from telegram.ext import MessageHandler, filters, ContextTypes

from services.ai_engine import parse_model_string
from services.async_utils import fire_and_forget
from services.formatting import (
    markdown_to_telegram_html,
    send_long_message,
    extract_text_from_file,
    _split_text_to_chunks,
)
from services.state import authorized

logger = logging.getLogger(__name__)


def register_handlers(app, services: dict):
    """Регистрирует обработчики медиа."""
    db_service = services["db"]
    ai_engine = services["ai"]
    memory_service = services.get("memory")
    pipeline = services["pipeline"]

    # --- Голосовые сообщения ---
    @authorized
    async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает входящие голосовые сообщения."""
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
            with io.BytesIO() as buffer:
                await voice_file.download_to_memory(out=buffer)
                buffer.seek(0)
                file_bytes = buffer.read()

            # Транскрибируем аудио
            transcribed_text = await ai_engine.transcribe_audio(file_bytes)
            logger.debug(f"Транскрипция для {user.id}: {len(transcribed_text)} символов")

            # Формируем текст сообщения с пометкой
            user_text = f"[Голосовое сообщение]: {transcribed_text}"

            # Запускаем стандартный диалоговый пайплайн
            state = context.bot_data.get("state")
            await pipeline.process_turn(user, update.effective_chat.id, user_text, context, state)

        except Exception as e:
            logger.error(f"Ошибка при обработке голосового сообщения от {user.id}: {e}")
            await update.message.reply_text("Не удалось обработать голосовое сообщение.")

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # --- Документы/файлы ---
    @authorized
    async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Обрабатывает загруженные файлы/документы.
        В bulk-режиме: извлекает текст и сохраняет в память.
        В обычном режиме: извлекает текст и обрабатывает как сообщение.
        """
        user = update.effective_user

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
                    parse_mode="HTML"
                )
                return

            caption = update.message.caption or ""
            state = context.bot_data.get("state")

            if state and state.is_bulk(user.id):
                # Bulk-режим: сохраняем в память напрямую
                if memory_service:
                    # Разбиваем длинные тексты на чанки по ~1500 символов
                    chunks = _split_text_to_chunks(text, max_len=1500)
                    for chunk in chunks:
                        content = f"[Файл: {file_name}] {chunk}"
                        await memory_service.store_message(user.id, "user", content)

                    state.increment_bulk(user.id, len(chunks))
                    await update.message.reply_text(
                        f"✅ <code>{file_name}</code> — {len(chunks)} фрагмент(ов), "
                        f"{len(text)} символов",
                        parse_mode="HTML"
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
                await pipeline.process_turn(user, update.effective_chat.id, user_text, context, state)

        except Exception as e:
            logger.error(f"Ошибка обработки документа от {user.id}: {e}")
            await update.message.reply_text(f"❌ Ошибка обработки файла: {e}")

    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # --- Фотографии ---
    @authorized
    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Обрабатывает изображения от пользователя.
        В bulk-режиме: описывает фото через AI и сохраняет описание в память.
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
            photo = update.message.photo[-1]
            file = await photo.get_file()
            image_bytes = await file.download_as_bytearray()
            caption = update.message.caption or ""
            state = context.bot_data.get("state")

            if state and state.is_bulk(user.id):
                # Bulk-режим: описываем фото коротко и сохраняем в память
                if memory_service:
                    # Получаем описание через AI (короткое)
                    description = await ai_engine.analyze_image(
                        bytes(image_bytes),
                        user_message=caption or "Кратко опиши что на изображении (2-3 предложения).",
                        user_profile=None,
                        user_name=user.first_name
                    )
                    content = f"[Фото] {caption + ': ' if caption else ''}{description}"
                    fire_and_forget(memory_service.store_message(user.id, "user", content), name=f"photo-bulk-{user.id}")
                    state.increment_bulk(user.id)
                    await update.message.reply_text("✅ Фото сохранено в память")
                else:
                    await update.message.reply_text("❌ Memory Service не доступен.")
                return

            # Обычный режим
            user_data = await db_service.get_user(user.id)
            user_profile = user_data if user_data else None

            # Определяем провайдер пользователя для vision
            user_provider = None
            user_model = None
            user_model_str = user_data.get('selected_model') if user_data else None
            if user_model_str:
                user_provider, user_model = parse_model_string(user_model_str)

            response_text = await ai_engine.analyze_image(
                bytes(image_bytes),
                user_message=caption,
                user_profile=user_profile,
                user_name=user.first_name,
                provider_name=user_provider,
                model=user_model,
            )

            formatted_response = markdown_to_telegram_html(response_text)
            await send_long_message(update.message, formatted_response)

        except Exception as e:
            logger.error(f"Ошибка обработки фото от {user.id}: {e}")
            await update.message.reply_text("❌ Не удалось обработать изображение.")

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
