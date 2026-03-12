"""
Миграция памяти из Firestore в Mem0 (Qdrant).

Читает все записи из users/{user_id}/memory/ в Firestore
и загружает их в Mem0 через AsyncMemory.add().

Запуск:
  export GEMINI_API_KEY=...
  export QDRANT_URL=http://89.167.90.181:6333
  export QDRANT_API_KEY=copilot_qdrant_2026_secret
  export GOOGLE_APPLICATION_CREDENTIALS=path/to/key.json  # для локального запуска
  python scripts/migrate_firestore_to_mem0.py
"""

import os
import sys
import asyncio
import logging

# Добавляем корень проекта в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("migrate")


async def main():
    # 1. Инициализация Firebase
    from config.firebase_init import init_firebase
    init_firebase()

    from google.cloud import firestore
    db = firestore.AsyncClient()

    # 2. Инициализация Mem0 напрямую (без MemoryService обёртки)
    from mem0 import AsyncMemory

    gemini_key = os.getenv("GEMINI_API_KEY")
    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")

    if not gemini_key:
        logger.error("GEMINI_API_KEY не задан")
        return

    os.environ.setdefault("MEM0_TELEMETRY", "false")

    config = {
        "version": "v1.1",
        "llm": {
            "provider": "gemini",
            "config": {
                "model": "gemini-2.0-flash",
                "api_key": gemini_key,
                "temperature": 0.1,
                "max_tokens": 2000,
            }
        },
        "embedder": {
            "provider": "gemini",
            "config": {
                "model": "models/gemini-embedding-001",
                "embedding_dims": 768,
                "api_key": gemini_key,
            }
        },
        "history_db_path": "/tmp/mem0_migrate_history.db",
    }

    if qdrant_url:
        vs_config = {
            "url": qdrant_url,
            "collection_name": "copilot_memory",
            "embedding_model_dims": 768,
        }
        if qdrant_api_key:
            vs_config["api_key"] = qdrant_api_key
        config["vector_store"] = {"provider": "qdrant", "config": vs_config}

    memory = await AsyncMemory.from_config(config)
    logger.info("AsyncMemory инициализирован.")

    # 3. Получаем всех пользователей
    users_ref = db.collection('users')
    users = await users_ref.get()
    user_ids = [doc.id for doc in users]
    logger.info(f"Найдено пользователей: {len(user_ids)}: {user_ids}")

    total_migrated = 0

    for user_id in user_ids:
        logger.info(f"\n{'='*50}")
        logger.info(f"Миграция пользователя {user_id}")

        # Читаем все записи памяти из Firestore
        memory_ref = db.collection('users').document(str(user_id)).collection('memory')
        docs = await memory_ref.order_by('timestamp').get()

        if not docs:
            logger.info(f"  Нет записей для {user_id}, пропускаем")
            continue

        logger.info(f"  Найдено записей в Firestore: {len(docs)}")

        # Группируем сообщения в пары user+assistant для лучшего извлечения фактов
        messages_buffer = []
        migrated = 0
        batch_num = 0

        for doc in docs:
            data = doc.to_dict()
            role = data.get('role', 'user')
            content = data.get('content', '')

            if not content or len(content.strip()) < 3:
                continue

            # Пропускаем служебные
            if content.strip() in ('✅', '❌', '...'):
                continue

            messages_buffer.append({"role": role, "content": content})

            # Отправляем по 2 сообщения (пара user+assistant)
            if len(messages_buffer) >= 2:
                batch_num += 1
                try:
                    result = await memory.add(
                        messages_buffer,
                        user_id=str(user_id),
                        infer=True,
                    )
                    events = result.get("results", [])
                    added = sum(1 for e in events if e.get("event") == "ADD")
                    updated = sum(1 for e in events if e.get("event") == "UPDATE")
                    migrated += added
                    logger.info(f"  Batch {batch_num}: +{added} new, ~{updated} updated")
                except Exception as e:
                    logger.error(f"  Ошибка Mem0 add batch {batch_num}: {e}")

                messages_buffer = []
                # Пауза чтобы не перегрузить Gemini API
                await asyncio.sleep(1)

        # Дозаписываем остаток
        if messages_buffer:
            try:
                result = await memory.add(
                    messages_buffer,
                    user_id=str(user_id),
                    infer=True,
                )
                events = result.get("results", [])
                added = sum(1 for e in events if e.get("event") == "ADD")
                migrated += added
            except Exception as e:
                logger.error(f"  Ошибка финального batch: {e}")

        # Проверяем результат
        all_memories = await memory.get_all(user_id=str(user_id), limit=500)
        total_facts = len(all_memories.get("results", []))

        logger.info(f"  Пользователь {user_id}: {len(docs)} записей Firestore → {total_facts} фактов в Mem0")
        total_migrated += migrated

    logger.info(f"\n{'='*50}")
    logger.info(f"МИГРАЦИЯ ЗАВЕРШЕНА. Всего новых фактов: {total_migrated}")


if __name__ == "__main__":
    asyncio.run(main())
