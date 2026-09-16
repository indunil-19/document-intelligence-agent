"""Mock enterprise REST API.

Mounted under /mock-api on the same app. The MCP server calls these endpoints over
HTTP, so the MCP tools exercise a real network hop exactly as they would against
the genuine employee directory and service catalog.
"""
import logging

from fastapi import APIRouter, HTTPException, Query

from app.mock.documents import EMPLOYEES, SERVICES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mock-api", tags=["mock-api"])


def _matches(record: dict, field: str, value: str | None) -> bool:
    if value is None:
        return True
    return value.lower() in str(record.get(field) or "").lower()


@router.get("/employees")
async def list_employees(
    name: str | None = Query(None),
    team: str | None = Query(None),
    department: str | None = Query(None),
    role: str | None = Query(None),
) -> dict:
    results = [
        e for e in EMPLOYEES
        if _matches(e, "name", name)
        and _matches(e, "team", team)
        and _matches(e, "department", department)
        and _matches(e, "role", role)
    ]
    return {"count": len(results), "results": results}


@router.get("/employees/{employee_id}")
async def get_employee(employee_id: str) -> dict:
    for e in EMPLOYEES:
        if e["employee_id"].lower() == employee_id.lower():
            return e
    raise HTTPException(status_code=404, detail=f"employee {employee_id} not found")


@router.get("/services")
async def list_services(
    name: str | None = Query(None),
    owner_team: str | None = Query(None),
    tier: str | None = Query(None),
) -> dict:
    results = [
        s for s in SERVICES
        if _matches(s, "name", name)
        and _matches(s, "owner_team", owner_team)
        and _matches(s, "tier", tier)
    ]
    return {"count": len(results), "results": results}


@router.get("/services/{service_id}")
async def get_service(service_id: str) -> dict:
    for s in SERVICES:
        if s["service_id"].lower() == service_id.lower() or s["name"].lower() == service_id.lower():
            return s
    raise HTTPException(status_code=404, detail=f"service {service_id} not found")
