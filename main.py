import os
import logging
import sys
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from telegram import Update
from dotenv import load_dotenv
from config.firebase_init import init_firebase
from dependencies import ADMIN_USER_ID, require_cron_secret
from services.db import DatabaseService
from services.telegram_bot import create_bot_app
from services.ai_engine import AIEngine
from services.analyzer import AnalyzerService
from services.memory_c60 import MemoryC60Service, _fire_and_forget
from services.model_catalog import ModelCatalog

# Настройка логирования — JSON для Cloud Run, обычный для локальной разработки
def _setup_logging():
    """Инициализирует логирование. JSON-формат если есть python-json-logger."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    try:
        from pythonjsonlogger.json import JsonFormatter
        formatter = JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "severity"},
        )
        handler.setFormatter(formatter)
    except ImportError:
        # Fallback: обычные текстовые логи (для локальной разработки)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    root_logger.handlers.clear()
    root_logger.addHandler(handler)

_setup_logging()
logger = logging.getLogger(__name__)

# Загрузка переменных окружения из файла .env (если он существует)
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager — замена deprecated on_event("startup"/"shutdown").
    Инициализирует сервисы при старте и корректно завершает при остановке.
    """
    # === STARTUP ===
    logger.info("Запуск приложения...")

    # 1. Firebase Admin
    try:
        init_firebase()
    except Exception as e:
        logger.warning(f"Не удалось инициализировать Firebase (возможно, отсутствуют учетные данные): {e}")

    # 2. Database Service
    try:
        app.state.db = DatabaseService()
        await app.state.db.initialize()
    except Exception as e:
        logger.error(f"Ошибка инициализации DatabaseService: {e}")
        app.state.db = None

    # 3. AI Engine
    try:
        app.state.ai_engine = AIEngine()
        if not app.state.ai_engine.client:
            logger.warning("AI Engine инициализирован без клиента (нет API ключа).")
    except Exception as e:
        logger.error(f"Ошибка инициализации AIEngine: {e}")
        app.state.ai_engine = None

    # 4. Memory Service (C60 Fullerene + Qdrant)
    try:
        gemini_key = os.getenv("GEMINI_API_KEY")
        qdrant_url = os.getenv("QDRANT_URL")
        qdrant_api_key = os.getenv("QDRANT_API_KEY")
        if gemini_key:
            app.state.memory = MemoryC60Service(
                gemini_api_key=gemini_key,
                qdrant_url=qdrant_url,
                qdrant_api_key=qdrant_api_key,
                db=app.state.db,
            )
            logger.info("Memory Service (C60 Fullerene) инициализирован.")
        else:
            app.state.memory = None
            logger.warning("Memory Service не инициализирован (нет GEMINI_API_KEY).")
    except Exception as e:
        logger.error(f"Ошибка инициализации Memory Service: {e}")
        app.state.memory = None

    # 5. Analyzer Service
    try:
        if app.state.db and app.state.ai_engine:
            app.state.analyzer = AnalyzerService(app.state.db, app.state.ai_engine)
            logger.info("Analyzer Service инициализирован.")
        else:
            app.state.analyzer = None
            logger.warning("Analyzer Service не инициализирован (отсутствуют зависимости).")
    except Exception as e:
        logger.error(f"Ошибка инициализации Analyzer Service: {e}")
        app.state.analyzer = None

    # 5.5. Model Catalog (LiteLLM)
    try:
        app.state.model_catalog = ModelCatalog()
        available, count = await app.state.model_catalog.check_health()
        app.state.litellm_available = available
        if available:
            logger.info(f"LiteLLM доступен: {count} моделей")
        else:
            litellm_url = os.getenv("LITELLM_BASE_URL", "")
            if litellm_url:
                logger.warning(f"LiteLLM недоступен ({litellm_url})")
            else:
                logger.info("LiteLLM не настроен (LITELLM_BASE_URL пуст)")
    except Exception as e:
        logger.warning(f"Ошибка инициализации Model Catalog: {e}")
        app.state.model_catalog = ModelCatalog()
        app.state.litellm_available = False

    # 6. Telegram Bot Application
    try:
        app.state.bot_app = await create_bot_app(
            app.state.db, app.state.ai_engine, app.state.analyzer, app.state.memory,
            model_catalog=app.state.model_catalog
        )
        if app.state.bot_app:
            await app.state.bot_app.start()
            logger.info("Telegram Bot запущен.")
        else:
            logger.warning("Bot Application не создано (возможно, нет токена).")
    except Exception as e:
        logger.error(f"Ошибка инициализации бота: {e}")
        app.state.bot_app = None

    yield  # --- приложение работает ---

    # === SHUTDOWN ===
    if getattr(app.state, "memory", None):
        await app.state.memory.close()
    if getattr(app.state, "bot_app", None):
        logger.info("Остановка Telegram Bot...")
        await app.state.bot_app.stop()
        await app.state.bot_app.shutdown()


