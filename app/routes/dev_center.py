from fastapi import APIRouter, Depends, Query

from app.deps import require_roles
from app.services import dev_center_service, ticket_workflow_service

router = APIRouter(prefix="/admin/dev-center", tags=["dev-center"])

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


@router.get("/profile")
def dev_center_profile(user: dict = Depends(require_roles("developpeur", "superadmin"))):
    return dev_center_service.get_profile(user)


@router.patch("/profile")
def dev_center_update_profile(
    body: dict,
    user: dict = Depends(require_roles("developpeur")),
):
    try:
        return dev_center_service.update_profile(user, body or {})
    except ValueError as e:
        _map_error(e)


@router.get("/stats")
def dev_center_stats(user: dict = Depends(require_roles("developpeur", "superadmin"))):
    return dev_center_service.get_stats(user)


@router.get("/tickets")
def dev_center_tickets(
    user: dict = Depends(require_roles("developpeur", "superadmin")),
    filter: str = Query("mine", alias="filter"),
    limit: int = Query(100, ge=1, le=200),
):
    tickets = dev_center_service.list_tickets(user, filter, limit)
    return {"tickets": tickets, "count": len(tickets), "filter": filter}


@router.get("/tickets/{ticket_id}")
def dev_center_ticket_detail(
    ticket_id: str,
    user: dict = Depends(require_roles("developpeur", "superadmin")),
):
    try:
        return {"ticket": dev_center_service.get_ticket(user, ticket_id)}
    except ValueError as e:
        _map_error(e)


@router.post("/tickets/{ticket_id}/assign")
def dev_center_assign_ticket(
    ticket_id: str,
    body: dict,
    user: dict = Depends(require_roles("developpeur", "superadmin")),
):
    try:
        assignee = (body or {}).get("assignee")
        ticket = dev_center_service.assign_ticket(user, ticket_id, assignee)
        return {"ok": True, "ticket": ticket}
    except ValueError as e:
        _map_error(e)


@router.patch("/tickets/{ticket_id}")
def dev_center_update_ticket(
    ticket_id: str,
    body: dict,
    user: dict = Depends(require_roles("developpeur", "superadmin")),
):
    try:
        ticket = dev_center_service.update_ticket(user, ticket_id, body or {})
        return {"ok": True, "ticket": ticket}
    except ValueError as e:
        _map_error(e)


@router.get("/developers")
def dev_center_developers(user: dict = Depends(require_roles("developpeur", "superadmin"))):
    return {"developers": dev_center_service.list_developers(user)}


@router.get("/performance")
def dev_center_performance(user: dict = Depends(require_roles("developpeur", "superadmin"))):
    email = str(user.get("email") or "").lower()
    return ticket_workflow_service.developer_performance(email)


@router.get("/projects")
def dev_center_projects(user: dict = Depends(require_roles("developpeur", "superadmin"))):
    email = str(user.get("email") or "").lower()
    return {"projects": ticket_workflow_service.list_projects_for_developer(email)}


@router.get("/tickets/{ticket_id}/comments")
def dev_center_comments(
    ticket_id: str,
    user: dict = Depends(require_roles("developpeur", "superadmin")),
):
    del user
    return {"comments": ticket_workflow_service.list_comments(ticket_id)}


@router.post("/tickets/{ticket_id}/comments")
def dev_center_add_comment(
    ticket_id: str,
    body: dict,
    user: dict = Depends(require_roles("developpeur", "superadmin")),
):
    try:
        comment = ticket_workflow_service.add_comment(user, ticket_id, (body or {}).get("body") or "")
        return {"ok": True, "comment": comment}
    except ValueError as e:
        _map_error(e)


@router.get("/tickets/{ticket_id}/history")
def dev_center_history(
    ticket_id: str,
    user: dict = Depends(require_roles("developpeur", "superadmin", "techmanager")),
):
    del user
    return {"history": ticket_workflow_service.list_history(ticket_id)}


@router.get("/tickets/{ticket_id}/time")
def dev_center_time(
    ticket_id: str,
    user: dict = Depends(require_roles("developpeur", "superadmin")),
):
    del user
    return {"entries": ticket_workflow_service.list_time_entries(ticket_id)}


@router.post("/tickets/{ticket_id}/time")
def dev_center_log_time(
    ticket_id: str,
    body: dict,
    user: dict = Depends(require_roles("developpeur", "superadmin")),
):
    try:
        entry = ticket_workflow_service.add_time_entry(
            user, ticket_id, int((body or {}).get("minutes") or 0), (body or {}).get("note") or ""
        )
        return {"ok": True, "entry": entry}
    except ValueError as e:
        _map_error(e)


@router.get("/workflow")
def dev_center_workflow(user: dict = Depends(require_roles("developpeur", "superadmin"))):
    del user
    return {
        "chain": ticket_workflow_service.WORKFLOW_CHAIN,
        "labels": ticket_workflow_service.STATUS_LABELS,
    }
