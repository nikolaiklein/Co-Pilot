import logging
from services.db import DatabaseService
from services.ai_engine import AIEngine

logger = logging.getLogger(__name__)

class AnalyzerService:
    """
    Сервис "Аналитическое ядро".
    Отвечает за фоновый анализ диалогов пользователя и обновление его профиля.
    """

    def __init__(self, db_service: DatabaseService, ai_engine: AIEngine):
        self.db_service = db_service
        self.ai_engine = ai_engine

    async def analyze_user_profile(self, user_id: int):
        """
        Выполняет анализ последних сообщений пользователя и обновляет его профиль.

        Args:
            user_id (int): Telegram User ID.
        """
        logger.info(f"Запуск анализа профиля для пользователя {user_id}...")

        try:
            # 1. Получаем историю сообщений (берем больше для анализа, например, 50)
            messages = await self.db_service.get_last_messages(user_id, limit=50)

            if not messages:
                logger.warning(f"Нет сообщений для анализа пользователя {user_id}.")
                return {"status": "skipped", "reason": "no messages"}

            # 2. Формируем текстовый лог диалога
            dialog_log = ""
            for msg in messages:
                role_ru = "Пользователь" if msg['role'] == 'user' else "Ассистент"
                dialog_log += f"{role_ru}: {msg['content']}\n"

            # 3. Формируем промпт для Gemini
            prompt = f"""
Проанализируй следующий диалог с мужчиной 1970 года рождения.
Задача: Обновить досье пользователя, выделив ключевые аспекты для оптимизации его работы и жизни.

Диалог:
{dialog_log}

Сформируй отчет в формате JSON со следующей структурой:
{{
  "new_skills": ["навык 1", "навык 2"],
  "interests": ["интерес 1", "интерес 2"],
  "pain_points": ["проблема 1", "проблема 2"],
  "dreams": ["мечта 1", "мечта 2"],
  "summary": "Краткое резюме текущего состояния и рекомендация."
}}
Если информации по какому-то пункту нет, оставь список пустым.
Ответ должен содержать только JSON код без лишнего текста и markdown форматирования (```json ... ``` не нужно).
"""

            # 4. Отправляем запрос в AI
            analysis_json_str = await self.ai_engine.analyze_content(prompt)

            # Попытаемся очистить ответ, если Gemini все же добавит markdown
            analysis_json_str = analysis_json_str.replace("```json", "").replace("```", "").strip()

            # 5. Сохраняем результат
            # Можно сохранить как строку или попытаться распарсить, если нужно работать как с объектом.
            # Для надежности сохраним как строку в поле profile_summary (или parsed json если получится)
            # Firestore умеет хранить map/dict, но если JSON битый, лучше сохранить как string.
            # Но мы хотим структурированное досье.

            import json
            try:
                profile_data = json.loads(analysis_json_str)
            except json.JSONDecodeError:
                logger.warning("Не удалось распарсить JSON ответа анализатора. Сохраняем как текст.")
                profile_data = {"raw_analysis": analysis_json_str}

            # Обновляем профиль в документе пользователя
            await self.db_service.update_user(user_id, {"profile_summary": profile_data})

            # Сохраняем в историю отчетов
            await self.db_service.save_report(user_id, profile_data)

            logger.info(f"Анализ профиля для {user_id} завершен.")
            return {"status": "success", "data": profile_data}

        except Exception as e:
            logger.error(f"Ошибка при анализе профиля {user_id}: {e}")
            return {"status": "error", "message": str(e)}
