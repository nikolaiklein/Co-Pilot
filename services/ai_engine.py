import os
import re
import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


# --- Системный промпт ---

def build_system_prompt(user_profile: dict = None, user_name: str = None, model_context: str = None, current_model: str = None) -> str:
    """
    Генерирует системный промпт, адаптированный под профиль пользователя.
    """
    bot_nickname = "Правильный Помощник"
    if user_profile and isinstance(user_profile, dict):
        bot_nickname = user_profile.get('bot_nickname', bot_nickname)

    base_prompt = f"""
Ты — персональный ИИ-ассистент "{bot_nickname}".
Ты — не просто чат-бот, ты цифровое зеркало пользователя.

## Твоя миссия
Помочь пользователю раскрыть потенциал: выявить навыки, структурировать опыт,
запомнить мечты и идеи, научить эффективно использовать современные ИИ-инструменты.
Интервьюируй, анализируй и превращай хаос мыслей в стратегию успеха.

## Твои роли
1. **Биограф**: Мягко вытягивай информацию о жизни. Изучай опыт и скрытые таланты
   через диалог. Запоминай мечты, желания, идеи.
2. **Второй Пилот**: Адаптируйся под пользователя — будь Коучем, Критиком или
   Исполнителем в зависимости от ситуации. Обучай через практику
   ("Давай сделаем это вместе, покажу как").
3. **Аналитик**: Строй "Карту Личности" и находи точки роста.
   Помогай с задачами, экономь время и энергию.

## Режимы работы (выбирай автоматически)
| Режим     | Когда использовать                                      | Как себя вести                                   |
|-----------|--------------------------------------------------------|--------------------------------------------------|
| INTERVIEW | Профиль пуст ИЛИ пользователь делится о себе           | Задавай 1 уточняющий вопрос, собирай информацию  |
| COACHING  | Вопрос "как сделать?", "помоги разобраться", "научи"   | Объясняй пошагово, показывай на примере          |
| EXECUTION | Запрос "сделай X", "напиши Y", конкретная задача       | Выполняй сразу, без лишних объяснений            |

## ВАЖНО: Честность о своих возможностях
НИКОГДА не говори что ты что-то сделал, если у тебя нет для этого механизма.
Ты можешь РЕАЛЬНО выполнять только действия через теги: [SWITCH_MODEL] и [VAULT_SAVE].
Всё остальное — ты только текстовый ассистент. Если пользователь просит что-то,
чего ты не можешь — честно скажи: "У меня пока нет такой функции" или
"Это нужно сделать вручную". НЕ притворяйся, что выполнил действие.

## Форматирование (Telegram HTML)
ВСЕГДА форматируй ответы для красивого чтения в Telegram:
• Используй <b>жирный</b> для заголовков, ключевых терминов и выводов
• Используй <i>курсив</i> для примеров, цитат и пояснений
• Используй <code>код</code> для команд, моделей и технических терминов
• Используй эмодзи для визуальной структуры: ✅ ❌ ⚡ 💡 📌 🎯 ⚠️
• Списки оформляй через • или нумерацию
• Разделяй смысловые блоки пустой строкой
• Пиши короткими абзацами (2-3 предложения)
• НЕ используй markdown-синтаксис (**, *, `, #) — ТОЛЬКО HTML-теги (<b>, <i>, <code>, <pre>)

## Правила общения
1. **Active Listening**: Сначала подтверди, что понял мысль собеседника, потом задай уточняющий вопрос.
2. **Один вопрос за раз**: Не перегружай собеседника множеством вопросов.
3. **Конкретика**: Без воды, уважительно, на равных.
4. **Контекст**: Помни всю историю беседы и используй её.
5. **Точки роста**: Если пользователь упоминает рутинную задачу — предложи автоматизацию через ИИ.

## Стиль
- Говори на языке собеседника.
- Адаптируйся к его манере общения.
- Будь профессионален, но дружелюбен.
- Используй понятные аналогии.

## Долговременная память
У тебя есть система долговременной памяти (RAG). Когда пользователь просит найти,
вспомнить или вытащить информацию из прошлых разговоров — ты МОЖЕШЬ это сделать.
Если в твоём контексте есть блок [КОНТЕКСТ ИЗ ДОЛГОВРЕМЕННОЙ ПАМЯТИ], используй его
для ответа. Если пользователь спрашивает о чём-то из прошлого, а контекста памяти нет —
предложи использовать команду /memory search <запрос> для точного поиска.

## Доступные команды (напоминай при необходимости)
- /myprofile — посмотреть своё досье
- /model — переключить AI-модель (40+ моделей: Gemini, Claude, DeepSeek, Kimi, Llama и др.)
- /vault — персональное хранилище (промпты, идеи, заметки)
- /memory search <запрос> — поиск по долговременной памяти
- /name — дать боту персональное имя
- /correct — исправить ошибку в профиле
- /clear — очистить историю диалога
- /help — список всех команд

## Хранилище (Vault)
У пользователя есть персональное хранилище для промптов, идей и заметок.
Когда пользователь ЯВНО просит сохранить что-то ("сохрани", "запиши", "запомни идею",
"сохрани промпт", "положи в хранилище"), добавь в конец ответа тег:
[VAULT_SAVE: тип | заголовок | содержимое]

Типы: prompt, idea, note
Примеры:
- "Сохрани промпт для анализа данных" → [VAULT_SAVE: prompt | Анализ данных | текст промпта]
- "Запиши идею: сделать бота" → [VAULT_SAVE: idea | Сделать бота | Идея — создать бота для автоматизации]
- "Сохрани это как заметку" → [VAULT_SAVE: note | Заголовок | содержимое заметки]

Правила:
- Тег ставь ТОЛЬКО при явном запросе на сохранение.
- НЕ ставь тег, если пользователь просто обсуждает хранилище или спрашивает о нём.
- Заголовок — короткий (до 60 символов). Содержимое — полный текст для сохранения.
- Если пользователь не указал заголовок, придумай краткий по контексту.
- Не объясняй тег пользователю. Просто скажи "Сохраняю..." и поставь тег.
Пользователь может посмотреть сохранённое через /vault.
"""

    has_profile = (
        user_profile and
        isinstance(user_profile, dict) and
        user_profile.get('profile_summary') and
        isinstance(user_profile.get('profile_summary'), dict) and
        any(user_profile['profile_summary'].get(k) for k in ['summary', 'interests', 'new_skills', 'dreams'])
    )

    mode_hint = ""
    if not has_profile:
        mode_hint = "\n## ТЕКУЩИЙ РЕЖИМ: INTERVIEW\nПрофиль пользователя пуст. Твоя задача — мягко познакомиться. Задавай по одному вопросу о жизни, интересах, целях.\n"

    personal_section = ""
    if user_name:
        personal_section += f"\n## Твой пользователь: {user_name}\n"

    if user_profile and isinstance(user_profile, dict):
        personal_section += "\n## ДОСЬЕ ПОЛЬЗОВАТЕЛЯ (Учитывай при ответе)\n"
        summary = user_profile.get('profile_summary', user_profile)
        if isinstance(summary, dict):
            if summary.get('summary'):
                personal_section += f"📌 **Портрет**: {summary['summary']}\n"
            if summary.get('interests') and isinstance(summary['interests'], list):
                personal_section += f"🎯 **Интересы**: {', '.join(summary['interests'])}\n"
            if summary.get('new_skills') and isinstance(summary['new_skills'], list):
                personal_section += f"🛠 **Навыки**: {', '.join(summary['new_skills'])}\n"
            if summary.get('pain_points') and isinstance(summary['pain_points'], list):
                personal_section += f"⚠️ **Боли/Проблемы**: {', '.join(summary['pain_points'])}\n"
            if summary.get('dreams') and isinstance(summary['dreams'], list):
                personal_section += f"💭 **Мечты**: {', '.join(summary['dreams'])}\n"
        elif isinstance(summary, str) and summary:
            personal_section += f"📝 **Заметки**: {summary}\n"

    # Инструкции по переключению моделей (только если model_context передан)
    model_switch_section = ""
    if model_context:
        model_switch_section = f"""
## Переключение моделей
Когда пользователь ЯВНО просит переключить модель (например, "переключи на Claude",
"поставь Kimi", "смени модель на GPT"), добавь в конец ответа тег:
[SWITCH_MODEL: model_id]

Доступные модели:
{model_context}

Правила:
- Используй тег ТОЛЬКО при явном запросе на переключение.
- НЕ используй тег, если пользователь просто упоминает модель в разговоре.
- НЕ используй тег в ответах на рекомендации — только описывай модели.
- Скажи "Переключаю на [название]" и поставь тег в конце.
- Не объясняй тег пользователю.
"""

    # Информация о текущей модели
    model_info = ""
    if current_model:
        model_info = f"\n## Текущая активная модель\nТы работаешь на модели: {current_model}\nКогда пользователь спрашивает 'какая у тебя модель' — отвечай эту информацию.\n"

    return base_prompt + mode_hint + personal_section + model_info + model_switch_section


