"""
Сервис долговременной памяти на базе Gemini Embedding + Firestore Vector Search.

Архитектура:
- Каждое сообщение пользователя получает эмбеддинг через Gemini Embedding API
- Эмбеддинг сохраняется в Firestore вместе с текстом
- При необходимости (триггерные слова или явный запрос) — vector search по всей истории
- Найденные релевантные фрагменты добавляются в контекст LLM

Firestore structure:
  users/{user_id}/memory/{auto_id}
    ├── content: string          — текст сообщения
    ├── role: "user" | "assistant"
    ├── embedding: Vector(3072)  — Gemini embedding
    ├── timestamp: timestamp
    └── summary_block: bool      — True если это суммаризация блока
"""

import os
import re
import asyncio
import logging
from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure

logger = logging.getLogger(__name__)

# Триггерные слова для автоматического поиска в памяти
MEMORY_TRIGGERS = re.compile(
    r'(?:вспомни|помнишь|мы обсуждали|я говорил|я рассказывал|'
    r'ранее|раньше|прошлый раз|в прошлом|напомни|'
    r'как я говорил|мы договорились|я упоминал|'
    r'найди|поищи|поиск|искать|потяни|вытащи|покажи|'
    r'что я загружал|что я отправлял|что было|'
    r'информаци[юия]|данные о|данные по|данные про|'
    r'что (?:ты )?знаешь о|что (?:ты )?знаешь про|'
    r'в моих (?:записях|данных|файлах|сообщениях)|'
    r'из (?:памяти|загруженного|моих данных)|'
    r'remember|recall|we discussed|i told you|i said|earlier|last time|'
    r'find|search|look up)',
    re.IGNORECASE
)


