"""Tools for the retrieval agent.

Every tool is async and returns a plain dict. Failures are returned as
`{"error": ...}` rather than raised: the agent can read that, explain the gap and
carry on, whereas an exception would abort the turn.
"""
import asyncio
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.activity import emit
from app.config import get_settings
from app.llm import get_subagent_llm
from app.rag import DOCUMENT_TYPES, get_rag_store
from app.session import get_session_store

logger = logging.getLogger(__name__)


async def _session_from_config(config: RunnableConfig | None):
    session_id = (config or {}).get("configurable", {}).get("session_id")
    if not session_id:
        return None
    return await get_session_store().get(session_id)


def _filter_signature(filters: dict[str, str]) -> str:
    return json.dumps(filters, sort_keys=True)


@tool
async def document_search(
    query: str, document_type: str | None = None, limit: int = 5
) -> dict:
    """Search the internal document store and return matching documents with content.

    Args:
        query: What to look for, in plain words.
        document_type: Optional narrowing to one of policy, architecture, runbook,
            incident_report, product_spec, meeting_notes.
        limit: Maximum documents to return (1-10).
    """
    try:
        if document_type and document_type not in DOCUMENT_TYPES:
            return {
                "error": f"unknown document_type '{document_type}'",
                "valid_document_types": DOCUMENT_TYPES,
            }
        limit = max(1, min(int(limit), 10))
        store = get_rag_store()
        documents = await store.search(query, document_type=document_type, limit=limit)
        logger.info(
            "document search",
            extra={
                "event": "tool.document_search",
                "query": query,
                "document_type": document_type,
                "result_count": len(documents),
                "dense_available": store.dense_available,
            },
        )
        emit(
            "retrieval_status",
            tool="document_search",
            message=(
                f"Hybrid search ({'dense+sparse' if store.dense_available else 'sparse-only, dense unavailable'}) "
                f"returned {len(documents)} result(s)"
            ),
            dense_available=store.dense_available,
            top_scores=[
                {"document_id": d["document_id"], "score": d["score"],
                 "dense": d.get("dense_score"), "sparse": d.get("sparse_score")}
                for d in documents[:3]
            ],
        )
        return {"count": len(documents), "documents": documents}
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not swallowed
        logger.exception("document_search failed", extra={"event": "tool.error"})
        return {"error": f"document search failed: {exc}"}


@tool
async def metadata_retrieval(document_type: str) -> dict:
    """Return the metadata fields a document type carries and the values in use.

    Call this before filter_by_metadata when you are unsure which fields exist or
    what values are valid for a document type.

    Args:
        document_type: One of policy, architecture, runbook, incident_report,
            product_spec, meeting_notes.
    """
    try:
        if document_type not in DOCUMENT_TYPES:
            return {
                "error": f"unknown document_type '{document_type}'",
                "valid_document_types": DOCUMENT_TYPES,
            }
        schema = await get_rag_store().metadata_for_type(document_type)
        logger.info(
            "metadata retrieval",
            extra={"event": "tool.metadata_retrieval", "document_type": document_type},
        )
        return schema
    except Exception as exc:  # noqa: BLE001
        logger.exception("metadata_retrieval failed", extra={"event": "tool.error"})
        return {"error": f"metadata retrieval failed: {exc}"}


@tool
async def filter_by_metadata(
    filters: dict[str, str], config: RunnableConfig = None
) -> dict:
    """Count documents matching exact metadata filters and cache them in the session.

    Returns the count and a lightweight index (ids and titles), not full contents.
    The matched documents are cached for analyze_documents to work on.

    Args:
        filters: Exact-match filters, e.g. {"document_type": "policy",
            "status": "active"} or {"owner": "Security Team"}. Keys may be
            document_type or any metadata field. Values are case-insensitive.
    """
    try:
        if not filters:
            return {"error": "filters must not be empty"}

        documents = await get_rag_store().filter_by_metadata(filters)
        signature = _filter_signature(filters)

        session = await _session_from_config(config)
        if session is not None:
            session.cache_documents(signature, documents)
            cached = True
            emit(
                "memory_update",
                tool="filter_by_metadata",
                message=f"Cached {len(documents)} document(s) in session for analysis",
                filters=filters,
                count=len(documents),
            )
        else:
            cached = False
            logger.warning("no session to cache into", extra={"event": "tool.no_session"})

        logger.info(
            "metadata filter",
            extra={
                "event": "tool.filter_by_metadata",
                "filters": filters,
                "result_count": len(documents),
                "cached": cached,
            },
        )
        return {
            "count": len(documents),
            "filters": filters,
            "cached_for_analysis": cached,
            "documents": [
                {
                    "document_id": d["document_id"],
                    "title": d["title"],
                    "document_type": d["document_type"],
                }
                for d in documents
            ],
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("filter_by_metadata failed", extra={"event": "tool.error"})
        return {"error": f"metadata filter failed: {exc}"}


def _format_documents(documents: list[dict]) -> str:
    return "\n\n".join(
        f"[{d['document_id']}] {d['title']} ({d['document_type']})\n"
        f"metadata: {json.dumps(d.get('metadata', {}), sort_keys=True)}\n"
        f"{d.get('content', '')}"
        for d in documents
    )


async def _analyze_batch(instruction: str, documents: list[dict], batch_no: int) -> str:
    """One analytics sub-agent: analyses a slice of the document set."""
    tool_name = f"analyze_documents:batch_{batch_no}"
    emit(
        "tool_start",
        tool=tool_name,
        message=f"Sub-agent analysing batch {batch_no} ({len(documents)} documents)",
    )
    llm = get_subagent_llm()
    system = (
        "You are an analysis sub-agent. You have been given a subset of a larger "
        "document set. Analyse only what is in front of you and answer the "
        "instruction for this subset. Cite document ids in brackets. Report "
        "concrete findings and figures; do not speculate about documents you "
        "cannot see, and do not write a conclusion about the whole set."
    )
    prompt = (
        f"Instruction: {instruction}\n\n"
        f"Documents in this batch ({len(documents)}):\n\n{_format_documents(documents)}"
    )
    try:
        response = await llm.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=prompt)]
        )
    except Exception as exc:  # noqa: BLE001 - let the caller's gather() collect this
        emit("tool_error", tool=tool_name, message=f"Batch {batch_no} failed: {exc}")
        raise
    logger.info(
        "analytics sub-agent finished",
        extra={
            "event": "tool.analytics.subagent",
            "batch": batch_no,
            "document_count": len(documents),
        },
    )
    emit("tool_end", tool=tool_name, message=f"Batch {batch_no} analysed")
    return f"### Batch {batch_no} ({len(documents)} documents)\n{response.text}"


