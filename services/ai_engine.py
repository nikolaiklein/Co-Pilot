import os
import logging
from google import genai
from google.genai import types

# Настройка логирования
logger = logging.getLogger(__name__)

# Системный промпт (System Instruction)
# Теперь это функция, которая динамически генерирует промпт на основе профиля пользователя

def build_system_prompt(user_profile: dict = None, user_name: str = None) -> str:
    """
    Генерирует системный промпт, адаптированный под профиль пользователя.
    
    Args:
        user_profile: Данные профиля пользователя (profile_summary из Firestore)
        user_name: Имя пользователя (first_name из Telegram)
    
    Returns:
        str: Персонализированный системный промпт
    """
    # Определяем имя бота (пользователь мог дать своё)
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
Анализируй сообщение пользователя и выбирай подходящий режим:

| Режим     | Когда использовать                                      | Как себя вести                                   |
|-----------|--------------------------------------------------------|--------------------------------------------------|
| INTERVIEW | Профиль пуст ИЛИ пользователь делится о себе           | Задавай 1 уточняющий вопрос, собирай информацию  |
| COACHING  | Вопрос "как сделать?", "помоги разобраться", "научи"   | Объясняй пошагово, показывай на примере          |
| EXECUTION | Запрос "сделай X", "напиши Y", конкретная задача       | Выполняй сразу, без лишних объяснений            |

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

