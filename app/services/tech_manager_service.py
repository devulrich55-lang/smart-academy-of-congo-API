"""Tech Manager — attribution, priorités, validation, performances équipe."""

from __future__ import annotations

from app.database import get_db
from app.services import ticket_workflow_service
from app.services.dev_center_service import _display_name, _email

TECH_ROLES = frozenset({"techmanager", "superadmin"})


def _assert_tech(user: dict) -> None:
    if user.get("role") not in TECH_ROLES:
        raise ValueError("FORBIDDEN")


def get_overview(user: dict) -> dict:
    _assert_tech(user)
    stats = ticket_workflow_service.team_resolution_stats()
    developers = list_team(user)
    return {
        "displayName": _display_name(user),
        "email": _email(user),
        "role": user.get("role"),
        "stats": stats,
        "teamCount": len(developers),
        "workflow": ticket_workflow_service.WORKFLOW_CHAIN,
        "statusLabels": ticket_workflow_service.STATUS_LABELS,
    }


def list_tickets(user: dict, filter_id: str = "all", limit: int = 150) -> list[dict]:
    _assert_tech(user)
    clause = "1=1"
    params: tuple = ()
    if filter_id == "review":
        clause = "status = 'review'"
    elif filter_id == "urgent":
        clause = "priority = 'critical' AND status NOT IN ('resolved','closed')"
    elif filter_id == "open":
        clause = "status IN ('open','assigned')"
    elif filter_id == "production":
        clause = "status IN ('validated','production')"
    lim = max(1, min(limit, 200))
    rows = get_db().execute(
        f"""SELECT * FROM monitor_dev_tickets WHERE {clause}
            ORDER BY
              CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
              CASE status WHEN 'review' THEN 0 WHEN 'open' THEN 1 WHEN 'assigned' THEN 2 ELSE 3 END,
              created_at DESC
            LIMIT ?""",
        params + (lim,),
    ).fetchall()
    return [ticket_workflow_service.row_to_ticket(dict(r)) for r in rows]


def assign_ticket(user: dict, ticket_id: str, assignee: str, priority: str | None = None) -> dict:
    _assert_tech(user)
    target = str(assignee or "").strip().lower()
    if not target:
        raise ValueError("INVALID_INPUT")
    patch: dict = {"assignee": target, "status": "assigned"}
    if priority:
        patch["priority"] = priority
    return ticket_workflow_service.update_ticket_fields(
        ticket_id, user, patch, allow_assign=True, allow_priority=True
    )


def set_priority(user: dict, ticket_id: str, priority: str) -> dict:
    _assert_tech(user)
    return ticket_workflow_service.update_ticket_fields(
        ticket_id,
        user,
        {"priority": priority},
        allow_priority=True,
    )


def validate_ticket(user: dict, ticket_id: str, approve: bool = True) -> dict:
    _assert_tech(user)
    row = get_db().execute(
        "SELECT status FROM monitor_dev_tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    if approve:
        if row["status"] not in ("review", "in_progress"):
            raise ValueError("INVALID_INPUT")
        return ticket_workflow_service.update_ticket_fields(
            ticket_id,
            user,
            {"status": "validated"},
            allow_validate=True,
        )
    return ticket_workflow_service.update_ticket_fields(
        ticket_id, user, {"status": "in_progress"}
    )


def approve_production(user: dict, ticket_id: str) -> dict:
    _assert_tech(user)
    return ticket_workflow_service.update_ticket_fields(
        ticket_id, user, {"status": "production"}
    )


def resolve_ticket(user: dict, ticket_id: str) -> dict:
    _assert_tech(user)
    return ticket_workflow_service.update_ticket_fields(
        ticket_id, user, {"status": "resolved"}
    )


def list_team(user: dict) -> list[dict]:
    _assert_tech(user)
    try:
        rows = get_db().execute(
            """SELECT email, prenom, nom, fonction FROM users
               WHERE role = 'developpeur' ORDER BY nom, prenom"""
        ).fetchall()
        return [
            {
                "email": r["email"],
                "displayName": _display_name(dict(r)),
                "fonction": r.get("fonction"),
                "performance": ticket_workflow_service.developer_performance(r["email"]),
            }
            for r in rows
        ]
    except Exception:
        return []


def team_stats(user: dict) -> dict:
    _assert_tech(user)
    stats = ticket_workflow_service.team_resolution_stats()
    team = list_team(user)
    return {"global": stats, "developers": team}
