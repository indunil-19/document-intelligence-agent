"""Glue between the HTTP layer and the agent graph.

stream_chat() is the single source of truth for running a turn: it invokes the
graph, appends to session history, and emits activity events throughout (session
resume, node transitions, tool calls, the final payload) as NDJSON lines - this is
what powers the Streamlit Agent Activity Panel. handle_chat() is a thin wrapper that
drains that same stream and returns just the ChatResponse, for callers (the plain
/chat endpoint, and any test) that don't want the play-by-play.
"""
import asyncio
import json
import logging
import time
from typing import AsyncIterator

from app.activity import emit, reset_sink, set_sink
from app.errors import AppError
from app.graph.builder import get_graph
from app.logging_config import request_id_var, session_id_var
from app.schemas import ChatRequest, ChatResponse, SourceDocument
from app.session import get_session_store

logger = logging.getLogger(__name__)


def _build_initial_state(request: ChatRequest, session, role: str) -> dict:
    return {
        "session_id": session.session_id,
        "question": request.message,
        "history": list(session.history),
        "role": role,
        "degraded": False,
        "notes": [],
    }


async def stream_chat(
    request: ChatRequest, request_id: str, role: str = "viewer"
) -> AsyncIterator[bytes]:
    """Run one turn, yielding NDJSON activity events as they happen.

    The graph runs as a background task while this generator drains an
    asyncio.Queue the task feeds via the activity sink - that's what lets events be
    yielded to the HTTP response as the graph produces them, rather than only after
    the whole turn finishes. Never raises: every failure becomes a final `type:
    "error"` line so the NDJSON framing is never broken mid-stream.
    """
    store = get_session_store()
    session = await store.get_or_create(request.session_id)
    session_id_var.set(session.session_id)
    is_new_session = len(session.history) == 0

    queue: asyncio.Queue = asyncio.Queue()

    async def run() -> None:
        token = set_sink(queue.put_nowait)
        started = time.perf_counter()
        try:
            emit(
                "memory_update",
                message=(
                    f"{'Started new' if is_new_session else 'Resumed'} session "
                    f"({len(session.history)} prior turn(s) in memory)"
                ),
                session_id=session.session_id,
                new_session=is_new_session,
            )
            logger.info(
                "chat request received",
                extra={"event": "chat.started", "message_chars": len(request.message)},
            )

            final_state = await get_graph().ainvoke(
                _build_initial_state(request, session, role)
            )

            answer = final_state.get("answer", "")
            session.add_turn("user", request.message)
            session.add_turn("assistant", answer)
            emit(
                "memory_update",
                message="Conversation history updated",
                turns=len(session.history),
            )

            elapsed_ms = round((time.perf_counter() - started) * 1000)
            logger.info(
                "chat request completed",
                extra={
                    "event": "chat.completed",
                    "route": final_state.get("route"),
                    "intent_type": final_state.get("intent_type"),
                    "tools_used": final_state.get("tools_used", []),
                    "source_count": len(final_state.get("sources", [])),
                    "degraded": final_state.get("degraded", False),
                    "duration_ms": elapsed_ms,
                },
            )

            payload = ChatResponse(
                session_id=session.session_id,
                request_id=request_id_var.get(request_id),
                answer=answer,
                intent=final_state.get("intent", ""),
                route=final_state.get("route", "direct"),
                in_scope=final_state.get("in_scope", True),
                sources=[SourceDocument(**s) for s in final_state.get("sources", [])],
                tools_used=final_state.get("tools_used", []),
                degraded=final_state.get("degraded", False),
            )
            emit("final", message="Response generated", **payload.model_dump())
        except AppError as exc:
            logger.warning(
                "chat turn failed",
                extra={"event": "chat.failed", "code": exc.code},
            )
            emit(
                "error",
                message=exc.message,
                code=exc.code,
                status_code=exc.status_code,
                details=exc.details,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("graph execution failed", extra={"event": "chat.failed"})
            emit(
                "error",
                message="Chat processing failed",
                code="internal_error",
                status_code=500,
                details={"reason": str(exc)},
            )
        finally:
            reset_sink(token)
            queue.put_nowait(None)  # sentinel: no more events

    task = asyncio.create_task(run())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield (event.model_dump_json() + "\n").encode("utf-8")
    finally:
        # A disconnected client (or an early break) must not leave the graph running.
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def handle_chat(
    request: ChatRequest, request_id: str, role: str = "viewer"
) -> ChatResponse:
    """Non-streaming façade over stream_chat: drains the events, returns the result."""
    final_payload: dict | None = None
    error: dict | None = None

    async for line in stream_chat(request, request_id, role):
        event = json.loads(line)
        if event["type"] == "final":
            final_payload = event["data"]
        elif event["type"] == "error":
            error = event["data"]
            error["message"] = event["message"]

    if final_payload is not None:
        return ChatResponse(**final_payload)

    error = error or {}
    err = AppError(
        error.get("message", "chat processing failed"),
        details=error.get("details", {}),
    )
    err.code = error.get("code", "internal_error")
    err.status_code = error.get("status_code", 500)
    raise err
