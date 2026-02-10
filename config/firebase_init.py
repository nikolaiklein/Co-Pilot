import firebase_admin
from firebase_admin import credentials
import google.auth
import os
import logging

# Настройка логирования для отслеживания процесса инициализации
logger = logging.getLogger(__name__)

def init_firebase():
    """
    Инициализирует Firebase Admin SDK.

    Эта функция пытается инициализировать приложение Firebase, используя
    стандартные учетные данные Google (Application Default Credentials).

    Приоритет:
    1. Переменная окружения GOOGLE_APPLICATION_CREDENTIALS (для локальной разработки).
    2. Учетные данные сервисного аккаунта, привязанного к ресурсу Cloud Run (для продакшена).
    """
    try:
        # Проверяем, инициализировано ли уже приложение, чтобы избежать ошибок при повторном вызове
        if not firebase_admin._apps:
            # Получаем стандартные учетные данные Google.
            # google.auth.default() автоматически ищет учетные данные в переменной окружения
            # GOOGLE_APPLICATION_CREDENTIALS или в метаданных сервиса (если в Cloud Run).
            creds, project_id = google.auth.default()

            # Инициализируем приложение Firebase с полученными учетными данными.
            # Если creds=None, Firebase попытается найти их самостоятельно, но явная передача
            # позволяет нам лучше контролировать процесс и логировать project_id.
            firebase_admin.initialize_app(credential=credentials.ApplicationDefault())

            logger.info(f"Firebase успешно инициализирован для проекта: {project_id}")
        else:
            logger.info("Firebase уже инициализирован.")

    except Exception as e:
        # Логируем ошибку, если инициализация не удалась.
        # Это критично, так как без Firebase приложение может не работать корректно.
        logger.error(f"Ошибка при инициализации Firebase: {e}")
        raise e
