"""
Авто-ротация моделей с таймаутом и fallback chain.
Оборачивает вызов провайдера: если модель не отвечает за 10с,
пробует следующую в цепочке. Никогда не меняет selected_model пользователя.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Retriable HTTP status codes и exceptions
_RETRIABLE_STATUS_CODES = {429, 500, 502, 503}


def is_retriable(exc: Exception) -> bool:
    """Определяет, стоит ли повторять запрос после данной ошибки."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    # Проверяем HTTP status code в известных SDK-ошибках
    status = getattr(exc, 'status_code', None) or getattr(exc, 'status', None)
    if status and int(status) in _RETRIABLE_STATUS_CODES:
        return True
    # google.api_core.exceptions имеют code
    code = getattr(exc, 'code', None)
    if code and int(code) in _RETRIABLE_STATUS_CODES:
        return True
    return False


async def generate_with_fallback(
    ai_engine,
    model_catalog,
    provider_name: str,
    model: str,
    messages: list,
    system_prompt: str,
    timeout: float = 10.0,
) -> tuple[str, str, str]:
    """
    Генерирует ответ с авто-ротацией при сбое.

    Returns:
        (response_text, actual_provider, actual_model)
    """
    from services.model_catalog import MODEL_FAMILY_META, get_model_meta

    # Строим fallback chain: (1) retry same, (2) same family, (3) default Gemini
    fallback_chain = [(provider_name, model)]

    # Получаем family_fallback_order для текущей модели
    meta = get_model_meta(model)
    family_fallbacks = meta.get("family_fallback_order", [])
    for fb_model in family_fallbacks:
        if fb_model != model:
            fallback_chain.append((provider_name, fb_model))

    # Финальный fallback — дефолтный Gemini
    if ai_engine.default_provider_name and ai_engine.default_model:
        default_pair = (ai_engine.default_provider_name, ai_engine.default_model)
        if default_pair not in fallback_chain:
            fallback_chain.append(default_pair)

    # Максимум 3 попытки
    fallback_chain = fallback_chain[:3]

    last_error = None
    for attempt, (prov, mdl) in enumerate(fallback_chain, 1):
        try:
            provider = ai_engine.get_provider(prov, mdl)
            start_time = time.time()
            response = await asyncio.wait_for(
                provider.generate(messages, system_prompt),
                timeout=timeout,
            )
            latency_ms = int((time.time() - start_time) * 1000)

            # Валидация ответа (Willison: проверяем, что ответ не пустой)
            if not response or not response.strip():
                logger.warning(
                    f"Rotation: пустой ответ от {prov}/{mdl} "
                    f"(attempt={attempt}, latency={latency_ms}ms)"
                )
                last_error = ValueError("Пустой ответ от модели")
                continue

            if attempt > 1:
                logger.info(
                    f"Rotation: успех на attempt={attempt}, "
                    f"model={prov}/{mdl}, latency={latency_ms}ms"
                )

            return response, prov, mdl

        except Exception as exc:
            latency_ms = int((time.time() - start_time) * 1000) if 'start_time' in dir() else 0
            # EC-8: не логируем полные сообщения ошибок (могут содержать API ключи)
            logger.warning(
                f"Rotation: {type(exc).__name__} от {prov}/{mdl} "
                f"(attempt={attempt}, latency={latency_ms}ms, retriable={is_retriable(exc)})"
            )
            last_error = exc

            if not is_retriable(exc):
                # Не retriable (400, 401, 403) — не пробуем дальше в том же провайдере
                # но пробуем fallback на другой провайдер
                continue
            continue

    # Все попытки провалились
    logger.error(
        f"Rotation: все {len(fallback_chain)} попытки провалились. "
        f"Последняя ошибка: {type(last_error).__name__ if last_error else 'unknown'}"
    )
    return (
        "Все модели временно недоступны. Попробуй повторить через минуту.",
        provider_name,
        model,
    )


def format_rotation_footnote(
    requested_provider: str,
    requested_model: str,
    actual_provider: str,
    actual_model: str,
) -> str:
    """
    Генерирует footnote если провайдер изменился.
    Возвращает пустую строку если ротации не было или модель из того же семейства.
    """
    if actual_provider == requested_provider and actual_model == requested_model:
        return ""

    from services.model_catalog import get_model_meta

    requested_meta = get_model_meta(requested_model)
    actual_meta = get_model_meta(actual_model)

    # Если семейство одинаковое — не показываем (Telegram UX: пользователю не важны minor version)
    if requested_meta.get("label") == actual_meta.get("label"):
        return ""

    # Формируем footnote (Friedman: brief, honest, non-alarming)
    return (
        f"\n\n<i>⚡ Ответила модель {actual_meta.get('label', actual_model)} "
        f"({requested_meta.get('label', requested_model)} временно недоступна)</i>"
    )
