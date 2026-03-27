"""
Node functions для LangGraph графа.
Каждый узел — фабрика: make_xxx_node(deps) → async function(state) → dict.
Зависимости передаются через closure (соответствует AP-5).
"""

import asyncio
import json
import logging
import time

from services.async_utils import fire_and_forget
from services.graph.prompts import build_mode_prompt
from services.graph.router import build_router_input, parse_router_response
from services.graph.state import DEFAULT_INTENT, GraphState
from services.model_rotation import generate_with_fallback

logger = logging.getLogger(__name__)

# Timeout budget (секунды): router 3s, generate 30s (внутри generate_with_fallback), analyzer 3s
# Total worst-case: ~36s
ROUTER_TIMEOUT = 3.0
ANALYZER_TIMEOUT = 3.0


# --- Analyzer prompt (~100 токенов инструкций) ---

ANALYZER_PROMPT = """Определи, содержит ли сообщение пользователя новую персональную информацию для профиля.

Персональная информация: навыки, интересы, мечты, боли/проблемы, биографические факты, цели, хобби, опыт работы.

НЕ считай персональной: вопросы, задачи, команды, приветствия, благодарности, обычный разговор.

<user_message>
{user_message}
</user_message>

Ответь ТОЛЬКО JSON: {{"has_new_info": true}}  или  {{"has_new_info": false}}"""


def parse_analyzer_response(raw: str) -> bool:
    """Парсит ответ analyzer. Возвращает True если обнаружена новая инфо, False при любой ошибке."""
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(cleaned)
        return bool(data.get("has_new_info", False))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False


# --- Node factories ---


def make_router_node(ai_engine):
    """Фабрика: router_node — классификация интента через LLM (temperature=0)."""

    async def router_node(state: GraphState) -> dict:
        start = time.time()
        try:
            router_input = build_router_input(
                state["user_message"], state["user_profile"], state["messages"]
            )

            provider = ai_engine.get_provider(
                ai_engine.default_provider_name, ai_engine.default_model
            )

            raw = await asyncio.wait_for(
                provider.generate(
                    messages=[{"role": "user", "content": router_input}],
                    system_prompt="",
                    temperature=0,
                ),
                timeout=ROUTER_TIMEOUT,
            )

            intent = parse_router_response(raw)

        except (asyncio.TimeoutError, asyncio.CancelledError):
            # Operational: LLM не ответил вовремя → fallback intent
            logger.warning("Router: timeout after %.0fs, defaulting to %s", ROUTER_TIMEOUT, DEFAULT_INTENT)
            intent = DEFAULT_INTENT
        except (KeyError, TypeError, AttributeError):
            # Programmer error → пробрасываем наверх в process_turn()
            raise
        except Exception as exc:
            # Operational: HTTP ошибки, пустой ответ и т.д.
            logger.warning("Router: %s, defaulting to %s", type(exc).__name__, DEFAULT_INTENT)
            intent = DEFAULT_INTENT

        latency_ms = int((time.time() - start) * 1000)
        logger.info("Router: intent=%s latency=%dms", intent, latency_ms)
        return {"intent": intent}

    return router_node


def make_generate_node(ai_engine, model_catalog):
    """Фабрика: generate_node — единый узел генерации (shared base + mode overlay)."""

    async def generate_node(state: GraphState) -> dict:
        intent = state.get("intent", DEFAULT_INTENT)

        model_context = state.get("model_context") or None
        current_model = f"{state['selected_provider']}/{state['selected_model']}"

        system_prompt = build_mode_prompt(
            intent=intent,
            user_profile=state["user_profile"],
            user_name=state["user_name"],
            memory_context=state["memory_context"],
            model_context=model_context,
            current_model=current_model,
        )

        # Копия messages + текущее сообщение (Task 9: копия предотвращает мутацию)
        messages = list(state["messages"]) + [
            {"role": "user", "content": state["user_message"]}
        ]

        response_text, actual_provider, actual_model = await generate_with_fallback(
            ai_engine=ai_engine,
            model_catalog=model_catalog,
            provider_name=state["selected_provider"],
            model=state["selected_model"],
            messages=messages,
            system_prompt=system_prompt,
            timeout=30.0,
        )

        return {
            "response": response_text,
            "actual_provider": actual_provider,
            "actual_model": actual_model,
        }

    return generate_node


def make_analyzer_node(ai_engine, analyzer_service):
    """Фабрика: analyzer_node — LLM решает, нужен ли анализ профиля."""

    async def analyzer_node(state: GraphState) -> dict:
        start = time.time()
        has_new_info = False
        try:
            prompt = ANALYZER_PROMPT.format(user_message=state["user_message"])

            provider = ai_engine.get_provider(
                ai_engine.default_provider_name, ai_engine.default_model
            )

            raw = await asyncio.wait_for(
                provider.generate(
                    messages=[{"role": "user", "content": prompt}],
                    system_prompt="",
                    temperature=0,
                ),
                timeout=ANALYZER_TIMEOUT,
            )

            has_new_info = parse_analyzer_response(raw)

        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("Analyzer: timeout after %.0fs, skipping", ANALYZER_TIMEOUT)
        except (KeyError, TypeError, AttributeError):
            raise
        except Exception as exc:
            logger.warning("Analyzer: %s, skipping", type(exc).__name__)

        latency_ms = int((time.time() - start) * 1000)
        logger.info("Analyzer: has_new_info=%s latency=%dms", has_new_info, latency_ms)

        # fire_and_forget — никогда не блокируем ответ на ожидании анализа
        if has_new_info and analyzer_service:
            fire_and_forget(
                analyzer_service.analyze_user_profile(state["user_id"]),
                name=f"analyze-{state['user_id']}",
            )

        return {}  # Analyzer не пишет в state — side effect через fire_and_forget

    return analyzer_node