# Инициализация FastAPI с lifespan
app = FastAPI(
    title="Telegram Bot API",
    description="Backend for Telegram Bot using FastAPI and Firebase",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def health_check(request: Request):
    """
    Простой эндпоинт для проверки работоспособности сервиса.
    Cloud Run использует этот эндпоинт, чтобы понять, готов ли контейнер принимать трафик.
    """
    result = {"status": "alive"}
    catalog = getattr(request.app.state, "model_catalog", None)
    if catalog:
        result["litellm"] = getattr(request.app.state, "litellm_available", False)
        result["litellm_models"] = len(catalog._cache)
    return result


@app.post("/webhook")
@app.post("/webhook/")
async def telegram_webhook(request: Request):
    """
    Эндпоинт для получения обновлений от Telegram (Webhook).
    """
    bot_app = request.app.state.bot_app
    if not bot_app:
        logger.error("Bot Application не инициализировано. Игнорируем апдейт.")
        return {"status": "error", "message": "Bot not initialized"}

    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ошибка при обработке вебхука: {e}")
        # EC-6: HTTP 500 для транзиентных ошибок — Telegram повторит запрос
        # EC-8: Не утечка внутренних деталей в ответе
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Internal processing error"}
        )


@app.post("/cron/analyze", dependencies=[Depends(require_cron_secret)])
async def analyze_user_cron(request: Request, user_id: int):
    """
    Эндпоинт для запуска анализа профиля пользователя по расписанию.
    """
    analyzer = request.app.state.analyzer
    if not analyzer:
        return {"status": "error", "message": "Analyzer Service not initialized"}

    return await analyzer.analyze_user_profile(user_id)


@app.post("/cron/analyze-all", dependencies=[Depends(require_cron_secret)])
async def analyze_all_users_cron(request: Request):
    """
    Эндпоинт для ежедневного анализа ВСЕХ пользователей.
    """
    analyzer = request.app.state.analyzer
    db = request.app.state.db
    if not analyzer or not db:
        return {"status": "error", "message": "Services not initialized"}

    try:
        user_ids = await db.get_all_user_ids()

        if not user_ids:
            return {"status": "ok", "message": "No users to analyze", "processed": 0}

        results = {"success": 0, "failed": 0, "skipped": 0}

        for user_id in user_ids:
            try:
                result = await analyzer.analyze_user_profile(user_id)
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


@app.post("/cron/weekly-digest", dependencies=[Depends(require_cron_secret)])
async def send_weekly_digest(request: Request):
    """
    Эндпоинт для отправки еженедельных итогов всем пользователям.
    """
    db = request.app.state.db
    ai_engine = request.app.state.ai_engine
    bot_app = request.app.state.bot_app
    if not db or not ai_engine or not bot_app:
        return {"status": "error", "message": "Services not initialized"}

    try:
        user_ids = await db.get_all_user_ids()

        if not user_ids:
            return {"status": "ok", "message": "No users", "sent": 0}

        sent_count = 0

        for user_id in user_ids:
            try:
                user_data = await db.get_user(user_id)

                if not user_data or not user_data.get('profile_summary'):
                    continue

                profile = user_data.get('profile_summary', {})
                first_name = user_data.get('first_name', 'друг')

                digest_text = f"📊 <b>Твои итоги недели, {first_name}!</b>\n\n"

                if profile.get('summary'):
                    digest_text += f"📝 {profile['summary'][:200]}...\n\n" if len(profile.get('summary', '')) > 200 else f"📝 {profile['summary']}\n\n"

                if profile.get('dreams'):
                    dreams = profile['dreams'][:3]
                    digest_text += "💭 <b>Твои цели:</b>\n"
                    for dream in dreams:
                        digest_text += f"  • {dream}\n"
                    digest_text += "\nКак продвигаешься? Напиши мне!"
                else:
                    digest_text += "Расскажи о своих целях, и я помогу их достичь! 🚀"

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


