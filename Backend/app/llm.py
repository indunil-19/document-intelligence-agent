"""Chat model factory. One place that knows which model each agent runs on.

Talks to any OpenAI-compatible chat-completions gateway via LLM_BASE_URL, so the
provider is a configuration change rather than a code change.
"""
import logging
from functools import lru_cache

import httpx
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.errors import LLMError

logger = logging.getLogger(__name__)


@lru_cache
def get_llm(model: str | None = None, max_tokens: int | None = None) -> ChatOpenAI:
    settings = get_settings()
    if not settings.llm_api_key:
        raise LLMError(
            "LLM_API_KEY is not set",
            details={"hint": "copy .env.example to .env and set LLM_API_KEY"},
        )
    return ChatOpenAI(
        model=model or settings.model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        max_tokens=max_tokens or settings.max_tokens,
        temperature=settings.temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
    )


def get_subagent_llm() -> ChatOpenAI:
    """Model used by analytics sub-agents. Same as the main model unless overridden."""
    settings = get_settings()
    return get_llm(model=settings.subagent_model, max_tokens=settings.max_tokens)


async def list_available_models() -> list[str]:
    """Ask the gateway which models it serves. Returns [] if it will not say."""
    settings = get_settings()
    url = f"{settings.llm_base_url.rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            url, headers={"Authorization": f"Bearer {settings.llm_api_key}"}
        )
        response.raise_for_status()
        payload = response.json()
    return sorted(m["id"] for m in payload.get("data", []) if "id" in m)


async def check_model_availability() -> None:
    """Startup check: warn loudly if the configured model is not on the gateway.

    Never fatal — the gateway may not expose /models, and that is not a reason to
    refuse to start.
    """
    settings = get_settings()
    try:
        models = await list_available_models()
    except Exception as exc:  # noqa: BLE001 - advisory check only
        logger.warning(
            "could not list gateway models",
            extra={"event": "llm.models_unavailable", "reason": str(exc)},
        )
        return

    configured = {settings.model, settings.subagent_model}
    missing = sorted(configured - set(models))
    if missing:
        logger.warning(
            "configured model is not served by this gateway",
            extra={
                "event": "llm.model_missing",
                "missing": missing,
                "available": models,
            },
        )
    else:
        logger.info(
            "models confirmed",
            extra={"event": "llm.models_ok", "models": sorted(configured)},
        )
