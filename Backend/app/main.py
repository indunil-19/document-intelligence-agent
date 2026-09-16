"""FastAPI entrypoint.

    uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from app.auth import ROLE_TOOLS, authenticate, resolve_role
from app.chat_service import handle_chat, stream_chat
from app.config import env_file_status, get_settings
from app.errors import AppError, AuthenticationError
from app.graph.builder import build_graph
from app.graph.nodes.retrieval import build_retrieval_agents
from app.llm import check_model_availability
from app.logging_config import (
    configure_logging,
    new_request_id,
    request_id_var,
    session_id_var,
)
from app.mock.mock_api import router as mock_api_router
from app.rag import DOCUMENT_TYPES, get_rag_store
from app.rate_limit import enforce_rate_limit
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    SessionResponse,
)
from app.session import get_session_store
from app.tools.mcp_tools import load_mcp_tools, mcp_available

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("settings loaded", extra={"event": "app.settings", **env_file_status()})
    # MCP tools first: the retrieval agents bind whatever is available at build time.
    await load_mcp_tools()
    build_retrieval_agents()
    build_graph()
    await check_model_availability()
    # Embeds the whole corpus once so the first /chat isn't the one paying for model
    # load + embedding latency. Failure here is non-fatal - search() falls back to
    # sparse-only, this just avoids a silent surprise on the first request.
    dense_available = await get_rag_store().warm_up()
    logger.info(
        "application ready",
        extra={
            "event": "app.ready",
            "mcp_available": mcp_available(),
            "dense_search_available": dense_available,
            "model": settings.model,
            "llm_base_url": settings.llm_base_url,
        },
    )
    yield
    logger.info("application shutting down", extra={"event": "app.shutdown"})


app = FastAPI(
    title="Internal Document Intelligence API",
    description="Chat over internal company documents via a LangGraph multi-agent pipeline.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(mock_api_router)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    """Attach a request id to every log line emitted while handling the request."""
    request_id = request.headers.get("x-request-id") or new_request_id()
    request_token = request_id_var.set(request_id)
    session_token = session_id_var.set("-")
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response
    finally:
        request_id_var.reset(request_token)
        session_id_var.reset(session_token)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    logger.warning(
        "request failed",
        extra={"event": "http.app_error", "code": exc.code, "details": exc.details},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            code=exc.code,
            message=exc.message,
            request_id=request_id_var.get(),
            details=exc.details,
        ).model_dump(),
        headers=exc.headers or None,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            code="validation_error",
            message="Request body is invalid.",
            request_id=request_id_var.get(),
            details={"errors": exc.errors()},
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception("unhandled error", extra={"event": "http.unhandled"})
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            code="internal_error",
            message="Something went wrong handling the request.",
            request_id=request_id_var.get(),
        ).model_dump(),
    )


@app.post("/auth/login", response_model=LoginResponse, tags=["auth"])
async def login(payload: LoginRequest) -> LoginResponse:
    """Demo login. Checks a hardcoded username/password and returns the role.

    There are exactly three accounts, one per role - see app/auth.py. This is not a
    real auth system: no token is issued, and the same X-User-Id header used here is
    trusted as-is on /chat and /chat/stream. It exists so the frontend has an actual
    gate to walk through instead of a bare role dropdown.
    """
    user = authenticate(payload.username, payload.password)
    if user is None:
        raise AuthenticationError("Invalid username or password.")
    return LoginResponse(
        username=user.username, role=user.role, display_name=user.display_name
    )


@app.post("/session", response_model=SessionResponse, tags=["chat"])
async def create_session() -> SessionResponse:
    """Mint a fresh chat session id.

    Call this once per new conversation and pass the id back as `session_id` on
    every /chat or /chat/stream call to keep history and cached filter results.
    """
    session = await get_session_store().get_or_create(None)
    return SessionResponse(session_id=session.session_id)


@app.post(
    "/chat",
    response_model=ChatResponse,
    tags=["chat"],
    dependencies=[Depends(enforce_rate_limit)],
)
async def chat(request: ChatRequest, http_request: Request) -> ChatResponse:
    """Ask a question about internal documentation.

    Pass the returned `session_id` back on the next call to keep conversation
    history and cached filter results. Which tools the retrieval agent may use
    depends on the caller's role, resolved from the X-User-Id header (see
    app/auth.py); an unrecognised or missing header defaults to the viewer role.
    """
    role = resolve_role(http_request)
    return await handle_chat(request, request_id_var.get(), role)


@app.post(
    "/chat/stream",
    tags=["chat"],
    dependencies=[Depends(enforce_rate_limit)],
)
async def chat_stream(request: ChatRequest, http_request: Request) -> StreamingResponse:
    """Same as /chat, but streams NDJSON activity events as the turn runs.

    Each line is one app.schemas.ActivityEvent. The stream ends with a `"final"`
    line carrying the completed ChatResponse payload, or an `"error"` line if the
    turn failed. This is what the Streamlit Agent Activity Panel consumes.
    """
    role = resolve_role(http_request)
    return StreamingResponse(
        stream_chat(request, request_id_var.get(), role),
        media_type="application/x-ndjson",
    )


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "model": settings.model,
        "llm_base_url": settings.llm_base_url,
        "mcp_available": mcp_available(),
        "document_types": DOCUMENT_TYPES,
        "role_tools": {role: sorted(tools) for role, tools in ROLE_TOOLS.items()},
        "dense_search_available": get_rag_store().dense_available,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
