"""
Каталог моделей LiteLLM — fetch, кеш, фильтрация, группировка.
Чистый сервис данных: без AI-вызовов, без записи в Firestore.
"""

import os
import re
import time
import logging
import asyncio

import aiohttp

logger = logging.getLogger(__name__)

# --- Model Metadata Layer (Task 4) ---
# Capabilities по семействам моделей. Используется для:
# - vision detection (Task 11)
# - reasoning model handling (Task 7)
# - context curation / prompt hints

MODEL_FAMILY_META = {
    "gemini": {
        "emoji": "✨",
        "label": "Gemini",
        "supports_vision": True,
        "is_reasoning": False,
        "context_window": 1_000_000,
        "description": "Модели Google Gemini. Очень большое контекстное окно, поддержка изображений и голоса. Отличный баланс скорости и качества.",
        "strengths": ["длинные тексты", "анализ изображений", "быстрые ответы", "мультимодальность"],
        "family_fallback_order": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"],
    },
    "claude": {
        "emoji": "🟣",
        "label": "Claude",
        "supports_vision": True,
        "is_reasoning": False,
        "context_window": 200_000,
        "description": "Модели Anthropic Claude. Глубокий анализ, творческое письмо, работа с большими документами. Поддержка изображений.",
        "strengths": ["глубокий анализ", "творческое письмо", "работа с документами", "точное следование инструкциям"],
        "family_fallback_order": [],
    },
    "gpt": {
        "emoji": "⚪",
        "label": "OpenAI",
        "supports_vision": True,
        "is_reasoning": False,
        "context_window": 128_000,
        "description": "Модели OpenAI GPT. Универсальные, хорошо справляются с разнообразными задачами. Поддержка изображений.",
        "strengths": ["универсальность", "кодирование", "анализ изображений", "следование инструкциям"],
        "family_fallback_order": [],
    },
    "llama": {
        "emoji": "🟢",
        "label": "Llama",
        "supports_vision": False,
        "is_reasoning": False,
        "context_window": 128_000,
        "description": "Модели Meta Llama. Открытые модели с хорошим качеством на разных задачах.",
        "strengths": ["открытая модель", "общие задачи", "мультиязычность"],
        "family_fallback_order": [],
    },
    "kimi": {
        "emoji": "🔵",
        "label": "Kimi",
        "supports_vision": False,
        "is_reasoning": False,
        "context_window": 128_000,
        "description": "Модели Moonshot Kimi. Сильны в анализе, рассуждениях и работе с длинными текстами.",
        "strengths": ["рассуждения", "анализ", "длинные тексты"],
        "family_fallback_order": ["kimi-k2", "kimi-k2.5"],
    },
    "kimi-k2-thinking": {
        "emoji": "🔵",
        "label": "Kimi",
        "supports_vision": False,
        "is_reasoning": True,
        "context_window": 128_000,
        "description": "Kimi K2 в режиме глубокого мышления. Думает дольше, но глубже анализирует.",
        "strengths": ["глубокий анализ", "сложные задачи", "рассуждения"],
        "family_fallback_order": [],
    },
    "deepseek": {
        "emoji": "🟠",
        "label": "DeepSeek",
        "supports_vision": False,
        "is_reasoning": False,
        "context_window": 64_000,
        "description": "Модели DeepSeek. Сильны в кодировании и технических задачах.",
        "strengths": ["кодирование", "технические задачи", "математика"],
        "family_fallback_order": [],
    },
    "deepseek-reasoner": {
        "emoji": "🟠",
        "label": "DeepSeek",
        "supports_vision": False,
        "is_reasoning": True,
        "context_window": 64_000,
        "description": "DeepSeek Reasoner — модель глубокого мышления. Решает сложные логические задачи.",
        "strengths": ["логика", "математика", "сложные рассуждения"],
        "family_fallback_order": [],
    },
    "qwen": {
        "emoji": "🟤",
        "label": "Qwen",
        "supports_vision": False,
        "is_reasoning": False,
        "context_window": 128_000,
        "description": "Модели Alibaba Qwen. Мощные модели с хорошим качеством на разных задачах.",
        "strengths": ["общие задачи", "мультиязычность", "кодирование"],
        "family_fallback_order": [],
    },
    "nemotron": {
        "emoji": "🟢",
        "label": "NVIDIA",
        "supports_vision": False,
        "is_reasoning": False,
        "context_window": 128_000,
        "description": "Модели NVIDIA Nemotron. Оптимизированы для инструкций и диалогов.",
        "strengths": ["следование инструкциям", "диалоги"],
        "family_fallback_order": [],
    },
    "mistral": {
        "emoji": "🔴",
        "label": "Mistral",
        "supports_vision": False,
        "is_reasoning": False,
        "context_window": 128_000,
        "description": "Модели Mistral AI. Мощная европейская модель, хороша для аналитики и текстов.",
        "strengths": ["аналитика", "тексты", "мультиязычность"],
        "family_fallback_order": [],
    },
    "minimax": {
        "emoji": "🟡",
        "label": "MiniMax",
        "supports_vision": False,
        "is_reasoning": False,
        "context_window": 128_000,
        "description": "Модели MiniMax. Новые модели с хорошим качеством генерации.",
        "strengths": ["генерация текста", "общие задачи"],
        "family_fallback_order": [],
    },
}

