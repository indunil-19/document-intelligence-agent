"""MCP server exposing the employee directory and service catalog.

Runs as a stdio subprocess launched by the retrieval agent. Each tool is a thin
async wrapper over the mock REST API, so swapping in the real HR / catalog
service is a base-URL change.

Run standalone:  python -m app.mcp_server.server
"""
import logging
import os

import httpx
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("mcp.enterprise_directory")

BASE_URL = os.getenv("MOCK_API_BASE_URL", "http://127.0.0.1:8000/mock-api")
TIMEOUT = float(os.getenv("MCP_HTTP_TIMEOUT", "10"))

mcp = FastMCP("enterprise-directory")


async def _get(path: str, params: dict | None = None) -> dict:
    """GET against the mock API. Errors are returned, not raised, so the agent can react."""
    clean = {k: v for k, v in (params or {}).items() if v}
    url = f"{BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(url, params=clean)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("mock api returned %s for %s", exc.response.status_code, url)
        return {"error": f"upstream returned HTTP {exc.response.status_code}", "url": url}
    except httpx.HTTPError as exc:
        logger.warning("mock api unreachable: %s", exc)
        return {"error": f"upstream unreachable: {exc}", "url": url}


@mcp.tool()
async def employee_directory(
    name: str = "",
    team: str = "",
    department: str = "",
    role: str = "",
) -> dict:
    """Look up employees in the company directory.

    Use for questions about who owns something, who to contact, reporting lines or
    team membership. All filters are optional substring matches; pass none to list
    everyone. Returns employee_id, name, role, department, team, email and manager.
    """
    return await _get(
        "/employees", {"name": name, "team": team, "department": department, "role": role}
    )


@mcp.tool()
async def service_catalog(name: str = "", owner_team: str = "", tier: str = "") -> dict:
    """Look up internal services in the service catalog.

    Use for questions about service ownership, on-call rotation, tier, dependencies
    or which runbook covers a service. All filters are optional substring matches;
    pass none to list every service.
    """
    return await _get("/services", {"name": name, "owner_team": owner_team, "tier": tier})


if __name__ == "__main__":
    mcp.run(transport="stdio")
