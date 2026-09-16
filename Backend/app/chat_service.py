"""Glue between the HTTP layer and the agent graph."""
import logging
import time

from app.errors import AppError
from app.graph.builder import get_graph
from app.logging_config import request_id_var, session_id_var
from app.schemas import ChatRequest, ChatResponse, SourceDocument
from app.session import get_session_store

logger = logging.getLogger(__name__)


async def handle_chat(request: ChatRequest, request_id: str) -> ChatResponse:
    store = get_session_store()
    session = await store.get_or_create(request.session_id)
    session_id_var.set(session.session_id)

    started = time.perf_counter()
    logger.info(
        "chat request received",
        extra={"event": "chat.started", "message_chars": len(request.message)},
    )

    initial_state = {
        "session_id": session.session_id,
        "question": request.message,
        "history": list(session.history),
        "degraded": False,
        "notes": [],
    }

    try:
        final_state = await get_graph().ainvoke(initial_state)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("graph execution failed", extra={"event": "chat.failed"})
        raise AppError("chat processing failed", details={"reason": str(exc)}) from exc

    answer = final_state.get("answer", "")
    session.add_turn("user", request.message)
    session.add_turn("assistant", answer)

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

    return ChatResponse(
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
