"""
Сервис долговременной памяти на базе Mem0.

Архитектура:
- Mem0 автоматически извлекает факты из сообщений через LLM
- Каждый факт получает эмбеддинг и сохраняется в Qdrant
- Дедупликация: повторные факты обновляются, а не дублируются
- Поиск: vector search + LLM-ранжирование
- Не нужны триггерные слова — всегда ищем релевантный контекст

Требования:
- pip install mem0ai
- GEMINI_API_KEY — для LLM и эмбеддингов
- QDRANT_URL + QDRANT_API_KEY — для vector store (или локальный путь для dev)
"""

import os
import asyncio
import logging

logger = logging.getLogger(__name__)

# Флаг: удалось ли импортировать mem0
_MEM0_AVAILABLE = False
try:
    from mem0 import AsyncMemory
    _MEM0_AVAILABLE = True
except ImportError:
    logger.warning("mem0ai не установлен. Установите: pip install mem0ai")


class MemoryService:
    """Сервис долговременной памяти с Mem0 + Qdrant."""

    def __init__(self, gemini_api_key: str, qdrant_url: str = None, qdrant_api_key: str = None):
        if not _MEM0_AVAILABLE:
            raise ImportError("mem0ai не установлен. pip install mem0ai")

        self._config = self._build_config(gemini_api_key, qdrant_url, qdrant_api_key)
        self._memory = None  # lazy init — AsyncMemory.from_config() is a coroutine

        # Очередь для фоновых задач (store)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task = None

        logger.info("MemoryService (Mem0) инициализирован (lazy).")

    @property
    def memory(self):
        """Доступ к внутреннему объекту memory (для миграции и тестов)."""
        return self._memory

    async def _ensure_memory(self) -> "AsyncMemory":
        """Lazy-инициализация AsyncMemory (корутина)."""
        if self._memory is None:
            self._memory = await AsyncMemory.from_config(self._config)
            logger.info("AsyncMemory инициализирован.")
        return self._memory

    @staticmethod
    def _build_config(gemini_api_key: str, qdrant_url: str = None, qdrant_api_key: str = None) -> dict:
        """Собирает конфигурацию Mem0."""
        config = {
            "version": "v1.1",
            "llm": {
                "provider": "gemini",
                "config": {
                    "model": "gemini-2.0-flash",
                    "api_key": gemini_api_key,
                    "temperature": 0.1,
                    "max_tokens": 2000,
                }
            },
            "embedder": {
                "provider": "gemini",
                "config": {
                    "model": "models/gemini-embedding-001",
                    "embedding_dims": 768,
                    "api_key": gemini_api_key,
                }
            },
            "history_db_path": "/tmp/mem0_history.db",
        }

        # Отключаем телеметрию
        os.environ.setdefault("MEM0_TELEMETRY", "false")

        if qdrant_url:
            # Удалённый Qdrant (Cloud или self-hosted)
            vs_config = {
                "url": qdrant_url,
                "collection_name": "copilot_memory",
                "embedding_model_dims": 768,
            }
            if qdrant_api_key:
                vs_config["api_key"] = qdrant_api_key
            config["vector_store"] = {"provider": "qdrant", "config": vs_config}
        else:
            # Локальный Qdrant (для разработки)
            config["vector_store"] = {
                "provider": "qdrant",
                "config": {
                    "path": "/tmp/qdrant_mem0",
                    "collection_name": "copilot_memory",
                    "embedding_model_dims": 768,
                }
            }
            logger.warning("QDRANT_URL не задан — используется локальный Qdrant (/tmp/qdrant_mem0). "
                           "Данные НЕ сохраняются при рестарте Cloud Run!")

        return config

    # --- Очередь для фоновой записи ---

    async def _start_worker(self):
        """Запускает воркер очереди если ещё не запущен."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._queue_worker())

    async def _queue_worker(self):
        """Воркер: обрабатывает очередь по одному."""
        while True:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=300)
                user_id, messages, infer = task
                await self._do_store(user_id, messages, infer)
                self._queue.task_done()
            except asyncio.TimeoutError:
                logger.info("Memory queue worker: idle 5 min, shutting down")
                break
            except Exception as e:
                logger.error(f"Memory queue worker error: {e}")

    async def _do_store(self, user_id: int, messages: list[dict], infer: bool):
        """Внутренняя реализация сохранения через Mem0."""
        try:
            memory = await self._ensure_memory()
            result = await memory.add(
                messages,
                user_id=str(user_id),
                infer=infer,
            )
            events = result.get("results", [])
            added = sum(1 for e in events if e.get("event") == "ADD")
            updated = sum(1 for e in events if e.get("event") == "UPDATE")
            logger.info(f"✅ Mem0 store for user {user_id}: +{added} new, ~{updated} updated (infer={infer})")
        except Exception as e:
            logger.error(f"Ошибка Mem0 add для {user_id}: {e}")

    # --- Публичный API ---

    async def store_message(self, user_id: int, role: str, content: str, infer: bool = True):
        """
        Ставит сообщение в очередь для фонового сохранения.
        Mem0 автоматически извлекает факты (infer=True) или сохраняет как есть (infer=False).
        """
        messages = [{"role": role, "content": content}]
        await self._queue.put((user_id, messages, infer))
        await self._start_worker()
        logger.info(f"Memory queued for user {user_id} (role={role}, queue_size={self._queue.qsize()})")

    async def store_bulk(self, user_id: int, content: str):
        """
        Сохраняет данные для bulk-режима: и сырой текст, и извлечённые факты.
        Сырой текст (infer=False) — чтобы поиск находил полный контент.
        Факты (infer=True) — чтобы работала дедупликация и структурирование.
        """
        messages = [{"role": "user", "content": content}]
        # Сначала сырой текст для полноты поиска
        await self._queue.put((user_id, messages, False))
        # Потом извлечение фактов
        await self._queue.put((user_id, messages, True))
        await self._start_worker()
        logger.info(f"Memory bulk queued for user {user_id} (queue_size={self._queue.qsize()})")

    async def store_conversation(self, user_id: int, user_text: str, assistant_text: str):
        """
        Сохраняет пару user+assistant для лучшего извлечения фактов.
        Mem0 видит контекст целиком и извлекает факты точнее.
        """
        messages = [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]
        await self._queue.put((user_id, messages, True))
        await self._start_worker()
        logger.info(f"Memory conversation queued for user {user_id} (queue_size={self._queue.qsize()})")

    async def search_memory(self, user_id: int, query: str, limit: int = 5) -> list[dict]:
        """
        Семантический поиск по памяти пользователя.

        Returns:
            list[dict]: [{id, content, score, role}]
        """
        try:
            memory = await self._ensure_memory()
            result = await memory.search(
                query,
                user_id=str(user_id),
                limit=limit,
            )
            memories = result.get("results", [])
            return [
                {
                    "id": m.get("id", ""),
                    "content": m.get("memory", ""),
                    "score": m.get("score", 0),
                    "role": "memory",
                }
                for m in memories
            ]
        except Exception as e:
            logger.error(f"Ошибка поиска Mem0 для {user_id}: {e}")
            return []

    async def get_memory_context(self, user_id: int, user_text: str) -> str:
        """
        Всегда ищет релевантные воспоминания и возвращает контекст.
        Не нужны триггерные слова — Mem0 хранит факты, а не сырой текст,
        поэтому поиск точнее и не даёт мусора.
        """
        results = await self.search_memory(user_id, user_text, limit=15)
        if not results:
            return ""

        # Фильтруем по релевантности (score 0-1, выше = лучше)
        relevant = [r for r in results if r.get('score', 0) >= 0.2]
        if not relevant:
            logger.debug(f"Memory: results found but below threshold for user {user_id}")
            return ""

        lines = ["[КОНТЕКСТ ИЗ ДОЛГОВРЕМЕННОЙ ПАМЯТИ — извлечённые факты о пользователе:]"]
        for i, r in enumerate(relevant, 1):
            lines.append(f"  {i}. {r['content'][:500]}")
        lines.append("[КОНЕЦ КОНТЕКСТА ПАМЯТИ]\n")

        context = "\n".join(lines)
        logger.info(f"Memory context for user {user_id}: {len(relevant)} facts, {len(context)} chars")
        return context

    async def get_all_memories(self, user_id: int, limit: int = 100) -> list[dict]:
        """Получить все воспоминания пользователя."""
        try:
            result = await self.memory.get_all(user_id=str(user_id), limit=limit)
            return result.get("results", [])
        except Exception as e:
            logger.error(f"Ошибка get_all для {user_id}: {e}")
            return []

    async def delete_all(self, user_id: int):
        """Удалить все воспоминания пользователя."""
        try:
            await self.memory.delete_all(user_id=str(user_id))
            logger.info(f"Все воспоминания удалены для user {user_id}")
        except Exception as e:
            logger.error(f"Ошибка delete_all для {user_id}: {e}")

    async def summarize_old_messages(self, user_id: int, ai_engine, batch_size: int = 30):
        """
        С Mem0 суммаризация не нужна — дедупликация происходит автоматически.
        Метод оставлен для совместимости с cron endpoint.
        """
        logger.info(f"summarize_old_messages: с Mem0 не требуется (user {user_id})")

    async def close(self):
        """Дождаться обработки очереди и завершить."""
        if self._worker_task and not self._worker_task.done():
            try:
                await asyncio.wait_for(self._queue.join(), timeout=30)
            except asyncio.TimeoutError:
                logger.warning("Memory queue not drained in 30s")
            self._worker_task.cancel()
        logger.info("MemoryService closed.")
