"""Application settings, loaded from environment / .env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor the .env path to the project root. A bare ".env" would resolve against the
# current working directory, so the file would be silently ignored whenever the
# process is started from anywhere but here (IDE run configs, uvicorn from a parent
# directory, containers with a different WORKDIR).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    # Any OpenAI-compatible chat-completions gateway.
    llm_base_url: str = "https://api.ai.kodekloud.com/v1"
    llm_api_key: str = ""
    model: str = "gpt-oss-120b"
    # Used by analytics sub-agents. Defaults to the main model; override to trade
    # cost for depth on large fan-outs.
    subagent_model: str = "gpt-oss-120b"
    max_tokens: int = 4096
    temperature: float = 0.0
    llm_timeout_seconds: float = 120.0

    # --- Service ---
    app_name: str = "doc-intelligence-api"
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000

    # Base URL of the mock backend the MCP tools call.
    mock_api_base_url: str = "http://127.0.0.1:8000/mock-api"
    enable_mcp: bool = True

    # --- Rate limiting ---
    rate_limit_enabled: bool = True
    # Tokens refilled per window. This is the sustained request rate per caller.
    rate_limit_requests: int = 20
    rate_limit_window_seconds: float = 60.0
    # Bucket capacity, i.e. how much unused allowance can be spent at once.
    # 0 means "same as rate_limit_requests".
    rate_limit_burst: int = 0
    # Buckets untouched for this long are dropped to bound memory.
    rate_limit_idle_ttl_seconds: float = 900.0

    # --- Hybrid search (app/rag/) ---
    # Local ONNX embedding model (fastembed) - no API key, runs on CPU, weights are
    # downloaded once and cached under FASTEMBED_CACHE_DIR / ~/.cache.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Candidates pulled from each of dense/sparse before the two are fused. Larger
    # catches more near-misses on one axis; smaller is cheaper per query.
    hybrid_candidate_k: int = 20
    # Fusion weight: hybrid = alpha * dense + (1 - alpha) * sparse. 1.0 = pure
    # dense/semantic, 0.0 = pure BM25/keyword.
    hybrid_alpha: float = 0.5

    # --- Observability (LangSmith) ---
    # Optional. Off by default - this app runs fine with none of this set.
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "doc-intelligence-api"
    # Override for self-hosted or region-specific LangSmith deployments (e.g. EU).
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # --- Retrieval behaviour ---
    # Tool rounds the retrieval agent may take before it is cut off.
    max_retrieval_steps: int = 10
    # Above this many cached documents, the analytics tool fans out to sub-agents.
    analytics_fanout_threshold: int = 10
    analytics_chunk_size: int = 5
    analytics_max_subagents: int = 6


@lru_cache
def get_settings() -> Settings:
    return Settings()


def env_file_status() -> dict:
    """Where the .env was looked for and whether it was there.

    Reported at startup so a missing or misplaced .env is visible in the logs
    rather than showing up later as an unexplained empty setting.
    """
    return {"env_file": str(ENV_FILE), "env_file_found": ENV_FILE.is_file()}
