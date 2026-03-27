"""
Утилиты для безопасной работы с asyncio tasks.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Хранилище ссылок на задачи (предотвращает GC)
_background_tasks: set[asyncio.Task] = set()


def fire_and_forget(coro, name: str = "background") -> asyncio.Task:
    """
    Создаёт asyncio.Task с логированием ошибок и защитой от GC.
    Использовать вместо голого asyncio.create_task() для fire-and-forget задач.
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error(f"Background task '{t.get_name()}' failed: {type(exc).__name__}: {exc}")

    task.add_done_callback(_on_done)
    return task