# Модели, которые фильтруем из списка (не чат-модели, дубликаты, мелкие)
FILTER_PATTERNS = [
    r"embedding",
    r"gemma-",         # Gemma убрана по решению владельца
    r"gemini-flash$",  # дубликат gemini-2.5-flash
    r"flash-latest$",  # дубликат
    r"lite-latest$",   # дубликат
    r"pro-latest$",    # дубликат
    r"-v1$",           # старые версии (claude-3.5-sonnet-v1)
]

# --- Модели провайдеров (единый источник правды) ---

# Модели доступные через NVIDIA NIM (проверенные, рабочие)
NVIDIA_MODELS = {
    "mistral-large-3": "mistralai/mistral-large-3-675b-instruct-2512",
    "qwen3.5-397b": "qwen/qwen3.5-397b-a17b",
    "deepseek-v3.2": "deepseek-ai/deepseek-v3.2",
    "kimi-k2": "moonshotai/kimi-k2-instruct",
    "llama-4-maverick": "meta/llama-4-maverick-17b-128e-instruct",
}

# Порядок ротации NVIDIA моделей (от мощных к быстрым)
NVIDIA_FALLBACK_CHAIN = [
    "mistralai/mistral-large-3-675b-instruct-2512",
    "qwen/qwen3.5-397b-a17b",
    "deepseek-ai/deepseek-v3.2",
    "moonshotai/kimi-k2-instruct",
    "meta/llama-4-maverick-17b-128e-instruct",
]

# Доступные Gemini модели (проверенные, рабочие)
GEMINI_MODELS = {
    "gemini-3-flash": "gemini-3-flash-preview",
    "gemini-3-pro": "gemini-3-pro-preview",
    "gemini-3.1-pro": "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite": "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash-lite": "gemini-2.5-flash-lite",
    "gemini-2.0-flash": "gemini-2.0-flash",
}

DEFAULT_MODELS = {
    "nvidia": "mistralai/mistral-large-3-675b-instruct-2512",
    "gemini": "gemini-3-flash-preview",
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "litellm": "gemini-2.5-flash",
}

# Модели с хинтами для UI
MODEL_HINTS = {
    "gemini-2.5-flash": "⚡ быстрая",
    "gemini-2.5-pro": "⭐ мощная",
    "gemini-2.5-flash-lite": "⚡ лёгкая",
    "gemini-2.0-flash": "⚡ стабильная",
    "gemini-3-flash-preview": "⚡ новая",
    "gemini-3-pro-preview": "⭐ новая",
    "gemini-3.1-pro-preview": "⭐ новейшая",
    "claude-opus-4.6": "⭐ топ",
    "claude-sonnet-4.6": "⭐ баланс",
    "claude-haiku-4.5": "⚡ быстрая",
    "claude-opus-4.5": "⭐ мощная",
    "claude-sonnet-4.5": "баланс",
    "claude-sonnet-4": "баланс",
    "claude-opus-4": "⭐ мощная",
    "claude-3.5-sonnet": "баланс",
    "claude-3.5-haiku": "⚡ быстрая",
    "deepseek-chat-direct": "💬 чат",
    "deepseek-reasoner-direct": "🧠 reasoning",
    "kimi-k2": "💬 чат",
    "kimi-k2-thinking": "🧠 reasoning",
    "llama-4-maverick": "⭐ мета",
    "mistral-large-3": "⭐ мощная",
    "minimax-m2.5": "⭐ новая",
    "minimax-m2.1": "баланс",
}


def get_model_meta(model_id: str) -> dict:
    """Возвращает метаданные для модели по ID. Ищет по наиболее специфичному префиксу."""
    # Сначала проверяем точные совпадения (reasoning модели)
    if model_id in MODEL_FAMILY_META:
        return MODEL_FAMILY_META[model_id]
    # Потом по префиксу
    for prefix, meta in sorted(MODEL_FAMILY_META.items(), key=lambda x: -len(x[0])):
        if model_id.startswith(prefix):
            return meta
    # Дефолт для неизвестных
    return {
        "emoji": "⬜",
        "label": "Другие",
        "supports_vision": False,
        "is_reasoning": False,
        "context_window": 32_000,
    }


def get_family_key(model_id: str) -> str:
    """Определяет ключ семейства модели для группировки."""
    meta = get_model_meta(model_id)
    return meta["label"]


def _should_filter(model_id: str) -> bool:
    """Проверяет, нужно ли отфильтровать модель."""
    for pattern in FILTER_PATTERNS:
        if re.search(pattern, model_id):
            return True
    return False


# --- Model Catalog Service (Task 5) ---

# Порядок отображения семейств
FAMILY_ORDER = ["Gemini", "Claude", "DeepSeek", "Kimi", "NVIDIA", "Llama", "Mistral", "MiniMax", "Qwen", "Другие"]


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
