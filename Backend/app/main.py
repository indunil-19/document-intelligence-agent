"""FastAPI entrypoint.

    uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.chat_service import handle_chat
from app.config import env_file_status, get_settings
from app.errors import AppError
from app.graph.builder import build_graph
from app.graph.nodes.retrieval import build_retrieval_agent
from app.llm import check_model_availability
from app.logging_config import (
    configure_logging,
    new_request_id,
    request_id_var,
    session_id_var,
)
from app.mock.mock_api import router as mock_api_router
from app.mock.rag import DOCUMENT_TYPES
from app.schemas import ChatRequest, ChatResponse, ErrorResponse
from app.tools.mcp_tools import load_mcp_tools, mcp_available

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("settings loaded", extra={"event": "app.settings", **env_file_status()})
    # MCP tools first: the retrieval agent binds whatever is available at build time.
    await load_mcp_tools()
    build_retrieval_agent()
    build_graph()
    await check_model_availability()
    logger.info(
        "application ready",
        extra={
            "event": "app.ready",
            "mcp_available": mcp_available(),
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


@app.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(request: ChatRequest) -> ChatResponse:
    """Ask a question about internal documentation.

    Pass the returned `session_id` back on the next call to keep conversation
    history and cached filter results.
    """
    return await handle_chat(request, request_id_var.get())


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "model": settings.model,
        "llm_base_url": settings.llm_base_url,
        "mcp_available": mcp_available(),
        "document_types": DOCUMENT_TYPES,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
