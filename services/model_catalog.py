"""
Каталог моделей LiteLLM — fetch, кеш, фильтрация, группировка.
Чистый сервис данных: без AI-вызовов, без записи в Firestore.
"""

import os
import time
import logging
import asyncio

import aiohttp

logger = logging.getLogger(__name__)

# --- Статические данные и чистые функции из model_data.py ---
from services.model_data import (
    MODEL_FAMILY_META,
    FILTER_PATTERNS,
    NVIDIA_MODELS,
    GEMINI_MODELS,
    DEFAULT_MODELS,
    MODEL_HINTS,
    FAMILY_ORDER,
    get_model_meta,
    get_family_key,
    should_filter as _should_filter,
)


class ModelCatalog:
    """
    Каталог моделей LiteLLM с TTL-кешем.
    Lazy refresh: при запросе после истечения TTL.
    """

    def __init__(self, cache_ttl: int = 300):
        self._cache: list[str] = []
        self._fetched_at: float = 0
        self._ttl = cache_ttl  # 5 минут по умолчанию

    @property
    def is_available(self) -> bool:
        """Есть ли закешированные модели."""
        return len(self._cache) > 0

    @property
    def cache_age_seconds(self) -> int:
        """Возраст кеша в секундах."""
        if self._fetched_at == 0:
            return -1
        return int(time.time() - self._fetched_at)

    async def get_models(self) -> list[str]:
        """
        Возвращает список моделей. Если кеш протух — обновляет.
        Если обновление не удалось — возвращает старый кеш.
        """
        if time.time() - self._fetched_at > self._ttl:
            await self._refresh()
        return self._cache

    async def get_models_grouped(self) -> dict[str, list[str]]:
        """Возвращает модели, сгруппированные по семействам."""
        models = await self.get_models()
        groups: dict[str, list[str]] = {}
        for model_id in models:
            family = get_family_key(model_id)
            if family not in groups:
                groups[family] = []
            groups[family].append(model_id)

        # Сортируем по заданному порядку
        sorted_groups: dict[str, list[str]] = {}
        for family in FAMILY_ORDER:
            if family in groups:
                sorted_groups[family] = sorted(groups[family])
        # Остальные в конец
        for family in groups:
            if family not in sorted_groups:
                sorted_groups[family] = sorted(groups[family])

        return sorted_groups

    async def _refresh(self):
        """Обновляет кеш из LiteLLM /v1/models."""
        base_url = os.getenv("LITELLM_BASE_URL", "")
        if not base_url:
            return

        # Нормализуем URL: убираем /v1 если есть, добавляем /v1/models
        url = base_url.rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        url += "/v1/models"

        try:
            async with aiohttp.ClientSession() as session:
                api_key = os.getenv("LITELLM_API_KEY", "")
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                resp = await asyncio.wait_for(
                    session.get(url, headers=headers),
                    timeout=3.0,
                )
                if resp.status != 200:
                    logger.warning(f"LiteLLM /v1/models вернул {resp.status}")
                    return

                data = await resp.json()

            if not isinstance(data, dict) or "data" not in data:
                logger.warning("LiteLLM /v1/models: неожиданный формат ответа")
                return

            models_raw = data["data"]
            if not isinstance(models_raw, list):
                return

            # Фильтруем и собираем model IDs
            model_ids = []
            for item in models_raw[:100]:  # cap at 100
                if not isinstance(item, dict):
                    continue
                model_id = item.get("id", "")
                if not isinstance(model_id, str) or not model_id:
                    continue
                if _should_filter(model_id):
                    continue
                model_ids.append(model_id)

            self._cache = model_ids
            self._fetched_at = time.time()
            logger.info(f"LiteLLM модели обновлены: {len(model_ids)} моделей")

        except asyncio.TimeoutError:
            logger.warning("LiteLLM /v1/models: таймаут (3с)")
        except Exception as e:
            logger.warning(f"LiteLLM /v1/models: ошибка — {type(e).__name__}: {e}")

    async def check_health(self) -> tuple[bool, int]:
        """
        Проверка доступности LiteLLM. Возвращает (is_available, model_count).
        Используется при старте приложения.
        """
        base_url = os.getenv("LITELLM_BASE_URL", "")
        if not base_url:
            return False, 0

        try:
            await self._refresh()
            return self.is_available, len(self._cache)
        except Exception:
            return False, 0
