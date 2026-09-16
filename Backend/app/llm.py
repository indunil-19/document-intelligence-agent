"""Chat model factory. One place that knows which model each agent runs on."""
import logging
from functools import lru_cache

from langchain_anthropic import ChatAnthropic

from app.config import get_settings
from app.errors import LLMError

logger = logging.getLogger(__name__)


@lru_cache
def get_llm(model: str | None = None, max_tokens: int | None = None) -> ChatAnthropic:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise LLMError(
            "ANTHROPIC_API_KEY is not set",
            details={"hint": "copy .env.example to .env and set the key"},
        )
    return ChatAnthropic(
        model=model or settings.model,
        max_tokens=max_tokens or settings.max_tokens,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
        api_key=settings.anthropic_api_key,
    )


def get_subagent_llm() -> ChatAnthropic:
    """Model used by analytics sub-agents. Same as the main model unless overridden."""
    settings = get_settings()
    return get_llm(model=settings.subagent_model, max_tokens=settings.max_tokens)
