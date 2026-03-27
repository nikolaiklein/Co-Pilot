"""
Явное управление состоянием бота.
Замена closure-переменных из telegram_bot.py на тестируемый класс.
"""

import logging

logger = logging.getLogger(__name__)

OWNER_ID = 292628110


class BotState:
    """
    Хранит per-user состояние бота: авторизацию и bulk-режим.
    Единственный экземпляр живёт в context.bot_data["state"].
    """

    def __init__(self, allowed_users: set[int] | None = None):
        self._allowed_users: set[int] = allowed_users or set()
        self._bulk_mode_users: dict[int, int] = {}  # user_id -> count

    # --- Авторизация ---

    def is_authorized(self, user_id: int) -> bool:
        """Проверяет, авторизован ли пользователь."""
        if not self._allowed_users:
            return True  # Если список пуст — доступ открыт
        return user_id in self._allowed_users

    def is_owner(self, user_id: int) -> bool:
        """Проверяет, является ли пользователь владельцем."""
        return user_id == OWNER_ID

    @property
    def allowed_users(self) -> set[int]:
        return self._allowed_users

    def add_user(self, user_id: int) -> None:
        """Добавляет пользователя в список авторизованных."""
        self._allowed_users.add(user_id)
        logger.info(f"Пользователь {user_id} добавлен. Всего: {len(self._allowed_users)}")

    def remove_user(self, user_id: int) -> bool:
        """Удаляет пользователя. Возвращает False если пытаемся удалить владельца."""
        if user_id == OWNER_ID:
            return False
        self._allowed_users.discard(user_id)
        logger.info(f"Пользователь {user_id} удалён. Всего: {len(self._allowed_users)}")
        return True

    # --- Bulk-режим ---

    def is_bulk(self, user_id: int) -> bool:
        """Проверяет, в bulk-режиме ли пользователь."""
        return user_id in self._bulk_mode_users

    def start_bulk(self, user_id: int) -> None:
        """Включает bulk-режим."""
        self._bulk_mode_users[user_id] = 0

    def stop_bulk(self, user_id: int) -> int:
        """Выключает bulk-режим. Возвращает количество загруженных записей."""
        return self._bulk_mode_users.pop(user_id, 0)

    def increment_bulk(self, user_id: int, count: int = 1) -> int:
        """Увеличивает счётчик bulk и возвращает новое значение."""
        self._bulk_mode_users[user_id] = self._bulk_mode_users.get(user_id, 0) + count
        return self._bulk_mode_users[user_id]

    def get_bulk_count(self, user_id: int) -> int:
        """Возвращает текущий счётчик bulk."""
        return self._bulk_mode_users.get(user_id, 0)

    @property
    def bulk_active_count(self) -> int:
        """Количество пользователей в bulk-режиме."""
        return len(self._bulk_mode_users)


def authorized(func):
    """
    Декоратор для хэндлеров python-telegram-bot.
    Проверяет авторизацию через BotState в context.bot_data.
    """
    async def wrapper(update, context, *args, **kwargs):
        user = update.effective_user
        if not user:
            return
        state: BotState = context.bot_data.get("state")
        if state and not state.is_authorized(user.id):
            return  # Молча игнорируем неавторизованных
        return await func(update, context, *args, **kwargs)
    # Сохраняем метаданные оригинальной функции
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper
