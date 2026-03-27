"""Тесты BotState и @authorized декоратора."""

from services.state import BotState, OWNER_ID, authorized


def test_authorized_user_passes(bot_state):
    assert bot_state.is_authorized(100)
    assert bot_state.is_authorized(200)


def test_unauthorized_user_rejected(bot_state):
    assert not bot_state.is_authorized(999)


def test_empty_allowed_users_means_open_access(empty_state):
    assert empty_state.is_authorized(12345)
    assert empty_state.is_authorized(99999)


def test_owner_check():
    state = BotState({100})
    assert state.is_owner(OWNER_ID)
    assert not state.is_owner(100)


def test_add_user(bot_state):
    bot_state.add_user(300)
    assert bot_state.is_authorized(300)


def test_remove_user(bot_state):
    assert bot_state.remove_user(100)
    assert not bot_state.is_authorized(100)


def test_cannot_remove_owner():
    state = BotState({OWNER_ID, 100})
    assert not state.remove_user(OWNER_ID)
    assert state.is_authorized(OWNER_ID)


def test_bulk_mode(bot_state):
    assert not bot_state.is_bulk(100)
    bot_state.start_bulk(100)
    assert bot_state.is_bulk(100)
    assert bot_state.increment_bulk(100) == 1
    assert bot_state.increment_bulk(100) == 2
    count = bot_state.stop_bulk(100)
    assert count == 2
    assert not bot_state.is_bulk(100)


def test_bulk_increment_multiple(bot_state):
    bot_state.start_bulk(100)
    assert bot_state.increment_bulk(100, 5) == 5
    assert bot_state.get_bulk_count(100) == 5


def test_bulk_active_count(bot_state):
    assert bot_state.bulk_active_count == 0
    bot_state.start_bulk(100)
    bot_state.start_bulk(200)
    assert bot_state.bulk_active_count == 2
