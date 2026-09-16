"""Loads agent instruction files from app/agents/instructions/.

Instructions live in markdown next to the code so they can be edited and reviewed
without touching Python. Files are read once and cached per process.
"""
import logging
from functools import lru_cache
from pathlib import Path

from app.errors import AppError

logger = logging.getLogger(__name__)

INSTRUCTIONS_DIR = Path(__file__).parent / "instructions"


@lru_cache
def load_instructions(agent_name: str) -> str:
    """Return the instruction text for an agent. Raises if the file is missing."""
    path = INSTRUCTIONS_DIR / f"{agent_name}.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise AppError(
            f"instruction file missing for agent '{agent_name}'",
            details={"path": str(path)},
        ) from exc
    if not text:
        raise AppError(f"instruction file for agent '{agent_name}' is empty")
    logger.debug(
        "instructions loaded",
        extra={"event": "instructions.loaded", "agent": agent_name, "chars": len(text)},
    )
    return text
