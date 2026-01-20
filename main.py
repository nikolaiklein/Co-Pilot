import os
import logging
from fastapi import FastAPI
from dotenv import load_dotenv
from config.firebase_init import init_firebase

# Настройка базового логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения из файла .env (если он существует)
# Это полезно для локальной разработки.
load_dotenv()

# Инициализация FastAPI приложения
# title и version помогают при генерации документации Swagger UI
app = FastAPI(
    title="Telegram Bot API",
    description="Backend for Telegram Bot using FastAPI and Firebase",
    version="1.0.0"
)

# Событие запуска приложения
@app.on_event("startup")
async def startup_event():
    """
    Выполняется при старте приложения.
    Здесь происходит инициализация внешних сервисов, например, Firebase.
    """
    logger.info("Запуск приложения...")
    try:
        # Инициализируем Firebase при старте
        # Мы оборачиваем это в try-except, чтобы приложение не падало сразу,
        # если нет кредов (хотя в проде это может быть критично).
        # В данном случае, просто логируем, так как кредов может не быть в sandbox.
        init_firebase()
    except Exception as e:
        logger.warning(f"Не удалось инициализировать Firebase (возможно, отсутствуют учетные данные): {e}")

@app.get("/")
async def health_check():
    """
    Простой эндпоинт для проверки работоспособности сервиса.
    Cloud Run использует этот эндпоинт, чтобы понять, готов ли контейнер принимать трафик.

    Returns:
        dict: Статус приложения.
    """
    return {"status": "alive"}

if __name__ == "__main__":
    # Этот блок используется только для локальной отладки при прямом запуске файла python main.py
    # В продакшене приложение запускается через uvicorn (см. Dockerfile)
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
