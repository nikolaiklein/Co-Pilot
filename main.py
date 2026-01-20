import os
import logging
from fastapi import FastAPI, Request
from telegram import Update
from dotenv import load_dotenv
from config.firebase_init import init_firebase
from services.db import DatabaseService
from services.telegram_bot import create_bot_app
from services.ai_engine import AIEngine
from services.analyzer import AnalyzerService

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
ai_engine = None
analyzer_service = None

# Событие запуска приложения
@app.on_event("startup")
async def startup_event():
    """
    Выполняется при старте приложения.
    Здесь происходит инициализация внешних сервисов: Firebase, DB, AI, Bot.
    """
    global db_service, bot_app, ai_engine, analyzer_service
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

    # 3. Инициализация AI Engine
    try:
        ai_engine = AIEngine()
        # Проверяем, удалось ли создать клиента (есть ли ключ)
        if not ai_engine.client:
            logger.warning("AI Engine инициализирован без клиента (нет API ключа).")
    except Exception as e:
        logger.error(f"Ошибка инициализации AIEngine: {e}")

    # 4. Инициализация Telegram Bot Application
    try:
        # Передаем db_service и ai_engine в функцию создания бота
        bot_app = await create_bot_app(db_service, ai_engine)
        if bot_app:
             await bot_app.start()
             logger.info("Telegram Bot запущен.")
        else:
             logger.warning("Bot Application не создано (возможно, нет токена).")
    except Exception as e:
        logger.error(f"Ошибка инициализации бота: {e}")

    # 5. Инициализация Analyzer Service
    try:
        if db_service and ai_engine:
            analyzer_service = AnalyzerService(db_service, ai_engine)
            logger.info("Analyzer Service инициализирован.")
        else:
            logger.warning("Analyzer Service не инициализирован (отсутствуют зависимости).")
    except Exception as e:
        logger.error(f"Ошибка инициализации Analyzer Service: {e}")

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
@app.post("/webhook/")
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
        await bot_app.process_update(update)

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ошибка при обработке вебхука: {e}")
        # Возвращаем 200 OK даже при ошибке, чтобы Telegram не слал повторные запросы бесконечно
        return {"status": "error", "message": str(e)}

@app.post("/cron/analyze")
async def analyze_user_cron(user_id: int):
    """
    Эндпоинт для запуска анализа профиля пользователя по расписанию.
    Cloud Scheduler должен делать POST запрос сюда с параметром user_id.

    Args:
        user_id (int): ID пользователя для анализа.

    Returns:
        dict: Результат анализа.
    """
    if not analyzer_service:
        return {"status": "error", "message": "Analyzer Service not initialized"}

    return await analyzer_service.analyze_user_profile(user_id)

@app.post("/cron/analyze-all")
async def analyze_all_users_cron():
    """
    Эндпоинт для ежедневного анализа ВСЕХ пользователей.
    Cloud Scheduler вызывает этот эндпоинт раз в сутки.

    Returns:
        dict: Статистика обработки.
    """
    if not analyzer_service or not db_service:
        return {"status": "error", "message": "Services not initialized"}

    try:
        # Получаем список всех пользователей
        user_ids = await db_service.get_all_user_ids()
        
        if not user_ids:
            return {"status": "ok", "message": "No users to analyze", "processed": 0}
        
        # Анализируем каждого пользователя
        results = {"success": 0, "failed": 0, "skipped": 0}
        
        for user_id in user_ids:
            try:
                result = await analyzer_service.analyze_user_profile(user_id)
                if result.get("status") == "success":
                    results["success"] += 1
                elif result.get("status") == "skipped":
                    results["skipped"] += 1
                else:
                    results["failed"] += 1
            except Exception as e:
                logger.error(f"Ошибка анализа пользователя {user_id}: {e}")
                results["failed"] += 1
        
        logger.info(f"Batch analysis complete: {results}")
        return {"status": "ok", "processed": len(user_ids), "results": results}
        
    except Exception as e:
        logger.error(f"Ошибка batch-анализа: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/cron/weekly-digest")
async def send_weekly_digest():
    """
    Эндпоинт для отправки еженедельных итогов всем пользователям.
    Cloud Scheduler вызывает этот эндпоинт раз в неделю (пятница).

    Returns:
        dict: Статистика отправки.
    """
    if not db_service or not ai_engine or not bot_app:
        return {"status": "error", "message": "Services not initialized"}

    try:
        user_ids = await db_service.get_all_user_ids()
        
        if not user_ids:
            return {"status": "ok", "message": "No users", "sent": 0}
        
        sent_count = 0
        
        for user_id in user_ids:
            try:
                # Получаем профиль пользователя
                user_data = await db_service.get_user(user_id)
                
                if not user_data or not user_data.get('profile_summary'):
                    continue  # Пропускаем пользователей без профиля
                
                profile = user_data.get('profile_summary', {})
                first_name = user_data.get('first_name', 'друг')
                
                # Формируем краткий дайджест
                digest_text = f"📊 <b>Твои итоги недели, {first_name}!</b>\n\n"
                
                if profile.get('summary'):
                    digest_text += f"📝 {profile['summary'][:200]}...\n\n" if len(profile.get('summary', '')) > 200 else f"📝 {profile['summary']}\n\n"
                
                if profile.get('dreams'):
                    dreams = profile['dreams'][:3]  # Первые 3 мечты
                    digest_text += "💭 <b>Твои цели:</b>\n"
                    for dream in dreams:
                        digest_text += f"  • {dream}\n"
                    digest_text += "\nКак продвигаешься? Напиши мне!"
                else:
                    digest_text += "Расскажи о своих целях, и я помогу их достичь! 🚀"
                
                # Отправляем сообщение через Telegram
                await bot_app.bot.send_message(
                    chat_id=user_id,
                    text=digest_text,
                    parse_mode="HTML"
                )
                sent_count += 1
                
            except Exception as e:
                logger.warning(f"Не удалось отправить дайджест пользователю {user_id}: {e}")
                continue
        
        logger.info(f"Weekly digest sent to {sent_count} users")
        return {"status": "ok", "sent": sent_count, "total": len(user_ids)}
        
    except Exception as e:
        logger.error(f"Ошибка отправки weekly digest: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    # Этот блок используется только для локальной отладки при прямом запуске файла python main.py
    # В продакшене приложение запускается через uvicorn (см. Dockerfile)
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
