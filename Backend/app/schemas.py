"""Request / response models for the public API."""
import time
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None, description="Reuse to keep cached filter results across turns."
    )


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class LoginResponse(BaseModel):
    username: str
    role: str
    display_name: str


class SessionResponse(BaseModel):
    session_id: str


class ActivityEvent(BaseModel):
    """One line of the /chat/stream NDJSON body. Powers the Agent Activity Panel.

    `type` is the event vocabulary the frontend renders against:
      node_start / node_end   - active LangGraph node (orchestrate/retrieval/respond)
      tool_start / tool_end / tool_error - a tool call in flight or finished
      retrieval_status        - progress narration inside the retrieval agent
      memory_update           - session/document-cache writes and history updates
      validation              - scope/evidence checks (in-scope? did we find anything?)
      token                   - one streamed chunk of the final answer
      final                   - the completed ChatResponse-shaped payload
      error                   - the turn failed; this is the last line
    """

    type: Literal[
        "node_start",
        "node_end",
        "tool_start",
        "tool_end",
        "tool_error",
        "retrieval_status",
        "memory_update",
        "validation",
        "token",
        "final",
        "error",
    ]
    ts: float = Field(default_factory=time.time)
    node: str | None = None
    tool: str | None = None
    message: str = ""
    data: dict[str, Any] = {}


class SourceDocument(BaseModel):
    document_id: str
    title: str
    document_type: str
    score: float | None = None


class ChatResponse(BaseModel):
    session_id: str
    request_id: str
    answer: str
    intent: str
    route: Literal["retrieval", "direct", "out_of_scope"]
    in_scope: bool
    sources: list[SourceDocument] = []
    tools_used: list[str] = []
    degraded: bool = False


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = {}
