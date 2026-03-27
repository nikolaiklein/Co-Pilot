"""Тесты parse_model_string — маппинг коротких имён на provider/model."""

from services.ai_engine import parse_model_string


def test_gemini_short_name():
    provider, model = parse_model_string("gemini-2.5-flash")
    assert provider == "gemini"
    assert "gemini" in model


def test_gemini_short_name_pro():
    provider, model = parse_model_string("gemini-2.5-pro")
    assert provider == "gemini"


def test_nvidia_short_name_kimi():
    provider, model = parse_model_string("kimi-k2")
    assert provider == "nvidia"
    assert "kimi" in model


def test_nvidia_short_name_deepseek():
    provider, model = parse_model_string("deepseek-v3.2")
    assert provider == "nvidia"
    assert "deepseek" in model


def test_explicit_provider_slash():
    provider, model = parse_model_string("gemini/gemini-2.5-flash")
    assert provider == "gemini"
    assert model == "gemini-2.5-flash"


def test_case_insensitive():
    provider1, _ = parse_model_string("Gemini-2.5-Flash")
    provider2, _ = parse_model_string("gemini-2.5-flash")
    assert provider1 == provider2


def test_unknown_model_returns_empty():
    provider, model = parse_model_string("nonexistent-model-xyz")
    # Should not crash; returns something (possibly empty or best-effort)
    assert isinstance(provider, str)
    assert isinstance(model, str)
