"""
State TypedDict — общее состояние графа за один диалоговый ход.

Input-поля заполняются ДО запуска графа (в dialog_pipeline.py).
Узлы пишут только в свои output-поля (NotRequired).
"""

from __future__ import annotations

from typing import TypedDict

from typing_extensions import NotRequired


class GraphState(TypedDict):
    """Состояние, циркулирующее между узлами LangGraph."""

    # --- Input (заполняются pre-graph) ---
    user_id: int
    user_name: str
    user_message: str
    messages: list[dict]            # История — последние 20 сообщений из Firestore
    user_profile: dict              # Профиль из Firestore (profile_summary, bot_nickname, etc.)
    memory_context: str             # Контекст из Mem0
    model_context: str              # Список моделей (если model-related query), иначе ""
    selected_provider: str          # Провайдер LLM (gemini, anthropic, nvidia...)
    selected_model: str             # Конкретная модель

    # --- Output (устанавливаются узлами) ---
    intent: NotRequired[str]        # router_node: interview|coaching|execution|chit_chat
    response: NotRequired[str]      # generate_node: сгенерированный ответ LLM
    actual_provider: NotRequired[str]   # generate_node: фактический провайдер (после fallback)
    actual_model: NotRequired[str]      # generate_node: фактическая модель (после fallback)


# Допустимые интенты — closed set для валидации
VALID_INTENTS = frozenset({"interview", "coaching", "execution", "chit_chat"})

# Fallback-интент при ошибке router
DEFAULT_INTENT = "execution"