@tool
async def analyze_documents(instruction: str, config: RunnableConfig = None) -> dict:
    """Analyse the documents most recently cached by filter_by_metadata.

    When the cached set is large this fans out to sub-agents that analyse batches in
    parallel, then merges their findings. Call filter_by_metadata first.

    Args:
        instruction: What to analyse, derived from the user's question, e.g.
            "list the recurring root causes and the action items for each".
    """
    settings = get_settings()
    try:
        session = await _session_from_config(config)
        if session is None:
            return {"error": "no active session; cannot reach cached documents"}

        documents = session.cached_documents()
        if not documents:
            return {
                "error": "nothing cached to analyse",
                "hint": "call filter_by_metadata first to select a document set",
            }

        total = len(documents)
        if total <= settings.analytics_fanout_threshold:
            logger.info(
                "analytics inline",
                extra={"event": "tool.analytics", "mode": "inline", "document_count": total},
            )
            emit(
                "retrieval_status",
                tool="analyze_documents",
                message=f"Analysing {total} document(s) inline",
            )
            analysis = await _analyze_batch(instruction, documents, batch_no=1)
            return {
                "mode": "inline",
                "document_count": total,
                "analysis": analysis,
            }

        # Large set: split into batches and run sub-agents concurrently.
        size = settings.analytics_chunk_size
        batches = [documents[i : i + size] for i in range(0, total, size)]
        batches = batches[: settings.analytics_max_subagents]
        analysed = sum(len(b) for b in batches)

        logger.info(
            "analytics fan-out",
            extra={
                "event": "tool.analytics",
                "mode": "fanout",
                "document_count": total,
                "analysed_count": analysed,
                "subagents": len(batches),
            },
        )
        emit(
            "retrieval_status",
            tool="analyze_documents",
            message=(
                f"{total} documents exceed the inline threshold - fanning out to "
                f"{len(batches)} sub-agents"
            ),
            subagents=len(batches),
            document_count=total,
        )

        results = await asyncio.gather(
            *(
                _analyze_batch(instruction, batch, batch_no=i + 1)
                for i, batch in enumerate(batches)
            ),
            return_exceptions=True,
        )

        partials, failures = [], 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failures += 1
                logger.error(
                    "analytics sub-agent failed",
                    extra={"event": "tool.analytics.subagent_failed", "batch": i + 1},
                    exc_info=result,
                )
            else:
                partials.append(result)

        if not partials:
            return {"error": "every analysis sub-agent failed", "document_count": total}

        merged = await _merge_analyses(instruction, partials, total)
        return {
            "mode": "fanout",
            "document_count": total,
            "analysed_count": analysed,
            "subagents": len(batches),
            "failed_subagents": failures,
            "truncated": analysed < total,
            "analysis": merged,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("analyze_documents failed", extra={"event": "tool.error"})
        return {"error": f"analysis failed: {exc}"}


async def _merge_analyses(instruction: str, partials: list[str], total: int) -> str:
    """Reduce step: fold the sub-agent findings into one answer."""
    llm = get_subagent_llm()
    system = (
        "You merge partial analyses of a document set into one consolidated result. "
        "Combine overlapping findings, keep every document id citation, preserve "
        "concrete figures, and note patterns that appear across batches. Do not "
        "invent anything that is not in the partials."
    )
    prompt = (
        f"Instruction the batches were given: {instruction}\n"
        f"Total documents in the set: {total}\n\n"
        + "\n\n".join(partials)
    )
    response = await llm.ainvoke(
        [SystemMessage(content=system), HumanMessage(content=prompt)]
    )
    return response.text


RAG_TOOLS = [document_search, metadata_retrieval, filter_by_metadata, analyze_documents]