@app.get("/debug/memory/{user_id}", dependencies=[Depends(require_cron_secret)])
async def debug_memory(request: Request, user_id: int, q: str = ""):
    """
    Отладочный эндпоинт для проверки памяти пользователя.
    GET /debug/memory/123 — статистика
    GET /debug/memory/123?q=тема — поиск по памяти

    EC-2: Protected by X-Cron-Secret. Restricted to admin user only.
    """
    # EC-2: restrict to admin user only
    if user_id != ADMIN_USER_ID:
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(status_code=403, detail="Forbidden")

    memory = request.app.state.memory
    if not memory:
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(status_code=503, content={"status": "error", "message": "Memory Service not initialized"})

    try:
        all_memories = await memory.get_all_memories(user_id, limit=100)
        stats = {"total": len(all_memories)}

        result = {"status": "ok", "user_id": user_id, "stats": stats}

        if q:
            search_results = await memory.search_memory(user_id, q, limit=5)
            result["search_query"] = q
            result["search_results"] = search_results

        result["recent"] = [
            {"id": m.get("id", "")}
            for m in all_memories[:5]
        ]

        return result
    except Exception as e:
        logger.error(f"debug_memory error for user {user_id}: {e}")
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(status_code=500, content={"status": "error", "message": "Internal error"})


@app.post("/cron/summarize-memory", dependencies=[Depends(require_cron_secret)])
async def summarize_memory_cron(request: Request):
    """
    Суммаризация старых сообщений в долговременной памяти.
    С Mem0 не требуется — дедупликация автоматическая.
    """
    memory = request.app.state.memory
    db = request.app.state.db
    ai_engine = request.app.state.ai_engine
    if not memory or not db or not ai_engine:
        return {"status": "error", "message": "Services not initialized"}

    try:
        user_ids = await db.get_all_user_ids()
        summarized = 0
        for user_id in user_ids:
            try:
                await memory.summarize_old_messages(user_id, ai_engine)
                summarized += 1
            except Exception as e:
                logger.warning(f"Ошибка суммаризации памяти для {user_id}: {e}")

        return {"status": "ok", "users_processed": summarized}
    except Exception as e:
        logger.error(f"Ошибка cron summarize-memory: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/cron/dream-state", dependencies=[Depends(require_cron_secret)])
async def dream_state_cron(request: Request):
    """
    AI Dream State — нightly memory consolidation.

    Returns HTTP 202 immediately, then runs all three phases
    (Weight Decay, Apoptosis, Valence Restoration) as a background task
    for each user.

    EC-2: Protected by X-Cron-Secret.
    Task 12: Cloud Run --timeout 3600.
    """
    memory = request.app.state.memory
    db = request.app.state.db

    if not memory or not db:
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(
            status_code=503,
            content={"status": "error", "message": "Services not initialized"},
        )

    async def _run_all_users():
        from services.dream_state import run_dream_state
        try:
            user_ids = await db.get_all_user_ids()
            logger.info(f"Dream State: starting for {len(user_ids)} users")
            for uid in user_ids:
                try:
                    # Get Qdrant client from memory service
                    client = await memory._ensure_qdrant()
                    provider = await memory._ensure_c60_provider()
                    result = await run_dream_state(uid, client, provider, db)
                    logger.info(f"Dream State: user {uid} complete: {result}")
                except Exception as e:
                    logger.error(f"Dream State: user {uid} error: {e}")
        except Exception as e:
            logger.error(f"Dream State: batch error: {e}")

    _fire_and_forget(_run_all_users())

    return {"status": "accepted", "message": "Dream State started in background"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
