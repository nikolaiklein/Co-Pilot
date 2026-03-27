"""Фикстуры для тестов Co-Pilot."""

import pytest


@pytest.fixture
def bot_state():
    """BotState с тестовыми пользователями."""
    from services.state import BotState
    return BotState(allowed_users={100, 200, 292628110})


@pytest.fixture
def empty_state():
    """BotState с пустым списком (доступ открыт всем)."""
    from services.state import BotState
    return BotState()
