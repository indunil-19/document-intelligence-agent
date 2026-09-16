"""Shared state passed between graph nodes."""
from typing import Any, TypedDict


class ChatState(TypedDict, total=False):
    # Input
    session_id: str
    question: str
    history: list[dict[str, str]]
    role: str  # viewer | analyst | admin - controls which tools retrieval may use

    # Set by the orchestrator agent
    intent: str
    intent_type: str
    route: str  # retrieval | direct | out_of_scope
    in_scope: bool
    document_types: list[str]
    metadata_filters: dict[str, str]
    clarification: str | None
    refusal_guidance: str | None

    # Set by the retrieval agent
    evidence: str
    sources: list[dict[str, Any]]
    tools_used: list[str]

    # Set by the response agent
    answer: str

    # Cross-cutting
    degraded: bool
    notes: list[str]
