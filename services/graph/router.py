"""
Router — классификация интента пользователя через LLM.
Минимальный контекст, structured output, temperature=0.
"""

import json
import logging

from services.graph.state import DEFAULT_INTENT, VALID_INTENTS

logger = logging.getLogger(__name__)

# Промпт для классификации интента (~200 токенов инструкций)
ROUTER_PROMPT = """Ты — классификатор интентов для AI-ассистента. Определи намерение пользователя.

Категории:
- interview: пользователь делится личной информацией (навыки, интересы, мечты, опыт, биография) ИЛИ профиль пуст и нужно собрать данные
- coaching: пользователь хочет научиться, разобраться, понять ("как сделать?", "объясни", "научи", "помоги разобраться")
- execution: пользователь даёт прямую задачу ("напиши", "сделай", "создай", "переведи", "исправь")
- chit_chat: обычный разговор, приветствия, благодарности, короткие реакции

Правила:
- Если профиль пуст (0 полей заполнено) → почти всегда interview
- Если пользователь делится фактами о себе → interview (даже если профиль заполнен)
- Если есть и "научи" и "сделай" → coaching (обучение приоритетнее)
- Если неоднозначно → execution (наиболее общий)

<profile_status>
{profile_status}
</profile_status>

<recent_messages>
{recent_messages}
</recent_messages>

<user_message>
{user_message}
</user_message>

Ответь ТОЛЬКО JSON: {{"intent": "interview|coaching|execution|chit_chat"}}"""


def build_router_input(
    user_message: str,
    user_profile: dict,
    messages: list[dict],
) -> str:
    """Строит input для router. Минимальный контекст — только то, что нужно для классификации."""
    # Профиль: только индикатор заполненности (Hunt: strip PII)
    profile_summary = user_profile.get("profile_summary", {}) if user_profile else {}
    filled_fields = sum(
        1 for k in ("summary", "interests", "new_skills", "dreams", "pain_points")
        if profile_summary.get(k)
    )
    profile_status = f"Заполнено полей профиля: {filled_fields}/5"
    if filled_fields == 0:
        profile_status += " (профиль пуст — приоритет interview)"

    # Последние 3 сообщения как bare text (Willison: curate aggressively)
    recent = messages[-3:] if messages else []
    recent_lines = []
    for msg in recent:
        role = "User" if msg.get("role") == "user" else "Bot"
        content = msg.get("content", "")[:100]  # Обрезаем до 100 символов
        recent_lines.append(f"{role}: {content}")
    recent_messages = "\n".join(recent_lines) if recent_lines else "(нет истории)"

    return ROUTER_PROMPT.format(
        profile_status=profile_status,
        recent_messages=recent_messages,
        user_message=user_message,
    )


def parse_router_response(raw: str) -> str:
    """Парсит и валидирует ответ router. Возвращает validated intent или DEFAULT_INTENT."""
    try:
        # Очистка: иногда LLM оборачивает в ```json
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(cleaned)
        intent = data.get("intent", "").strip().lower()

        if intent in VALID_INTENTS:
            return intent

        logger.warning("Router returned invalid intent: %s", intent)
        return DEFAULT_INTENT

    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        logger.warning("Router JSON parse failed: %s", type(e).__name__)
        return DEFAULT_INTENT