# --- Абстрактный провайдер ---

class BaseProvider(ABC):
    """Базовый класс для AI провайдеров."""

    @abstractmethod
    async def generate(self, messages: list, system_prompt: str, temperature: float = 0.7) -> str:
        """Генерирует ответ на основе истории сообщений."""
        ...

    @abstractmethod
    async def analyze(self, prompt: str) -> str:
        """Анализирует контент по заданному промпту (без истории)."""
        ...

    async def transcribe_audio(self, file_bytes: bytes) -> str:
        """Транскрибирует аудио. По умолчанию не поддерживается."""
        return "[Транскрибация аудио не поддерживается этим провайдером]"

    async def analyze_image(self, image_bytes: bytes, prompt: str, system_prompt: str = "") -> str:
        """Анализирует изображение. По умолчанию не поддерживается."""
        return "[Анализ изображений не поддерживается этим провайдером]"


# --- Gemini ---

class GeminiProvider(BaseProvider):
    """Провайдер для Google Gemini API."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        from google import genai
        self.genai = genai
        self.types = genai.types
        self.client = genai.Client(api_key=api_key)
        self.model = model
        logger.info(f"GeminiProvider инициализирован (модель: {model})")

    async def generate(self, messages: list, system_prompt: str, temperature: float = 0.7) -> str:
        contents = []
        for msg in messages:
            role = "user" if msg['role'] == "user" else "model"
            contents.append(self.types.Content(
                role=role,
                parts=[self.types.Part.from_text(text=msg['content'])]
            ))

        config = self.types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
        )

        start_time = time.time()
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=config
        )
        latency_ms = int((time.time() - start_time) * 1000)

        # EC-4: логирование метаданных LLM-вызова
        usage = getattr(response, 'usage_metadata', None)
        finish_reason = getattr(response.candidates[0], 'finish_reason', None) if response.candidates else None
        logger.info(
            f"LLM call: model={self.model} "
            f"prompt_tokens={getattr(usage, 'prompt_token_count', '?') if usage else '?'} "
            f"completion_tokens={getattr(usage, 'candidates_token_count', '?') if usage else '?'} "
            f"finish_reason={finish_reason} "
            f"latency={latency_ms}ms"
        )

        return response.text if response.text else ""

    async def analyze(self, prompt: str) -> str:
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=[self.types.Content(
                role="user",
                parts=[self.types.Part.from_text(text=prompt)]
            )]
        )
        return response.text.strip() if response.text else ""

    async def transcribe_audio(self, file_bytes: bytes) -> str:
        audio_part = self.types.Part.from_bytes(data=file_bytes, mime_type="audio/ogg")
        prompt = "Дословно транскрибируй аудио в текст. Выведи ТОЛЬКО текст речи, без пояснений, без кавычек, без префиксов. Если неразборчиво — '[Не удалось распознать речь]'."

        # Используем gemini-2.0-flash для транскрипции — он стабильнее и не "думает вслух"
        transcribe_model = "gemini-2.0-flash"
        response = await self.client.aio.models.generate_content(
            model=transcribe_model,
            contents=[self.types.Content(
                role="user",
                parts=[self.types.Part.from_text(text=prompt), audio_part]
            )]
        )
        return response.text.strip() if response.text else "[Не удалось распознать речь]"

    async def analyze_image(self, image_bytes: bytes, prompt: str, system_prompt: str = "") -> str:
        image_part = self.types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        full_prompt = (system_prompt + "\n\n" + prompt) if system_prompt else prompt

        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=[self.types.Content(
                role="user",
                parts=[self.types.Part.from_text(text=full_prompt), image_part]
            )]
        )
        return response.text.strip() if response.text else ""


# --- Anthropic Claude ---

class ClaudeProvider(BaseProvider):
    """Провайдер для Anthropic Claude API."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        import anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        logger.info(f"ClaudeProvider инициализирован (модель: {model})")

    async def generate(self, messages: list, system_prompt: str, temperature: float = 0.7) -> str:
        api_messages = []
        for msg in messages:
            role = msg['role'] if msg['role'] in ('user', 'assistant') else 'user'
            api_messages.append({"role": role, "content": msg['content']})

        start_time = time.time()
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=api_messages,
            temperature=temperature,
        )
        latency_ms = int((time.time() - start_time) * 1000)

        # EC-4: логирование метаданных LLM-вызова
        usage = getattr(response, 'usage', None)
        logger.info(
            f"LLM call: model={self.model} "
            f"prompt_tokens={getattr(usage, 'input_tokens', '?') if usage else '?'} "
            f"completion_tokens={getattr(usage, 'output_tokens', '?') if usage else '?'} "
            f"finish_reason={response.stop_reason} "
            f"latency={latency_ms}ms"
        )

        return response.content[0].text if response.content else ""

    async def analyze(self, prompt: str) -> str:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip() if response.content else ""

    async def analyze_image(self, image_bytes: bytes, prompt: str, system_prompt: str = "") -> str:
        import base64
        b64 = base64.standard_b64encode(image_bytes).decode()
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt or "",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return response.content[0].text.strip() if response.content else ""


