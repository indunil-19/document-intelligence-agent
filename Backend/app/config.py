"""Application settings, loaded from environment / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM ---
    anthropic_api_key: str = ""
    model: str = "claude-opus-5"
    # Used by analytics sub-agents. Defaults to the main model; override to trade
    # cost for depth on large fan-outs.
    subagent_model: str = "claude-opus-5"
    max_tokens: int = 4096
    llm_timeout_seconds: float = 120.0

    # --- Service ---
    app_name: str = "doc-intelligence-api"
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000

    # Base URL of the mock backend the MCP tools call.
    mock_api_base_url: str = "http://127.0.0.1:8000/mock-api"
    enable_mcp: bool = True

    # --- Retrieval behaviour ---
    max_retrieval_steps: int = 6
    # Above this many cached documents, the analytics tool fans out to sub-agents.
    analytics_fanout_threshold: int = 10
    analytics_chunk_size: int = 5
    analytics_max_subagents: int = 6


@lru_cache
def get_settings() -> Settings:
    return Settings()
