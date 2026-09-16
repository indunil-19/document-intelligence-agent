"""Real-time activity events for the frontend's Agent Activity Panel.

A sink callback is threaded through a ContextVar - the same technique
logging_config.py uses for request_id_var/session_id_var - so nodes and tools can
emit() without an event-sink parameter being plumbed through every function
signature. asyncio copies the current context when a task is created, so this
still works correctly across `asyncio.gather`'d tool calls; each gathered task sees
the sink that was active when it was spawned.

Tool call visibility (tool_start/tool_end/tool_error) comes from ActivityCallbackHandler,
a standard LangChain callback attached to the retrieval agent's invocation - it fires
uniformly for every tool the agent calls, including MCP tools, with no per-tool code.
Everything else (which node is active, memory writes, validation checks) is emitted
inline at the point it actually happens, because there is no generic hook for those.
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from app.schemas import ActivityEvent

logger = logging.getLogger(__name__)

Sink = Callable[[ActivityEvent], None]

_sink_var: ContextVar[Sink | None] = ContextVar("activity_sink", default=None)


def set_sink(sink: Sink | None):
    return _sink_var.set(sink)


def reset_sink(token) -> None:
    _sink_var.reset(token)


@contextmanager
def activity_sink(sink: Sink | None):
    """Scope a sink to a `with` block, resetting it on the way out."""
    token = set_sink(sink)
    try:
        yield
    finally:
        reset_sink(token)


def emit(event_type: str, *, node: str | None = None, tool: str | None = None,
          message: str = "", **data: Any) -> None:
    """Publish one activity event. A no-op when nothing is listening."""
    sink = _sink_var.get()
    if sink is None:
        return
    try:
        sink(ActivityEvent(type=event_type, node=node, tool=tool, message=message, data=data))
    except Exception:  # noqa: BLE001 - a broken panel must never break the chat turn
        logger.exception("activity sink raised", extra={"event": "activity.sink_error"})


def _preview(value: Any, limit: int = 300) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ActivityCallbackHandler(AsyncCallbackHandler):
    """Emits tool_start/tool_end/tool_error for every tool call an agent makes.

    Attach via `config={"callbacks": [handler]}` on the agent invocation - LangChain
    propagates callbacks to every nested tool call automatically, so this covers the
    RAG tools and the MCP-adapter tools identically without touching either.
    """

    def __init__(self):
        super().__init__()
        # on_tool_end/on_tool_error only receive run_id, not the tool name - track it.
        self._names: dict[UUID, str] = {}

    async def on_tool_start(
        self, serialized: dict, input_str: str, *, run_id: UUID, **kwargs: Any
    ) -> None:
        name = serialized.get("name", "unknown_tool")
        self._names[run_id] = name
        emit(
            "tool_start",
            tool=name,
            message=f"Calling {name}",
            input=_preview(kwargs.get("inputs") or input_str),
        )

    async def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        name = self._names.pop(run_id, "unknown_tool")
        emit("tool_end", tool=name, message=f"{name} finished", output=_preview(output))

    async def on_tool_error(
        self, error: BaseException, *, run_id: UUID, **kwargs: Any
    ) -> None:
        name = self._names.pop(run_id, "unknown_tool")
        emit("tool_error", tool=name, message=f"{name} failed: {error}")
