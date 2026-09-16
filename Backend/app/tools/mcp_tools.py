"""Loads the employee-directory and service-catalog tools from the MCP server.

The server runs as a stdio subprocess (app/mcp_server/server.py) and calls the mock
REST API. If it cannot be reached the app starts anyway with those two tools
missing — the retrieval agent is told to work without them rather than failing the
request.
"""
import logging
import os
import sys
from pathlib import Path

from langchain_core.tools import BaseTool

from app.config import get_settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_tools: list[BaseTool] = []
_loaded = False


def _server_config() -> dict:
    settings = get_settings()
    env = dict(os.environ)
    env["MOCK_API_BASE_URL"] = settings.mock_api_base_url
    return {
        "enterprise-directory": {
            "command": sys.executable,
            "args": ["-m", "app.mcp_server.server"],
            "transport": "stdio",
            "cwd": str(PROJECT_ROOT),
            "env": env,
        }
    }


async def load_mcp_tools() -> list[BaseTool]:
    """Connect to the MCP server and return its tools. Returns [] on any failure."""
    global _tools, _loaded

    settings = get_settings()
    if not settings.enable_mcp:
        logger.info("mcp disabled by config", extra={"event": "mcp.disabled"})
        _loaded = True
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(_server_config())
        _tools = await client.get_tools()
        _loaded = True
        logger.info(
            "mcp tools loaded",
            extra={
                "event": "mcp.loaded",
                "tool_count": len(_tools),
                "tools": [t.name for t in _tools],
            },
        )
    except Exception:  # noqa: BLE001 - degraded start is better than no start
        _tools = []
        _loaded = True
        logger.exception(
            "mcp server unavailable; continuing without directory tools",
            extra={"event": "mcp.unavailable"},
        )
    return _tools


def get_mcp_tools() -> list[BaseTool]:
    """Tools loaded at startup. Empty when MCP is disabled or unreachable."""
    return _tools


def mcp_available() -> bool:
    return bool(_tools)