# --- OpenAI ---

class OpenAIProvider(BaseProvider):
    """Провайдер для OpenAI API."""

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        import openai
        self.client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model
        logger.info(f"OpenAIProvider инициализирован (модель: {model})")

    async def generate(self, messages: list, system_prompt: str, temperature: float = 0.7) -> str:
        api_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            role = msg['role'] if msg['role'] in ('user', 'assistant') else 'user'
            api_messages.append({"role": role, "content": msg['content']})

        start_time = time.time()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=api_messages,
            temperature=temperature,
        )
        latency_ms = int((time.time() - start_time) * 1000)

        # EC-4: логирование метаданных LLM-вызова
        usage = response.usage
        finish_reason = response.choices[0].finish_reason if response.choices else None
        logger.info(
            f"LLM call: model={response.model} "
            f"prompt_tokens={usage.prompt_tokens if usage else '?'} "
            f"completion_tokens={usage.completion_tokens if usage else '?'} "
            f"finish_reason={finish_reason} "
            f"latency={latency_ms}ms"
        )

        return response.choices[0].message.content or ""

    async def analyze(self, prompt: str) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return (response.choices[0].message.content or "").strip()

    async def transcribe_audio(self, file_bytes: bytes) -> str:
        import io
        audio_file = io.BytesIO(file_bytes)
        audio_file.name = "voice.ogg"
        response = await self.client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="ru",
        )
        return response.text.strip() if response.text else "[Не удалось распознать речь]"

    async def analyze_image(self, image_bytes: bytes, prompt: str, system_prompt: str = "") -> str:
        import base64
        b64 = base64.standard_b64encode(image_bytes).decode()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        })
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )
        return (response.choices[0].message.content or "").strip()


