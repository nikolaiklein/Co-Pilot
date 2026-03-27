"""
Статические данные моделей — единый источник правды.
Словари, константы и чистые функции. Без I/O, без кеша.
"""

import re

# --- Метаданные семейств моделей ---

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

# --- Паттерны фильтрации ---

FILTER_PATTERNS = [
    r"embedding",
    r"gemma-",         # Gemma убрана по решению владельца
    r"gemini-flash$",  # дубликат gemini-2.5-flash
    r"flash-latest$",  # дубликат
    r"lite-latest$",   # дубликат
    r"pro-latest$",    # дубликат
    r"-v1$",           # старые версии (claude-3.5-sonnet-v1)
]

# --- Модели провайдеров ---

NVIDIA_MODELS = {
    "llama-4-maverick": "meta/llama-4-maverick-17b-128e-instruct",
    "kimi-k2": "moonshotai/kimi-k2-instruct",
    "kimi-k2.5": "moonshotai/kimi-k2.5",
    "deepseek-v3.2": "deepseek-ai/deepseek-v3.2",
    "qwen3.5-397b": "qwen/qwen3.5-397b-a17b",
    "nemotron-ultra": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "mistral-large-3": "mistralai/mistral-large-3-675b-instruct-2512",
    "minimax-m2.5": "minimaxai/minimax-m2.5",
}

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
    "gemini": "gemini-3-flash-preview",
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "nvidia": "meta/llama-4-maverick-17b-128e-instruct",
    "litellm": "gemini-2.5-flash",
}

# --- Хинты для UI ---

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

# --- Порядок отображения семейств ---

FAMILY_ORDER = ["Gemini", "Claude", "DeepSeek", "Kimi", "NVIDIA", "Llama", "Mistral", "MiniMax", "Qwen", "Другие"]


# --- Чистые функции ---

def get_model_meta(model_id: str) -> dict:
    """Возвращает метаданные для модели по ID. Ищет по наиболее специфичному префиксу."""
    if model_id in MODEL_FAMILY_META:
        return MODEL_FAMILY_META[model_id]
    for prefix, meta in sorted(MODEL_FAMILY_META.items(), key=lambda x: -len(x[0])):
        if model_id.startswith(prefix):
            return meta
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


def should_filter(model_id: str) -> bool:
    """Проверяет, нужно ли отфильтровать модель из списка."""
    for pattern in FILTER_PATTERNS:
        if re.search(pattern, model_id):
            return True
    return False
