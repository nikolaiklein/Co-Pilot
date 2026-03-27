"""
Утилиты форматирования и обработки текста для Telegram.
Вынесены из telegram_bot.py для повторного использования.
"""

import io
import re
import csv
import json
import html
import logging

from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

# Лимит символов в одном сообщении Telegram
TELEGRAM_MESSAGE_LIMIT = 4096


def markdown_to_telegram_html(text: str) -> str:
    """
    Конвертирует Markdown от AI в HTML-формат Telegram.
    Если текст уже содержит HTML-теги — сохраняет их.
    Telegram поддерживает: <b>, <i>, <code>, <pre>, <a>, <s>, <u>
    """
    # Проверяем, есть ли уже HTML-теги в тексте
    _telegram_tags = re.compile(r'</?(?:b|i|u|s|code|pre|a)\b[^>]*>')
    has_html = bool(_telegram_tags.search(text))

    if has_html:
        # Текст уже содержит HTML — сохраняем теги, экранируем только контент между ними
        # Разбиваем на части: теги и текст между ними
        parts = _telegram_tags.split(text)
        tags = _telegram_tags.findall(text)
        result = []
        for i, part in enumerate(parts):
            result.append(html.escape(part))
            if i < len(tags):
                result.append(tags[i])
        text = ''.join(result)
    else:
        # Чистый markdown — конвертируем
        text = html.escape(text)
        # **жирный** -> <b>жирный</b>
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        # *курсив* -> <i>курсив</i>
        text = re.sub(r'\*([^*]+?)\*', r'<i>\1</i>', text)
        # `код` -> <code>код</code>
        text = re.sub(r'`([^`]+?)`', r'<code>\1</code>', text)
        # ~~зачёркнутый~~ -> <s>зачёркнутый</s>
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


async def send_long_message(message, text: str):
    """Отправляет длинное сообщение, разбивая на части."""
    parts = split_message(text)
    for part in parts:
        try:
            await message.reply_text(part, parse_mode=ParseMode.HTML)
        except Exception:
            await message.reply_text(part)


def _split_text_to_chunks(text: str, max_len: int = 1500) -> list[str]:
    """Разбивает текст на чанки для эмбеддинга."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    paragraphs = text.split('\n\n')
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_len:
            current += para + "\n\n"
        else:
            if current.strip():
                chunks.append(current.strip())
            # Если абзац сам длиннее max_len — нарезаем по предложениям
            if len(para) > max_len:
                words = para.split()
                current = ""
                for word in words:
                    if len(current) + len(word) + 1 <= max_len:
                        current += word + " "
                    else:
                        if current.strip():
                            chunks.append(current.strip())
                        current = word + " "
            else:
                current = para + "\n\n"

    if current.strip():
        chunks.append(current.strip())

    return chunks if chunks else [text[:max_len]]


async def extract_text_from_file(file_bytes: bytes, file_name: str) -> str | None:
    """Извлекает текст из файла по расширению."""
    ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''

    try:
        if ext == 'txt':
            return file_bytes.decode('utf-8', errors='replace')

        elif ext == 'pdf':
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(io.BytesIO(file_bytes))
                pages = []
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return '\n\n'.join(pages) if pages else None
            except ImportError:
                logger.warning("PyPDF2 не установлен, PDF не поддерживается")
                return None

        elif ext == 'docx':
            try:
                from docx import Document
                doc = Document(io.BytesIO(file_bytes))
                paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                return '\n\n'.join(paragraphs) if paragraphs else None
            except ImportError:
                logger.warning("python-docx не установлен, DOCX не поддерживается")
                return None

        elif ext == 'csv':
            text = file_bytes.decode('utf-8', errors='replace')
            return text  # Сохраняем CSV как текст

        elif ext == 'json':
            data = json.loads(file_bytes.decode('utf-8', errors='replace'))
            return json.dumps(data, ensure_ascii=False, indent=2)

        else:
            # Пробуем как текст
            decoded = file_bytes.decode('utf-8', errors='strict')
            return decoded
    except Exception as e:
        logger.error(f"Ошибка извлечения текста из {file_name}: {e}")
        return None
