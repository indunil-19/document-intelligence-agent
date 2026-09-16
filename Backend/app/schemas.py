"""Request / response models for the public API."""
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None, description="Reuse to keep cached filter results across turns."
    )


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
