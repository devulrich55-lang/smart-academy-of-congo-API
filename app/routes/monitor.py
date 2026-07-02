from fastapi import APIRouter, Depends, Query

from app.deps import require_roles
from app.services import monitor_service

router = APIRouter(prefix="/admin/monitor", tags=["monitor"])

ERROR_MAP = {
    "NOT_FOUND": (404, "Incident introuvable"),
    "INVALID_INPUT": (400, "Données invalides"),
    "FORBIDDEN": (403, "Accès refusé"),
}


def _map_error(exc: ValueError) -> None:
    from fastapi import HTTPException

    code = str(exc)
    if code in ERROR_MAP:
        status, message = ERROR_MAP[code]
        raise HTTPException(status_code=status, detail={"error": code, "message": message})
    raise exc


@router.get("/overview")
def monitor_overview(
    user: dict = Depends(require_roles("superadmin")),
    notify: bool = Query(False),
):
    try:
        return monitor_service.get_overview(user, persist=True, notify=notify)
    except ValueError as e:
        _map_error(e)


@router.get("/security-pulse")
def monitor_security_pulse(user: dict = Depends(require_roles("superadmin"))):
    del user
    return monitor_service.security_pulse()


@router.get("/incidents")
def monitor_incidents(
    user: dict = Depends(require_roles("superadmin")),
    limit: int = Query(50, ge=1, le=200),
):
    del user
    return {"incidents": monitor_service.list_incidents(limit)}


@router.patch("/incidents/{incident_id}")
def monitor_resolve_incident(
    incident_id: str,
    body: dict,
    user: dict = Depends(require_roles("superadmin")),
):
    try:
        incident = monitor_service.resolve_incident(
            user,
            incident_id,
            body.get("status") or "resolved",
        )
        return {"ok": True, "incident": incident}
    except ValueError as e:
        _map_error(e)
