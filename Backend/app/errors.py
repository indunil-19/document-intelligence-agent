"""Domain exceptions. Everything raised deliberately by the app derives from AppError."""


class AppError(Exception):
    """Base application error."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class RetrievalError(AppError):
    status_code = 502
    code = "retrieval_error"


class LLMError(AppError):
    status_code = 502
    code = "llm_error"


class ToolError(AppError):
    """Raised inside a tool. Surfaced back to the agent rather than to the client."""

    status_code = 500
    code = "tool_error"
