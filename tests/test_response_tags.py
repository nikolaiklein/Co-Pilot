"""Тесты parse_response_tags — чистая функция, без зависимостей."""

from services.response_tags import parse_response_tags


def test_no_tags():
    result = parse_response_tags("Привет, как дела?")
    assert result.clean_text == "Привет, как дела?"
    assert result.actions == []


def test_switch_model_tag():
    text = "Переключаю на Kimi. [SWITCH_MODEL: kimi-k2]"
    result = parse_response_tags(text)
    assert "SWITCH_MODEL" not in result.clean_text
    assert len(result.actions) == 1
    assert result.actions[0].type == "switch_model"
    assert result.actions[0].model_id == "kimi-k2"


def test_switch_model_whitespace_variations():
    text = "[SWITCH_MODEL:  gemini-2.5-flash  ]"
    result = parse_response_tags(text)
    assert result.actions[0].model_id == "gemini-2.5-flash"


def test_vault_save_tag():
    text = "Сохраняю. [VAULT_SAVE: prompt | Анализ данных | текст промпта для анализа]"
    result = parse_response_tags(text)
    assert "VAULT_SAVE" not in result.clean_text
    assert len(result.actions) == 1
    a = result.actions[0]
    assert a.type == "vault_save"
    assert a.vault_type == "prompt"
    assert a.vault_title == "Анализ данных"
    assert a.vault_content == "текст промпта для анализа"


def test_vault_save_invalid_type_defaults_to_note():
    text = "[VAULT_SAVE: unknown | Title | Content]"
    result = parse_response_tags(text)
    assert result.actions[0].vault_type == "note"


def test_multiple_tags():
    text = "Текст [SWITCH_MODEL: claude] ещё текст [VAULT_SAVE: idea | Идея | содержимое]"
    result = parse_response_tags(text)
    assert len(result.actions) == 2
    types = {a.type for a in result.actions}
    assert types == {"switch_model", "vault_save"}


def test_tags_stripped_from_clean_text():
    text = "Начало [SWITCH_MODEL: gpt] конец"
    result = parse_response_tags(text)
    assert "[" not in result.clean_text
    assert "SWITCH_MODEL" not in result.clean_text
    assert "Начало" in result.clean_text
    assert "конец" in result.clean_text


def test_triple_newlines_collapsed():
    text = "Текст\n\n\n[SWITCH_MODEL: x]\n\n\nЕщё"
    result = parse_response_tags(text)
    assert "\n\n\n" not in result.clean_text
