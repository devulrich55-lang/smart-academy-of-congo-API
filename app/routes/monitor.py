from fastapi import APIRouter, Depends, Query

from app.deps import require_roles
from app.services import monitor_service, monitor_sata_service

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
def monitor_update_incident(
    incident_id: str,
    body: dict,
    user: dict = Depends(require_roles("superadmin")),
):
    try:
        incident = monitor_sata_service.update_incident(user, incident_id, body or {})
        return {"ok": True, "incident": incident}
    except ValueError as e:
        _map_error(e)


@router.get("/logs")
def monitor_logs(
    user: dict = Depends(require_roles("superadmin")),
    q: str | None = Query(None),
    category: str | None = Query(None),
    level: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
):
    del user
    logs = monitor_sata_service.list_logs(q=q, category=category, level=level, limit=limit)
    repeats = monitor_sata_service.detect_repeated_errors(logs)
    return {"logs": logs, "repeats": repeats, "count": len(logs)}


@router.post("/heal")
def monitor_heal(
    body: dict,
    user: dict = Depends(require_roles("superadmin")),
):
    del user
    try:
        action = (body or {}).get("action") or "ping_api"
        result = monitor_sata_service.trigger_heal(action)
        return {"ok": result.get("ok", False), **result}
    except ValueError as e:
        _map_error(e)


@router.post("/alerts/dispatch")
def monitor_dispatch_alert(
    body: dict,
    user: dict = Depends(require_roles("superadmin")),
):
    del user
    return monitor_sata_service.dispatch_alert(body or {})


@router.post("/simulate")
def monitor_simulate(
    body: dict,
    user: dict = Depends(require_roles("superadmin")),
):
    del user
    scenario = (body or {}).get("scenario") or ""
    if scenario == "stop" or (body or {}).get("stop"):
        return monitor_sata_service.stop_simulation()
    try:
        return monitor_sata_service.start_simulation(scenario or "traffic")
    except ValueError as e:
        _map_error(e)
