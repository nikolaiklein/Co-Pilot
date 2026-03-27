"""Тесты prompt_builder — чистые функции."""

from services.prompt_builder import build_system_prompt, wrap_prompt_with_context, build_model_context_for_prompt


def test_build_system_prompt_default():
    prompt = build_system_prompt()
    assert "Правильный Помощник" in prompt
    assert "миссия" in prompt.lower() or "Миссия" in prompt


def test_build_system_prompt_with_user_name():
    prompt = build_system_prompt(user_name="Nikolai")
    assert "Nikolai" in prompt


def test_build_system_prompt_with_custom_nickname():
    profile = {"bot_nickname": "Рух"}
    prompt = build_system_prompt(user_profile=profile)
    assert "Рух" in prompt
    assert "Правильный Помощник" not in prompt


def test_build_system_prompt_interview_mode_when_no_profile():
    prompt = build_system_prompt(user_profile={})
    assert "INTERVIEW" in prompt


def test_build_system_prompt_no_interview_when_profile_exists():
    profile = {
        "profile_summary": {
            "summary": "Опытный разработчик",
            "interests": ["Python"],
        }
    }
    prompt = build_system_prompt(user_profile=profile)
    assert "ТЕКУЩИЙ РЕЖИМ: INTERVIEW" not in prompt


def test_build_system_prompt_model_context():
    prompt = build_system_prompt(model_context="- gemini-2.5-flash (Gemini)")
    assert "Переключение моделей" in prompt
    assert "gemini-2.5-flash" in prompt


def test_build_system_prompt_current_model():
    prompt = build_system_prompt(current_model="gemini/gemini-2.5-flash")
    assert "gemini/gemini-2.5-flash" in prompt


def test_wrap_prompt_with_context_no_memory():
    result = wrap_prompt_with_context("base prompt")
    assert "<instructions>" in result
    assert "base prompt" in result
    assert "<user_context>" not in result


def test_wrap_prompt_with_context_with_memory():
    result = wrap_prompt_with_context("base prompt", "memory data")
    assert "<instructions>" in result
    assert "<user_context>" in result
    assert "memory data" in result


def test_build_model_context_for_prompt():
    gemini = {"flash": "gemini-2.5-flash"}
    nvidia = {"kimi-k2": "moonshotai/kimi-k2-instruct"}
    meta = {"kimi": {"label": "Kimi"}}
    result = build_model_context_for_prompt(gemini, nvidia, meta)
    assert "flash" in result
    assert "kimi-k2" in result
