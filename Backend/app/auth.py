"""Hardcoded demo users, roles, and per-role tool access.

DEMO ONLY. Plaintext passwords, no hashing, no tokens, no expiry - this exists to
demonstrate role-based tool access, not to gate a real system. Anyone who knows (or
guesses) a username can set it as X-User-Id and get that role's tools without a
password, since there is no signed session - /auth/login just checks the password
once so the frontend has a real gate to walk through.
"""
from dataclasses import dataclass

from fastapi import Request

# --- Users -------------------------------------------------------------------


@dataclass(frozen=True)
class DemoUser:
    username: str
    password: str
    role: str
    display_name: str


DEMO_USERS: dict[str, DemoUser] = {
    "viewer": DemoUser("viewer", "viewer123", "viewer", "Viewer (read-only)"),
    "analyst": DemoUser("analyst", "analyst123", "analyst", "Analyst"),
    "admin": DemoUser("admin", "admin123", "admin", "Admin"),
}

DEFAULT_ROLE = "viewer"


def authenticate(username: str, password: str) -> DemoUser | None:
    user = DEMO_USERS.get(username)
    if user is not None and user.password == password:
        return user
    return None


def get_role(username: str) -> str | None:
    user = DEMO_USERS.get(username)
    return user.role if user else None


# --- Tool access ---------------------------------------------------------------
# Exact tool names, matched against each BaseTool's .name at agent-build time.

ROLE_TOOLS: dict[str, set[str]] = {
    "viewer": {"document_search"},
    "analyst": {
        "document_search",
        "metadata_retrieval",
        "filter_by_metadata",
        "analyze_documents",
    },
    "admin": {
        "document_search",
        "metadata_retrieval",
        "filter_by_metadata",
        "analyze_documents",
        "employee_directory",
        "service_catalog",
    },
}

ROLES = tuple(ROLE_TOOLS)


def select_tools_for_role(role: str, tools: list) -> list:
    """Filter a tool list down to what a role may use.

    Pure and LLM-free by design, so tool binding is unit-testable without a live
    agent or model. Unknown roles get nothing rather than everything - failing
    closed, not open.
    """
    allowed = ROLE_TOOLS.get(role, set())
    return [t for t in tools if getattr(t, "name", None) in allowed]


def resolve_role(request: Request) -> str:
    """Identify the caller's role from the same X-User-Id header rate limiting
    already uses for caller identity. Missing or unknown usernames default to the
    least-privileged role rather than rejecting the request - fails closed.
    """
    username = (request.headers.get("x-user-id") or "").strip()
    return get_role(username) or DEFAULT_ROLE
