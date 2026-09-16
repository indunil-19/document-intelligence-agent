"""LangSmith tracing for the LangGraph pipeline.

LangChain's tracer activates itself by reading environment variables directly
(LANGSMITH_TRACING / LANGSMITH_API_KEY / LANGSMITH_PROJECT / LANGSMITH_ENDPOINT) -
pydantic-settings reads .env into our Settings object but never mutates
os.environ, so Settings alone would not turn tracing on. configure() is the one
place that bridges the two, keeping .env as the single source of truth instead of
requiring the same values to be set twice.

Ordering matters and is easy to get wrong: langsmith.utils.get_env_var() is
`functools.lru_cache`d, so the first process-wide read of a given var permanently
sticks - if anything asks "is tracing on?" before configure() runs, a later
os.environ mutation has no effect for the rest of the process. configure() is
called from app/main.py immediately after settings are loaded, before any graph or
agent is built and long before the first real request could trigger an LLM call, so
nothing has asked that question yet.
"""
import logging
import os
from collections.abc import MutableMapping

from app.config import get_settings

logger = logging.getLogger(__name__)


def configure(environ: MutableMapping[str, str] | None = None) -> bool:
    """Push LangSmith settings into the process environment.

    Returns whether tracing ended up enabled. A no-op (tracing stays off) if
    LANGSMITH_TRACING is false or no API key is set - this is optional
    observability, never a hard requirement to run the app.

    `environ` defaults to the real os.environ; tests inject a plain dict instead so
    they never touch real process state.
    """
    settings = get_settings()
    env = os.environ if environ is None else environ

    if not settings.langsmith_tracing:
        return False
    if not settings.langsmith_api_key:
        logger.warning(
            "LANGSMITH_TRACING is enabled but LANGSMITH_API_KEY is empty; "
            "tracing stays off",
            extra={"event": "tracing.missing_api_key"},
        )
        return False

    env["LANGSMITH_TRACING"] = "true"
    env["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    env["LANGSMITH_PROJECT"] = settings.langsmith_project
    env["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    logger.info(
        "LangSmith tracing enabled",
        extra={
            "event": "tracing.enabled",
            "project": settings.langsmith_project,
            "endpoint": settings.langsmith_endpoint,
        },
    )
    return True


async def check_connectivity() -> bool:
    """Verify the API key actually works against the configured endpoint.

    Advisory only, mirrors app.llm.check_model_availability() - a bad key shows up
    as a warning in the logs at startup rather than a mystery on the first traced
    call. Never raises and never blocks startup.
    """
    settings = get_settings()
    if not settings.langsmith_tracing or not settings.langsmith_api_key:
        return False

    try:
        import asyncio

        from langsmith import Client

        def _ping() -> None:
            client = Client(
                api_key=settings.langsmith_api_key, api_url=settings.langsmith_endpoint
            )
            # Cheapest read that proves the key and endpoint actually work.
            next(iter(client.list_projects(limit=1)), None)

        await asyncio.to_thread(_ping)
        return True
    except Exception as exc:  # noqa: BLE001 - advisory only, never fatal
        logger.warning(
            "could not verify LangSmith connectivity",
            extra={"event": "tracing.connectivity_failed", "reason": str(exc)},
        )
        return False


def trace_config(*, request_id: str, session_id: str, role: str) -> dict:
    """RunnableConfig extras for one chat turn.

    Tags/metadata on the top-level graph.ainvoke() call become the root LangSmith
    run's tags/metadata; every node's own LLM and tool calls automatically nest
    under it as child runs via LangChain's context-based run tree - they don't need
    this config re-passed into each inner .ainvoke() call individually. Carrying the
    same request_id/session_id already used to correlate the structured JSON logs
    and the Agent Activity Panel means a trace for any turn is one lookup away from
    the matching log lines, not a separate investigation.
    """
    return {
        "run_name": "chat_turn",
        "tags": [f"role:{role}"],
        "metadata": {
            "request_id": request_id,
            "session_id": session_id,
            "role": role,
        },
    }
