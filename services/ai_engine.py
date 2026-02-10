import os
import logging
from google import genai
from google.genai import types

# Настройка логирования
logger = logging.getLogger(__name__)

# Системный промпт (System Instruction)
SYSTEM_PROMPT = """
Ты — эмпатичный ассистент-биограф и коуч для мужчины 1970 года рождения.
Твоя цель: Интервьюировать, выявлять навыки, помогать структурировать опыт и обучать работе с современными ИИ-инструментами.
Стиль общения: Уважительный, мужской, конкретный, без воды. Говори на русском языке.
Ты должен помнить контекст беседы и использовать его для построения глубоких и осмысленных диалогов.
"""

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
                model="gemini-1.5-pro",
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
                model="gemini-1.5-pro",
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

    async def generate_response(self, user_id: int, user_text: str, history: list) -> str:
        """
        Генерирует ответ с использованием модели Gemini 1.5 Pro.

        Args:
            user_id (int): ID пользователя (для логирования).
            user_text (str): Текущее сообщение пользователя.
            history (list): Список словарей с историей сообщений [{'role': 'user'/'assistant', 'content': '...'}].

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

            # Конфигурация генерации
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7, # Баланс между креативностью и точностью
            )

            # Выполняем запрос к модели асинхронно
            # google-genai SDK поддерживает async через client.aio
            response = await self.client.aio.models.generate_content(
                model="gemini-1.5-pro",
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
