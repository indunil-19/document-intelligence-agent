"""Retrieval agent: a tool-calling agent over the RAG tools plus the MCP tools.

Bound tools vary by caller role - a separate ReAct agent is built per role at
startup, each holding only the tools app.auth.ROLE_TOOLS grants that role. This is
the actual access-control boundary: a viewer's agent object has no
employee_directory/service_catalog tool bound at all, so the model has nothing to
call even if it wanted to.
"""
import logging
import re

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

from app.activity import ActivityCallbackHandler, emit
from app.agents.loader import load_instructions
from app.auth import ROLE_TOOLS, ROLES, select_tools_for_role
from app.config import get_settings
from app.graph.state import ChatState
from app.llm import get_llm
from app.mock.rag import get_rag_store
from app.tools.mcp_tools import get_mcp_tools, mcp_available
from app.tools.rag_tools import RAG_TOOLS

logger = logging.getLogger(__name__)

_DOC_ID_PATTERN = re.compile(r"\b(?:POL|ARC|RUN|INC|SPEC|MTG)-\d{3}\b")

# create_react_agent emits this as a normal AI message when it runs out of steps.
# Treated as evidence it reads like "I found nothing", which is not the same thing.
STEP_LIMIT_SENTINEL = "Sorry, need more steps to process this request."

_agents: dict[str, object] = {}


def _role_instructions(role: str, bound_tool_names: list[str]) -> str:
    instructions = load_instructions("retrieval")
    if not mcp_available():
        instructions += (
            "\n\n## Note\nThe employee directory and service catalog are currently "
            "unavailable. Answer from documents alone and state that ownership and "
            "on-call details could not be confirmed."
        )
    instructions += (
        f"\n\n## Tool access for this session (role: {role})\n"
        f"You only have these tools available: {', '.join(bound_tool_names) or 'none'}. "
        "Do not assume any other tool described above exists in this session - if a "
        "question needs one you don't have, say so plainly rather than guessing at "
        "an answer that tool would have provided."
    )
    return instructions


def build_retrieval_agents() -> dict[str, object]:
    """Built once at startup, after MCP tools are loaded - one ReAct agent per role."""
    global _agents
    all_tools = [*RAG_TOOLS, *get_mcp_tools()]
    _agents = {}
    for role in ROLES:
        tools = select_tools_for_role(role, all_tools)
        _agents[role] = create_react_agent(
            get_llm(), tools, prompt=_role_instructions(role, [t.name for t in tools])
        )
        logger.info(
            "retrieval agent built",
            extra={"event": "agent.built", "role": role, "tools": [t.name for t in tools]},
        )
    return _agents


def get_retrieval_agent(role: str):
    if not _agents:
        build_retrieval_agents()
    return _agents.get(role) or _agents[list(ROLE_TOOLS)[0]]


def _build_task(state: ChatState) -> str:
    parts = [f"User question: {state['question']}", f"Interpreted intent: {state.get('intent', '')}"]
    if state.get("document_types"):
        parts.append(f"Likely document types: {', '.join(state['document_types'])}")
    if state.get("metadata_filters"):
        parts.append(f"Implied metadata filters: {state['metadata_filters']}")
    if state.get("history"):
        recent = "\n".join(f"{t['role']}: {t['content']}" for t in state["history"][-4:])
        parts.append(f"Recent conversation:\n{recent}")
    return "\n\n".join(parts)


async def _collect_sources(messages) -> list[dict]:
    """Resolve document ids mentioned by any tool result back to document records."""
    found: list[str] = []
    for message in messages:
        if isinstance(message, ToolMessage):
            for doc_id in _DOC_ID_PATTERN.findall(str(message.content)):
                if doc_id not in found:
                    found.append(doc_id)
    if not found:
        return []
    documents = await get_rag_store().get_many(found)
    return [
        {
            "document_id": d["document_id"],
            "title": d["title"],
            "document_type": d["document_type"],
        }
        for d in documents
    ]


async def retrieval_node(state: ChatState) -> ChatState:
    settings = get_settings()
    role = state.get("role", "viewer")
    emit("node_start", node="retrieval", message=f"Gathering evidence (role: {role})")
    agent = get_retrieval_agent(role)

    config = {
        "configurable": {"session_id": state["session_id"]},
        "callbacks": [ActivityCallbackHandler()],
        # One tool round costs two graph steps (model node + tool node), plus a
        # final model node to write the brief.
        "recursion_limit": settings.max_retrieval_steps * 2 + 1,
    }

    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=_build_task(state))]}, config=config
        )
    except Exception as exc:  # noqa: BLE001 - degrade to an answer without evidence
        logger.exception("retrieval agent failed", extra={"event": "node.retrieval.error"})
        emit("validation", node="retrieval", message=f"Retrieval failed: {exc}")
        emit("node_end", node="retrieval", message="Retrieval failed")
        return {
            "evidence": "",
            "sources": [],
            "tools_used": [],
            "degraded": True,
            "notes": state.get("notes", []) + [f"retrieval failed: {exc}"],
        }

    messages = result.get("messages", [])
    evidence = ""
    for message in reversed(messages):
        if message.type == "ai" and message.text.strip():
            evidence = message.text.strip()
            break

    truncated = evidence.startswith(STEP_LIMIT_SENTINEL)
    notes = list(state.get("notes", []))
    if truncated:
        # Do not pass the sentinel on as findings - it would be reported to the user
        # as "nothing was found" when the search was simply cut short.
        evidence = ""
        notes.append("retrieval stopped early: step budget exhausted")
        logger.warning(
            "retrieval hit the step limit",
            extra={
                "event": "node.retrieval.truncated",
                "recursion_limit": config["recursion_limit"],
            },
        )
        emit("validation", node="retrieval", message="Step budget exhausted before finishing")

    tools_used = [m.name for m in messages if isinstance(m, ToolMessage) and m.name]
    sources = await _collect_sources(messages)

    logger.info(
        "retrieval complete",
        extra={
            "event": "node.retrieval",
            "tool_calls": len(tools_used),
            "tools": sorted(set(tools_used)),
            "source_count": len(sources),
            "evidence_chars": len(evidence),
        },
    )

    emit(
        "validation",
        node="retrieval",
        message="Evidence found" if evidence else "No evidence found",
        evidence_found=bool(evidence),
        source_count=len(sources),
    )
    emit(
        "node_end",
        node="retrieval",
        message=f"Used {len(set(tools_used))} tool(s), found {len(sources)} source(s)",
        tools_used=sorted(set(tools_used)),
        source_count=len(sources),
    )

    return {
        "evidence": evidence,
        "sources": sources,
        "tools_used": sorted(set(tools_used)),
        # Preserve an upstream degradation; do not overwrite it.
        "degraded": state.get("degraded", False) or not evidence,
        "notes": notes,
    }
