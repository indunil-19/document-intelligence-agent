"""Retrieval agent: a tool-calling agent over the RAG tools plus the MCP tools."""
import logging
import re

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

from app.agents.loader import load_instructions
from app.config import get_settings
from app.graph.state import ChatState
from app.llm import get_llm
from app.mock.rag import get_rag_store
from app.tools.mcp_tools import get_mcp_tools, mcp_available
from app.tools.rag_tools import RAG_TOOLS

logger = logging.getLogger(__name__)

_DOC_ID_PATTERN = re.compile(r"\b(?:POL|ARC|RUN|INC|SPEC|MTG)-\d{3}\b")

_agent = None


def build_retrieval_agent():
    """Built once, after MCP tools are loaded at startup."""
    global _agent
    tools = [*RAG_TOOLS, *get_mcp_tools()]
    instructions = load_instructions("retrieval")
    if not mcp_available():
        instructions += (
            "\n\n## Note\nThe employee directory and service catalog are currently "
            "unavailable. Answer from documents alone and state that ownership and "
            "on-call details could not be confirmed."
        )
    _agent = create_react_agent(get_llm(), tools, prompt=instructions)
    logger.info(
        "retrieval agent built",
        extra={"event": "agent.built", "tools": [t.name for t in tools]},
    )
    return _agent


def get_retrieval_agent():
    return _agent if _agent is not None else build_retrieval_agent()


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
    agent = get_retrieval_agent()

    config = {
        "configurable": {"session_id": state["session_id"]},
        # Each agent step is a model call plus a tool round, so allow two per step.
        "recursion_limit": settings.max_retrieval_steps * 2,
    }

    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=_build_task(state))]}, config=config
        )
    except Exception as exc:  # noqa: BLE001 - degrade to an answer without evidence
        logger.exception("retrieval agent failed", extra={"event": "node.retrieval.error"})
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

    return {
        "evidence": evidence,
        "sources": sources,
        "tools_used": sorted(set(tools_used)),
        # Preserve an upstream degradation; do not overwrite it.
        "degraded": state.get("degraded", False) or not evidence,
    }
