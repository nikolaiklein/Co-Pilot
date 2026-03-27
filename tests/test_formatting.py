"""Тесты formatting — чистые функции без I/O."""

from services.formatting import markdown_to_telegram_html, split_message, _split_text_to_chunks


def test_markdown_bold_to_html():
    result = markdown_to_telegram_html("**жирный** текст")
    assert "<b>жирный</b>" in result


def test_markdown_italic_to_html():
    result = markdown_to_telegram_html("*курсив* текст")
    assert "<i>курсив</i>" in result


def test_markdown_code_to_html():
    result = markdown_to_telegram_html("`код` текст")
    assert "<code>код</code>" in result


def test_markdown_strikethrough_to_html():
    result = markdown_to_telegram_html("~~зачёркнутый~~ текст")
    assert "<s>зачёркнутый</s>" in result


def test_html_tags_preserved():
    text = "<b>уже жирный</b> текст"
    result = markdown_to_telegram_html(text)
    assert "<b>уже жирный</b>" in result


def test_html_entities_escaped_in_markdown_mode():
    result = markdown_to_telegram_html("a < b & c > d")
    assert "&lt;" in result
    assert "&amp;" in result
    assert "&gt;" in result


def test_split_message_short():
    parts = split_message("Короткий текст")
    assert len(parts) == 1
    assert parts[0] == "Короткий текст"


def test_split_message_long():
    # Текст с абзацами — разбивается корректно
    text = ("A" * 2000 + "\n\n") * 3  # ~6006 chars с разделителями
    parts = split_message(text, limit=4096)
    assert len(parts) >= 2
    for part in parts:
        assert len(part) <= 4096


def test_split_message_by_paragraphs():
    p1 = "A" * 2000
    p2 = "B" * 2000
    p3 = "C" * 2000
    text = f"{p1}\n\n{p2}\n\n{p3}"
    parts = split_message(text, limit=4096)
    assert len(parts) >= 2


def test_split_text_to_chunks():
    text = "Hello world. " * 200  # ~2600 chars
    chunks = _split_text_to_chunks(text, max_len=1500)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 1500


def test_split_text_to_chunks_short():
    chunks = _split_text_to_chunks("short", max_len=1500)
    assert chunks == ["short"]