class MemoryService:
    """Сервис долговременной памяти с vector search."""

    def __init__(self, db: firestore.AsyncClient, gemini_api_key: str):
        self.db = db
        self.api_key = gemini_api_key
        self._http_session = None
        # Семафор: макс 5 параллельных вызовов к Embedding API
        self._embedding_semaphore = asyncio.Semaphore(5)
        # Очередь для фоновых задач (store_message)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task = None
        logger.info("MemoryService инициализирован.")

    async def _get_session(self):
        """Lazy-init aiohttp session."""
        if self._http_session is None or self._http_session.closed:
            import aiohttp
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def _start_worker(self):
        """Запускает воркер очереди если ещё не запущен."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._queue_worker())

    async def _queue_worker(self):
        """Воркер: обрабатывает очередь store_message по одному, с семафором."""
        while True:
            try:
                user_id, role, content = await asyncio.wait_for(self._queue.get(), timeout=300)
                await self._do_store_message(user_id, role, content)
                self._queue.task_done()
            except asyncio.TimeoutError:
                logger.info("Memory queue worker: idle 5 min, shutting down")
                break
            except Exception as e:
                logger.error(f"Memory queue worker error: {e}")

    async def enqueue_store(self, user_id: int, role: str, content: str):
        """Ставит сообщение в очередь для фонового сохранения с эмбеддингом."""
        await self._queue.put((user_id, role, content))
        await self._start_worker()
        logger.info(f"Memory queued for user {user_id} (role={role}, queue_size={self._queue.qsize()})")

    async def close(self):
        """Закрыть HTTP-сессию и остановить воркер."""
        if self._worker_task and not self._worker_task.done():
            # Дождёмся обработки оставшихся задач (макс 30 сек)
            try:
                await asyncio.wait_for(self._queue.join(), timeout=30)
            except asyncio.TimeoutError:
                logger.warning("Memory queue not drained in 30s, stopping worker")
            self._worker_task.cancel()
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    async def get_embedding(self, text: str) -> list[float] | None:
        """Получить эмбеддинг текста через Gemini Embedding API (768 dimensions)."""
        try:
            async with self._embedding_semaphore:
                return await self._get_embedding_impl(text)
        except Exception as e:
            logger.error(f"Ошибка получения эмбеддинга: {e}")
            return None

    async def _get_embedding_impl(self, text: str) -> list[float] | None:
        """Внутренняя реализация получения эмбеддинга."""
        try:
            session = await self._get_session()
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent"
            # Обрезаем текст до 2000 символов (лимит API)
            truncated = text[:2000]
            payload = {
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": truncated}]},
                "outputDimensionality": 768,  # Firestore max 2048, 768 оптимально
            }
            async with session.post(
                url,
                json=payload,
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                timeout=__import__('aiohttp').ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                values = data.get("embedding", {}).get("values")
                if values and len(values) > 0:
                    return values
                logger.warning(f"Пустой эмбеддинг: {data.get('error', 'unknown')}")
                return None
        except Exception as e:
            logger.error(f"Ошибка получения эмбеддинга: {e}")
            return None

    async def store_message(self, user_id: int, role: str, content: str):
        """
        Ставит сообщение в очередь для фонового сохранения.
        Не блокирует вызывающий код — возвращается сразу.
        """
        await self.enqueue_store(user_id, role, content)

    async def _do_store_message(self, user_id: int, role: str, content: str):
        """
        Сохранить сообщение с эмбеддингом в коллекцию memory.
        Вызывается воркером очереди.
        """
        try:
            embedding = await self.get_embedding(content)
            if not embedding:
                logger.warning(f"Не удалось получить эмбеддинг, сохраняем без вектора")
                memory_ref = self.db.collection('users').document(str(user_id)).collection('memory')
                await memory_ref.add({
                    'content': content,
                    'role': role,
                    'timestamp': firestore.SERVER_TIMESTAMP,
                    'summary_block': False,
                })
                return

            memory_ref = self.db.collection('users').document(str(user_id)).collection('memory')
            await memory_ref.add({
                'content': content,
                'role': role,
                'embedding': Vector(embedding),
                'timestamp': firestore.SERVER_TIMESTAMP,
                'summary_block': False,
            })
            logger.info(f"✅ Memory stored for user {user_id} (role={role}, len={len(content)}, embedding_dims={len(embedding)})")
        except Exception as e:
            logger.error(f"Ошибка сохранения в memory для {user_id}: {e}")

    async def search_memory(self, user_id: int, query: str, limit: int = 5) -> list[dict]:
        """
        Семантический поиск по всей истории пользователя.

        Returns:
            list[dict]: Список найденных сообщений [{role, content, distance}]
        """
        try:
            query_embedding = await self.get_embedding(query)
            if not query_embedding:
                return []

            memory_ref = self.db.collection('users').document(str(user_id)).collection('memory')

            # Firestore Vector Search
            vector_query = memory_ref.find_nearest(
                vector_field="embedding",
                query_vector=Vector(query_embedding),
                distance_measure=DistanceMeasure.COSINE,
                limit=limit,
            )

            docs = await vector_query.get()

            results = []
            for doc in docs:
                data = doc.to_dict()
                results.append({
                    'role': data.get('role', 'user'),
                    'content': data.get('content', ''),
                    'summary_block': data.get('summary_block', False),
                })

            logger.info(f"Memory search for user {user_id}: found {len(results)} results")
            return results
        except Exception as e:
            logger.error(f"Ошибка поиска в memory для {user_id}: {e}")
            return []

    def should_search_memory(self, text: str) -> bool:
        """Проверяет, содержит ли текст триггерные слова для поиска в памяти."""
        return bool(MEMORY_TRIGGERS.search(text))

    async def get_memory_context(self, user_id: int, user_text: str) -> str:
        """
        Основной метод: определяет нужен ли поиск и возвращает контекст.

        Returns:
            str: Форматированный контекст из памяти (или пустая строка).
        """
        trigger_match = self.should_search_memory(user_text)
        if not trigger_match:
            logger.info(f"Memory: no trigger words in message from user {user_id}")
            return ""

        logger.info(f"Memory: trigger detected for user {user_id}, searching...")
        results = await self.search_memory(user_id, user_text, limit=5)
        if not results:
            return ""

        # Форматируем найденные фрагменты
        lines = ["[КОНТЕКСТ ИЗ ДОЛГОВРЕМЕННОЙ ПАМЯТИ — релевантные фрагменты прошлых разговоров:]"]
        for i, r in enumerate(results, 1):
            role_label = "Пользователь" if r['role'] == 'user' else "Ассистент"
            prefix = "[Конспект]" if r.get('summary_block') else ""
            lines.append(f"  {i}. {prefix}{role_label}: {r['content'][:500]}")
        lines.append("[КОНЕЦ КОНТЕКСТА ПАМЯТИ]\n")

        context = "\n".join(lines)
        logger.info(f"Memory context for user {user_id}: {len(results)} fragments, {len(context)} chars")
        return context

    async def summarize_old_messages(self, user_id: int, ai_engine, batch_size: int = 30):
        """
        Суммаризирует старые сообщения в блоки-конспекты.
        Вызывается периодически (cron) для сжатия истории.

        Args:
            user_id: Telegram User ID
            ai_engine: AIEngine для суммаризации
            batch_size: сколько сообщений объединять в один конспект
        """
        try:
            memory_ref = self.db.collection('users').document(str(user_id)).collection('memory')

            # Берём старые сообщения (не конспекты), сортируем по времени
            query = (
                memory_ref
                .where('summary_block', '==', False)
                .order_by('timestamp')
                .limit(batch_size)
            )
            docs = await query.get()

            if len(docs) < batch_size:
                return  # Недостаточно сообщений для суммаризации

            # Формируем текст для суммаризации
            messages_text = []
            doc_refs = []
            for doc in docs:
                data = doc.to_dict()
                role = "Пользователь" if data['role'] == 'user' else "Ассистент"
                messages_text.append(f"{role}: {data['content']}")
                doc_refs.append(doc.reference)

            dialog_text = "\n".join(messages_text)

            # Суммаризируем через LLM
            summary_prompt = f"""Сожми следующий диалог в краткий конспект (3-5 предложений).
Сохрани ключевые факты, решения, имена, даты и важные детали.
Пиши от третьего лица.

Диалог:
{dialog_text}

Конспект:"""

            summary = await ai_engine.analyze_content(summary_prompt)
            if not summary or len(summary) < 10:
                return

            # Сохраняем конспект с эмбеддингом
            embedding = await self.get_embedding(summary)
            summary_data = {
                'content': summary,
                'role': 'system',
                'timestamp': firestore.SERVER_TIMESTAMP,
                'summary_block': True,
            }
            if embedding:
                summary_data['embedding'] = Vector(embedding)

            await memory_ref.add(summary_data)

            # Удаляем оригинальные сообщения (они заменены конспектом)
            for ref in doc_refs:
                await ref.delete()

            logger.info(f"Summarized {len(doc_refs)} messages for user {user_id}")

        except Exception as e:
            logger.error(f"Ошибка суммаризации для {user_id}: {e}")
