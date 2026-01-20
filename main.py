import os
import logging
from fastapi import FastAPI, Request
from telegram import Update
from dotenv import load_dotenv
from config.firebase_init import init_firebase
from services.db import DatabaseService
from services.telegram_bot import create_bot_app

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

# Глобальные переменные для сервисов
db_service = None
bot_app = None

# Событие запуска приложения
@app.on_event("startup")
async def startup_event():
    """
    Выполняется при старте приложения.
    Здесь происходит инициализация внешних сервисов: Firebase, DB, Bot.
    """
    global db_service, bot_app
    logger.info("Запуск приложения...")

    # 1. Инициализация Firebase Admin
    try:
        init_firebase()
    except Exception as e:
        logger.warning(f"Не удалось инициализировать Firebase (возможно, отсутствуют учетные данные): {e}")

    # 2. Инициализация сервиса базы данных
    try:
        db_service = DatabaseService()
        await db_service.initialize()
    except Exception as e:
        logger.error(f"Ошибка инициализации DatabaseService: {e}")

    # 3. Инициализация Telegram Bot Application
    try:
        bot_app = await create_bot_app()
        if bot_app:
             await bot_app.start()
             logger.info("Telegram Bot запущен.")
        else:
             logger.warning("Bot Application не создано (возможно, нет токена).")
    except Exception as e:
        logger.error(f"Ошибка инициализации бота: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """
    Выполняется при остановке приложения.
    Корректное завершение работы бота.
    """
    global bot_app
    if bot_app:
        logger.info("Остановка Telegram Bot...")
        await bot_app.stop()
        await bot_app.shutdown()

@app.get("/")
async def health_check():
    """
    Простой эндпоинт для проверки работоспособности сервиса.
    Cloud Run использует этот эндпоинт, чтобы понять, готов ли контейнер принимать трафик.

    Returns:
        dict: Статус приложения.
    """
    return {"status": "alive"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Эндпоинт для получения обновлений от Telegram (Webhook).

    Args:
        request (Request): Входящий HTTP-запрос.

    Returns:
        dict: Статус обработки ('ok').
    """
    if not bot_app:
        logger.error("Bot Application не инициализировано. Игнорируем апдейт.")
        return {"status": "error", "message": "Bot not initialized"}

    try:
        # Получаем JSON из запроса
        data = await request.json()

        # Преобразуем JSON в объект Update библиотеки python-telegram-bot
        update = Update.de_json(data, bot_app.bot)

        # Обрабатываем обновление асинхронно
        # await bot_app.process_update(update) # В v20+ process_update уже async? Да.
        # В документации PTB v20: await application.process_update(update)
        await bot_app.process_update(update)

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ошибка при обработке вебхука: {e}")
        # Возвращаем 200 OK даже при ошибке, чтобы Telegram не слал повторные запросы бесконечно
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    # Этот блок используется только для локальной отладки при прямом запуске файла python main.py
    # В продакшене приложение запускается через uvicorn (см. Dockerfile)
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
