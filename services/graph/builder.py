"""
Сборка и компиляция LangGraph графа.
router → generate → END.
Analyzer вынесен из графа — работает async после отправки ответа.
"""

from langgraph.graph import END, StateGraph

from services.graph.nodes import make_generate_node, make_router_node
from services.graph.state import GraphState


def build_graph(ai_engine, model_catalog=None):
    """Собирает и компилирует граф. Вызывать один раз при старте приложения."""
    router = make_router_node(ai_engine)
    generate = make_generate_node(ai_engine, model_catalog)

    sg = StateGraph(GraphState)
    sg.add_node("router", router)
    sg.add_node("generate", generate)

    sg.set_entry_point("router")
    sg.add_edge("router", "generate")
    sg.add_edge("generate", END)

    return sg.compile()
