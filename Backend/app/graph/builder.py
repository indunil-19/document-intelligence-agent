"""Graph wiring.

    orchestrate ─┬─ retrieval ─┐
                 └─────────────┴─ respond ─ END

The orchestrator routes out-of-scope and conversational turns straight to the
response agent; only document questions pay for retrieval.
"""
import logging

from langgraph.graph import END, StateGraph

from app.graph.nodes.orchestrator import orchestrator_node, route_from_orchestrator
from app.graph.nodes.response import response_node
from app.graph.nodes.retrieval import retrieval_node
from app.graph.state import ChatState

logger = logging.getLogger(__name__)

_graph = None


def build_graph():
    global _graph
    workflow = StateGraph(ChatState)

    workflow.add_node("orchestrate", orchestrator_node)
    workflow.add_node("retrieval", retrieval_node)
    workflow.add_node("respond", response_node)

    workflow.set_entry_point("orchestrate")
    workflow.add_conditional_edges(
        "orchestrate",
        route_from_orchestrator,
        {"retrieval": "retrieval", "respond": "respond"},
    )
    workflow.add_edge("retrieval", "respond")
    workflow.add_edge("respond", END)

    _graph = workflow.compile()
    logger.info("graph compiled", extra={"event": "graph.compiled"})
    return _graph


def get_graph():
    return _graph if _graph is not None else build_graph()
