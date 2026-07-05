from fastapi import APIRouter, Depends, Query

from app.deps import require_roles
from app.services import attack_shield_service, tech_manager_service, ticket_workflow_service

router = APIRouter(prefix="/admin/tech-manager", tags=["tech-manager"])

ERROR_MAP = {
    "NOT_FOUND": (404, "Ticket introuvable"),
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
def tech_manager_overview(user: dict = Depends(require_roles("techmanager", "superadmin"))):
    return tech_manager_service.get_overview(user)


@router.get("/tickets")
def tech_manager_tickets(
    user: dict = Depends(require_roles("techmanager", "superadmin")),
    filter: str = Query("all", alias="filter"),
    limit: int = Query(150, ge=1, le=200),
):
    tickets = tech_manager_service.list_tickets(user, filter, limit)
    return {"tickets": tickets, "count": len(tickets), "filter": filter}


@router.post("/tickets/{ticket_id}/assign")
def tech_manager_assign(
    ticket_id: str,
    body: dict,
    user: dict = Depends(require_roles("techmanager", "superadmin")),
):
    try:
        ticket = tech_manager_service.assign_ticket(
            user,
            ticket_id,
            (body or {}).get("assignee") or "",
            (body or {}).get("priority"),
        )
        return {"ok": True, "ticket": ticket}
    except ValueError as e:
        _map_error(e)


@router.patch("/tickets/{ticket_id}/priority")
def tech_manager_priority(
    ticket_id: str,
    body: dict,
    user: dict = Depends(require_roles("techmanager", "superadmin")),
):
    try:
        ticket = tech_manager_service.set_priority(
            user, ticket_id, (body or {}).get("priority") or "medium"
        )
        return {"ok": True, "ticket": ticket}
    except ValueError as e:
        _map_error(e)


@router.post("/tickets/{ticket_id}/validate")
def tech_manager_validate(
    ticket_id: str,
    body: dict,
    user: dict = Depends(require_roles("techmanager", "superadmin")),
):
    try:
        ticket = tech_manager_service.validate_ticket(
            user, ticket_id, approve=(body or {}).get("approve", True) is not False
        )
        return {"ok": True, "ticket": ticket}
    except ValueError as e:
        _map_error(e)


@router.post("/tickets/{ticket_id}/production")
def tech_manager_production(
    ticket_id: str,
    user: dict = Depends(require_roles("techmanager", "superadmin")),
):
    try:
        ticket = tech_manager_service.approve_production(user, ticket_id)
        return {"ok": True, "ticket": ticket}
    except ValueError as e:
        _map_error(e)


@router.post("/tickets/{ticket_id}/resolve")
def tech_manager_resolve(
    ticket_id: str,
    user: dict = Depends(require_roles("techmanager", "superadmin")),
):
    try:
        ticket = tech_manager_service.resolve_ticket(user, ticket_id)
        return {"ok": True, "ticket": ticket}
    except ValueError as e:
        _map_error(e)


@router.get("/team")
def tech_manager_team(user: dict = Depends(require_roles("techmanager", "superadmin"))):
    return {"developers": tech_manager_service.list_team(user)}


@router.get("/stats")
def tech_manager_stats(user: dict = Depends(require_roles("techmanager", "superadmin"))):
    return tech_manager_service.team_stats(user)


@router.get("/workflow")
def tech_manager_workflow(
    user: dict = Depends(require_roles("techmanager", "superadmin", "developpeur")),
):
    del user
    return {
        "chain": ticket_workflow_service.WORKFLOW_CHAIN,
        "labels": ticket_workflow_service.STATUS_LABELS,
    }


@router.get("/shield/overview")
def tech_manager_shield_overview(
    user: dict = Depends(require_roles("techmanager", "superadmin")),
):
    del user
    return attack_shield_service.get_overview()


@router.get("/shield/events")
def tech_manager_shield_events(
    user: dict = Depends(require_roles("techmanager", "superadmin")),
    limit: int = Query(50, ge=1, le=200),
):
    del user
    events = attack_shield_service.list_events(limit)
    return {"events": events, "count": len(events)}


@router.get("/shield/blocked")
def tech_manager_shield_blocked(
    user: dict = Depends(require_roles("techmanager", "superadmin")),
):
    del user
    blocked = attack_shield_service.list_blocked()
    return {"blocked": blocked, "count": len(blocked)}


@router.get("/shield/honeypot")
def tech_manager_shield_honeypot(
    user: dict = Depends(require_roles("techmanager", "superadmin")),
    limit: int = Query(50, ge=1, le=200),
):
    del user
    hits = attack_shield_service.list_honeypot_hits(limit)
    return {"hits": hits, "count": len(hits)}


@router.post("/shield/unblock/{ip_hash}")
def tech_manager_shield_unblock(
    ip_hash: str,
    user: dict = Depends(require_roles("techmanager", "superadmin")),
):
    del user
    try:
        attack_shield_service.unblock_ip(ip_hash)
        return {"ok": True, "ipHash": ip_hash}
    except ValueError as e:
        _map_error(e)


@router.get("/shield/pulse")
def tech_manager_shield_pulse(
    user: dict = Depends(require_roles("techmanager", "superadmin")),
    since: str | None = Query(None),
):
    del user
    return attack_shield_service.get_pulse(since)


@router.get("/shield/trends")
def tech_manager_shield_trends(
    user: dict = Depends(require_roles("techmanager", "superadmin")),
    hours: int = Query(24, ge=1, le=72),
):
    del user
    return attack_shield_service.get_trends(hours)


@router.get("/shield/alerts/status")
def tech_manager_shield_alerts_status(
    user: dict = Depends(require_roles("techmanager", "superadmin")),
):
    del user
    return attack_shield_service.get_alerts_status()


@router.post("/shield/alerts/test")
def tech_manager_shield_alerts_test(
    user: dict = Depends(require_roles("techmanager", "superadmin")),
):
    del user
    return attack_shield_service.test_alert()


@router.post("/shield/block")
def tech_manager_shield_block(
    body: dict,
    user: dict = Depends(require_roles("techmanager", "superadmin")),
):
    del user
    try:
        result = attack_shield_service.manual_block_ip(
            (body or {}).get("ip") or "",
            reason=str((body or {}).get("reason") or "manual_block"),
            minutes=(body or {}).get("minutes"),
        )
        return {"ok": True, **result}
    except ValueError as e:
        _map_error(e)