# --- OpenAI-совместимый провайдер (NVIDIA, MiniMax и др.) ---

class OpenAICompatibleProvider(BaseProvider):
    """Провайдер для любого OpenAI-совместимого API (NVIDIA, MiniMax, LiteLLM и др.)."""

    def __init__(self, api_key: str, model: str, base_url: str):
        import openai
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=50.0)
        self.model = model
        self._is_reasoning = self._check_reasoning(model)
        logger.info(f"OpenAICompatibleProvider инициализирован (модель: {model}, endpoint: {base_url})")

    @staticmethod
    def _check_reasoning(model: str) -> bool:
        """Проверяет, является ли модель reasoning-моделью."""
        reasoning_patterns = ["thinking", "reasoner"]
        return any(p in model.lower() for p in reasoning_patterns)

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Убирает блоки <think>...</think> из ответа reasoning-моделей."""
        return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    async def generate(self, messages: list, system_prompt: str, temperature: float = 0.7) -> str:
        api_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            role = msg['role'] if msg['role'] in ('user', 'assistant') else 'user'
            api_messages.append({"role": role, "content": msg['content']})

        kwargs = {"model": self.model, "messages": api_messages}
        if not self._is_reasoning:
            kwargs["temperature"] = temperature

        start_time = time.time()
        response = await self.client.chat.completions.create(**kwargs)
        latency_ms = int((time.time() - start_time) * 1000)

        # Логирование метаданных (Task 8)
        usage = response.usage
        finish_reason = response.choices[0].finish_reason if response.choices else None
        logger.info(
            f"LLM call: model={response.model} "
            f"prompt_tokens={usage.prompt_tokens if usage else '?'} "
            f"completion_tokens={usage.completion_tokens if usage else '?'} "
            f"finish_reason={finish_reason} "
            f"latency={latency_ms}ms"
        )

        content = response.choices[0].message.content or ""

        # Strip thinking tokens из reasoning-моделей
        if self._is_reasoning and content:
            content = self._strip_thinking(content)

        # Предупреждение при обрезанном ответе
        if finish_reason == "length" and content:
            content += "\n\n⚠️ _Ответ был обрезан из-за лимита модели._"

        return content

    async def analyze(self, prompt: str) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return (response.choices[0].message.content or "").strip()

    async def analyze_image(self, image_bytes: bytes, prompt: str, system_prompt: str = "") -> str:
        import base64
        b64 = base64.standard_b64encode(image_bytes).decode()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        })
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )
        return (response.choices[0].message.content or "").strip()


# --- Фабрика провайдеров ---

PROVIDER_MAP = {
    "gemini": GeminiProvider,
    "anthropic": ClaudeProvider,
    "openai": OpenAIProvider,
}

# Провайдеры с OpenAI-совместимым API
# Все модели NVIDIA NIM доступны через единый endpoint
OPENAI_COMPATIBLE_PROVIDERS = {
    "nvidia": {
        "env_key": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "meta/llama-4-maverick-17b-128e-instruct",
    },
    "litellm": {
        "env_key": "LITELLM_API_KEY",
        "base_url": os.getenv("LITELLM_BASE_URL", ""),
        "default_model": "gemini-2.5-flash",
    },
}

# Импорт модельных данных из единого источника (model_catalog.py)
from services.model_catalog import NVIDIA_MODELS, GEMINI_MODELS, DEFAULT_MODELS

def parse_model_string(model_string: str) -> tuple[str, str]:
    """
    Парсит строку модели в кортеж (provider, model).

    Поддерживает форматы:
    - 'gemini/gemini-2.5-flash' -> ('gemini', 'gemini-2.5-flash')
    - 'gemini' -> ('gemini', 'gemini-2.5-flash')  # дефолтная модель провайдера
    - 'kimi-k2' -> ('nvidia', 'moonshotai/kimi-k2-instruct')  # короткое имя NVIDIA модели
    - 'nvidia/moonshotai/kimi-k2-instruct' -> ('nvidia', 'moonshotai/kimi-k2-instruct')
    """
    ms = model_string.strip().lower()

    # Проверяем короткие имена Gemini моделей
    if ms in GEMINI_MODELS:
        return "gemini", GEMINI_MODELS[ms]

    # Проверяем короткие имена NVIDIA моделей (kimi-k2, qwen3.5-397b и т.д.)
    if ms in NVIDIA_MODELS:
        return "nvidia", NVIDIA_MODELS[ms]

    if '/' in ms:
        # nvidia/moonshotai/kimi-k2-instruct -> provider=nvidia, model=moonshotai/kimi-k2-instruct
        parts = ms.split('/', 1)
        provider = parts[0]
        model = parts[1]
        # Если провайдер nvidia и передали полное имя модели — используем как есть
        if provider in DEFAULT_MODELS or provider in OPENAI_COMPATIBLE_PROVIDERS:
            return provider, model
        # Иначе это может быть полное имя NVIDIA модели (org/model)
        # Проверяем, есть ли такая в NVIDIA_MODELS values
        if ms in NVIDIA_MODELS.values():
            return "nvidia", ms
        # Fallback: первая часть = provider
        return provider, model

    return ms, DEFAULT_MODELS.get(ms, ms)


def create_provider(provider_name: str, model: str) -> BaseProvider:
    """Создаёт экземпляр провайдера по имени."""
    # Стандартные провайдеры
    key_map = {
        "gemini": "GEMINI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }

    # OpenAI-совместимые провайдеры
    if provider_name in OPENAI_COMPATIBLE_PROVIDERS:
        config = OPENAI_COMPATIBLE_PROVIDERS[provider_name]
        api_key = os.getenv(config["env_key"])
        if not api_key:
            raise ValueError(f"API ключ {config['env_key']} не задан")
        # Для litellm base_url читаем из env динамически (может быть не задан при импорте)
        base_url = config["base_url"]
        if provider_name == "litellm":
            base_url = os.getenv("LITELLM_BASE_URL", "")
        if not base_url:
            raise ValueError(f"Base URL для {provider_name} не задан")
        return OpenAICompatibleProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
        )

    # Стандартные провайдеры
    env_var = key_map.get(provider_name)
    if not env_var:
        raise ValueError(f"Неизвестный провайдер: {provider_name}")

    api_key = os.getenv(env_var)
    if not api_key:
        raise ValueError(f"API ключ {env_var} не задан")

    cls = PROVIDER_MAP[provider_name]
    return cls(api_key=api_key, model=model)


# --- Главный движок ---

class AIEngine:
    """
    Мульти-провайдерный AI движок.
    Поддерживает Gemini, Claude, OpenAI. Выбор модели через DEFAULT_MODEL
    или переключение пользователем.
    """

    def __init__(self):
        self.providers: dict[str, BaseProvider] = {}
        self.default_provider_name: str = ""
        self.default_model: str = ""

        # Парсим DEFAULT_MODEL из env
        default = os.getenv("DEFAULT_MODEL", "gemini/gemini-2.5-flash")
        provider_name, model = parse_model_string(default)

        try:
            cache_key = f"{provider_name}:{model}"
            self.providers[cache_key] = create_provider(provider_name, model)
            self.default_provider_name = provider_name
            self.default_model = model
            logger.info(f"AI Engine: провайдер по умолчанию — {provider_name}/{model}")
        except ValueError as e:
            logger.warning(f"Не удалось создать провайдер по умолчанию: {e}")

        # Для обратной совместимости: self.client != None означает, что движок готов
        self.client = self.providers.get(f"{self.default_provider_name}:{self.default_model}")

    def get_provider(self, provider_name: str = None, model: str = None) -> BaseProvider:
        """Возвращает провайдер. Если не указан — дефолтный."""
        if not provider_name:
            provider_name = self.default_provider_name
        if not model:
            model = DEFAULT_MODELS.get(provider_name, "")

        # Ключ кеша: provider+model (разные модели одного провайдера = разные экземпляры)
        cache_key = f"{provider_name}:{model}"

        if cache_key not in self.providers:
            self.providers[cache_key] = create_provider(provider_name, model)

        return self.providers[cache_key]

    async def generate_response(
        self,
        user_id: int,
        user_text: str,
        history: list,
        user_profile: dict = None,
        user_name: str = None,
        provider_name: str = None,
        model: str = None,
        memory_context: str = None,
    ) -> str:
        """Генерирует ответ ИИ с учётом истории, профиля и контекста памяти."""
        try:
            provider = self.get_provider(provider_name, model)
        except ValueError as e:
            logger.error(f"Провайдер недоступен: {e}")
            return "Извините, сервис ИИ временно недоступен."

        try:
            system_prompt = build_system_prompt(user_profile, user_name)
            # Контекст памяти — отделяем от инструкций структурными тегами
            if memory_context:
                system_prompt = f"<instructions>\n{system_prompt}\n</instructions>\n\n<user_context>\n{memory_context}\n</user_context>"
            messages = list(history) + [{"role": "user", "content": user_text}]
            response = await provider.generate(messages, system_prompt)
            if response:
                return response
            logger.warning(f"Пустой ответ для пользователя {user_id}")
            return "Извините, я не смог сформировать ответ. Попробуйте еще раз."
        except Exception as e:
            # Не логируем полное сообщение ошибки — может содержать ключи API
            logger.error(f"Ошибка при генерации ответа для {user_id}: {type(e).__name__}")
            return "Произошла ошибка при обращении к ИИ. Пожалуйста, повторите попытку позже."

    async def transcribe_audio(self, file_bytes: bytes) -> str:
        """Транскрибирует аудио через текущий провайдер."""
        try:
            provider = self.get_provider()
            return await provider.transcribe_audio(file_bytes)
        except Exception as e:
            logger.error(f"Ошибка при транскрибации аудио: {e}")
            return "[Ошибка обработки аудио]"

    async def analyze_content(self, prompt: str) -> str:
        """Анализирует контент через дефолтный провайдер (Gemini). Не зависит от выбора пользователя."""
        try:
            # Всегда используем дефолтный провайдер — анализатор заточен под Gemini
            provider = self.get_provider(self.default_provider_name, self.default_model)
            return await provider.analyze(prompt)
        except Exception as e:
            logger.error(f"Ошибка при анализе контента: {e}")
            return f"[Ошибка анализа]"

    async def analyze_image(
        self,
        image_bytes: bytes,
        user_message: str = "",
        user_profile: dict = None,
        user_name: str = None,
        provider_name: str = None,
        model: str = None,
    ) -> str:
        """
        Анализирует изображение. Если выбранная модель не поддерживает vision,
        автоматически переключается на дефолтный Gemini.
        """
        try:
            from services.model_catalog import get_model_meta

            # Определяем, поддерживает ли выбранная модель vision
            use_provider = provider_name
            use_model = model
            fallback_note = ""

            if use_provider and use_model:
                meta = get_model_meta(use_model)
                if not meta.get("supports_vision", False):
                    # Модель не поддерживает vision — fallback на дефолтный Gemini
                    use_provider = self.default_provider_name
                    use_model = self.default_model
                    fallback_note = f"\n\n💡 _Модель {model} не поддерживает изображения. Использован {use_model}._"

            provider = self.get_provider(use_provider, use_model)
            system_prompt = build_system_prompt(user_profile, user_name)
            if user_message:
                prompt = f'Пользователь отправил изображение с сообщением: "{user_message}"\n\nОпиши что на изображении и ответь на сообщение пользователя.'
            else:
                prompt = "Пользователь отправил изображение без текста. Опиши что на нём и спроси, чем можешь помочь."
            result = await provider.analyze_image(image_bytes, prompt, system_prompt)
            return result + fallback_note
        except Exception as e:
            logger.error(f"Ошибка при анализе изображения: {type(e).__name__}")
            return "Произошла ошибка при обработке изображения."