## Доступные команды (напоминай при необходимости)
- /myprofile — посмотреть своё досье
- /name — дать боту персональное имя
- /correct — исправить ошибку в профиле
- /clear — очистить историю диалога
- /help — список всех команд
"""

    # Определяем режим на основе профиля
    has_profile = (
        user_profile and 
        isinstance(user_profile, dict) and 
        user_profile.get('profile_summary') and
        isinstance(user_profile.get('profile_summary'), dict) and
        any(user_profile['profile_summary'].get(k) for k in ['summary', 'interests', 'new_skills', 'dreams'])
    )
    
    # Добавляем подсказку о режиме
    mode_hint = ""
    if not has_profile:
        mode_hint = "\n## ТЕКУЩИЙ РЕЖИМ: INTERVIEW\nПрофиль пользователя пуст. Твоя задача — мягко познакомиться. Задавай по одному вопросу о жизни, интересах, целях.\n"
    
    # Персонализированная секция (Dynamic Persona)
    personal_section = ""
    
    if user_name:
        personal_section += f"\n## Твой пользователь: {user_name}\n"
    
    if user_profile and isinstance(user_profile, dict):
        personal_section += "\n## ДОСЬЕ ПОЛЬЗОВАТЕЛЯ (Учитывай при ответе)\n"
        
        # Извлекаем данные из profile_summary
        summary = user_profile.get('profile_summary', user_profile)
        
        if isinstance(summary, dict):
            if summary.get('summary'):
                personal_section += f"📌 **Портрет**: {summary['summary']}\n"
            if summary.get('interests'):
                interests = summary['interests']
                if isinstance(interests, list):
                    personal_section += f"🎯 **Интересы**: {', '.join(interests)}\n"
            if summary.get('new_skills'):
                skills = summary['new_skills']
                if isinstance(skills, list):
                    personal_section += f"🛠 **Навыки**: {', '.join(skills)}\n"
            if summary.get('pain_points'):
                pains = summary['pain_points']
                if isinstance(pains, list):
                    personal_section += f"⚠️ **Боли/Проблемы**: {', '.join(pains)}\n"
            if summary.get('dreams'):
                dreams = summary['dreams']
                if isinstance(dreams, list):
                    personal_section += f"💭 **Мечты**: {', '.join(dreams)}\n"
        elif isinstance(summary, str) and summary:
            personal_section += f"📝 **Заметки**: {summary}\n"
    
    return base_prompt + mode_hint + personal_section

class AIEngine:
    """
    Класс для работы с Gemini API через google-genai SDK.
    Отвечает за генерацию ответов на основе истории переписки.
    """

    def __init__(self):
        """
        Инициализация клиента Gemini.
        API ключ берется из переменной окружения GEMINI_API_KEY (или GOOGLE_API_KEY).
        """
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY или GOOGLE_API_KEY не найдены. AI Engine не будет работать.")
            self.client = None
        else:
            try:
                self.client = genai.Client(api_key=api_key)
                logger.info("Gemini Client успешно инициализирован.")
            except Exception as e:
                logger.error(f"Ошибка при инициализации Gemini Client: {e}")
                self.client = None

    async def transcribe_audio(self, file_bytes: bytes) -> str:
        """
        Транскрибирует аудиофайл в текст, используя Gemini.

        Args:
            file_bytes (bytes): Содержимое аудиофайла.

        Returns:
            str: Расшифрованный текст.
        """
        if not self.client:
            return "Ошибка: API ключ не настроен."

        try:
            # Создаем объект Part из байтов аудио
            # Для Telegram voice messages формат обычно OGG (Opus)
            # Gemini поддерживает audio/ogg
            audio_part = types.Part.from_bytes(data=file_bytes, mime_type="audio/ogg")

            # Отправляем запрос только на транскрибацию
            prompt = "Пожалуйста, дословно транскрибируй этот аудиофайл в текст. Если аудио пустое или неразборчивое, напиши '[Не удалось распознать речь]'."

            response = await self.client.aio.models.generate_content(
                model="gemini-3-pro-preview",
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=prompt),
                            audio_part
                        ]
                    )
                ]
            )

            if response.text:
                return response.text.strip()
            else:
                return "[Не удалось распознать речь]"

        except Exception as e:
            logger.error(f"Ошибка при транскрибации аудио: {e}")
            return "[Ошибка обработки аудио]"

    async def analyze_content(self, prompt: str) -> str:
        """
        Анализирует контент на основе переданного промпта без истории чата.

        Args:
            prompt (str): Промпт для анализа.

        Returns:
            str: Результат анализа.
        """
        if not self.client:
            return "Ошибка: API ключ не настроен."

        try:
            # Для анализа не нужна системная инструкция "биографа",
            # так как мы явно задаем задачу в промпте.
            # Но можно оставить дефолтную или переопределить.
            # Для простоты используем generate_content с промптом как user message.

            response = await self.client.aio.models.generate_content(
                model="gemini-3-pro-preview",
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=prompt)]
                    )
                ]
            )

            if response.text:
                return response.text.strip()
            else:
                return "[Не удалось получить результат анализа]"

        except Exception as e:
            logger.error(f"Ошибка при анализе контента: {e}")
            return f"[Ошибка анализа: {e}]"

    async def generate_response(self, user_id: int, user_text: str, history: list, user_profile: dict = None, user_name: str = None) -> str:
        """
        Генерирует ответ с использованием модели Gemini 1.5 Pro.

        Args:
            user_id (int): ID пользователя (для логирования).
            user_text (str): Текущее сообщение пользователя.
            history (list): Список словарей с историей сообщений [{'role': 'user'/'assistant', 'content': '...'}].
            user_profile (dict, optional): Профиль пользователя для персонализации.
            user_name (str, optional): Имя пользователя.

        Returns:
            str: Текст ответа от ИИ.
        """
        if not self.client:
            return "Извините, сервис ИИ временно недоступен (API ключ не настроен)."

        try:
            # Формируем историю сообщений в формате, который принимает API
            # API ожидает список объектов Content или словарей.
            # user -> user
            # assistant -> model
            contents = []

            for msg in history:
                role = "user" if msg['role'] == "user" else "model"
                contents.append(types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=msg['content'])]
                ))

            # Добавляем текущее сообщение пользователя в конец истории
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_text)]
            ))

            # Генерируем системный промпт динамически
            system_instruction = build_system_prompt(user_profile, user_name)

            # Конфигурация генерации
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.7, # Баланс между креативностью и точностью
            )

            # Выполняем запрос к модели асинхронно
            # google-genai SDK поддерживает async через client.aio
            response = await self.client.aio.models.generate_content(
                model="gemini-3-pro-preview",
                contents=contents,
                config=config
            )

            if response.text:
                return response.text
            else:
                logger.warning(f"Пустой ответ от Gemini для пользователя {user_id}")
                return "Извините, я не смог сформировать ответ. Попробуйте еще раз."

        except Exception as e:
            logger.error(f"Ошибка при генерации ответа для {user_id}: {e}")
            return "Произошла ошибка при обращении к ИИ. Пожалуйста, повторите попытку позже."
