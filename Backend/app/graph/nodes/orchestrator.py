"""Orchestration agent: intent understanding, scope check and routing."""
import logging
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.loader import load_instructions
from app.graph.state import ChatState
from app.llm import get_llm

logger = logging.getLogger(__name__)


class OrchestratorDecision(BaseModel):
    """Structured routing decision produced by the orchestration agent."""

    intent: str = Field(description="One sentence describing what the user wants.")
    intent_type: Literal[
        "lookup", "summarize", "compare", "analyze", "directory", "chitchat", "out_of_scope"
    ]
    route: Literal["retrieval", "direct", "out_of_scope"]
    in_scope: bool
    document_types: list[str] = Field(
        default_factory=list, description="Document types likely to hold the answer."
    )
    metadata_filters: dict[str, str] = Field(
        default_factory=dict, description="Metadata filters implied by the question."
    )
    clarification: str | None = Field(
        default=None, description="Clarifying question to ask, if the request is vague."
    )
    refusal_guidance: str | None = Field(
        default=None, description="How to decline gracefully, when out of scope."
    )


def _format_history(history: list[dict[str, str]]) -> str:
    if not history:
        return "(no earlier turns)"
    return "\n".join(f"{turn['role']}: {turn['content']}" for turn in history[-6:])


async def orchestrator_node(state: ChatState) -> ChatState:
    question = state["question"]
    llm = get_llm().with_structured_output(OrchestratorDecision)

    prompt = (
        f"Recent conversation:\n{_format_history(state.get('history', []))}\n\n"
        f"Current user message:\n{question}"
    )

    try:
        decision: OrchestratorDecision = await llm.ainvoke(
            [
                SystemMessage(content=load_instructions("orchestrator")),
                HumanMessage(content=prompt),
            ]
        )
    except Exception:  # noqa: BLE001 - routing must not take the request down
        logger.exception(
            "orchestrator failed; defaulting to retrieval",
            extra={"event": "node.orchestrator.error"},
        )
        return {
            "intent": "Unclassified request; routed to retrieval as a fallback.",
            "intent_type": "lookup",
            "route": "retrieval",
            "in_scope": True,
            "document_types": [],
            "metadata_filters": {},
            "clarification": None,
            "refusal_guidance": None,
            "degraded": True,
            "notes": state.get("notes", []) + ["orchestrator unavailable"],
        }

    logger.info(
        "routing decision",
        extra={
            "event": "node.orchestrator",
            "route": decision.route,
            "intent_type": decision.intent_type,
            "in_scope": decision.in_scope,
        },
    )

    return {
        "intent": decision.intent,
        "intent_type": decision.intent_type,
        "route": decision.route,
        "in_scope": decision.in_scope,
        "document_types": decision.document_types,
        "metadata_filters": decision.metadata_filters,
        "clarification": decision.clarification,
        "refusal_guidance": decision.refusal_guidance,
    }


def route_from_orchestrator(state: ChatState) -> str:
    """Conditional edge: only in-scope document questions reach the retrieval agent."""
    return "retrieval" if state.get("route") == "retrieval" else "respond"
