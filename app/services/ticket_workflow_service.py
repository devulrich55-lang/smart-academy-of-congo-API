"""Workflow tickets Dev Center — statuts, commentaires, historique, temps passé."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.database import get_db
from app.utils.platform_security import uid

TICKET_STATUSES = frozenset(
    {
        "open",
        "assigned",
        "in_progress",
        "review",
        "validated",
        "production",
        "resolved",
        "closed",
    }
)

PRIORITIES = frozenset({"low", "medium", "high", "critical"})

STATUS_LABELS = {
    "open": "Nouveau",
    "assigned": "Attribué",
    "in_progress": "En cours",
    "review": "En revue",
    "validated": "Validé",
    "production": "En production",
    "resolved": "Résolu",
    "closed": "Clos",
}

WORKFLOW_CHAIN = [
    "open",
    "assigned",
    "in_progress",
    "review",
    "validated",
    "production",
    "resolved",
    "closed",
]

DEV_TRANSITIONS = {
    "open": {"assigned", "in_progress"},
    "assigned": {"in_progress"},
    "in_progress": {"review", "resolved"},
    "review": {"in_progress"},
    "validated": {"production"},
    "production": {"resolved"},
}

TECH_TRANSITIONS = {
    "open": {"assigned", "in_progress"},
    "assigned": {"in_progress", "open"},
    "in_progress": {"review", "assigned", "open"},
    "review": {"validated", "in_progress"},
    "validated": {"production", "review"},
    "production": {"resolved", "validated"},
    "resolved": {"closed", "production"},
    "closed": {"resolved"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_load(val, default):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (TypeError, json.JSONDecodeError):
        return default


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _display_name(user: dict) -> str:
    parts = [user.get("prenom"), user.get("nom")]
    name = " ".join(p for p in parts if p).strip()
    return name or _email(user) or "Utilisateur"


def normalize_priority(val: str | None, severity: str | None = None) -> str:
    p = str(val or "").strip().lower()
    if p in PRIORITIES:
        return p
    if severity == "critical":
        return "critical"
    if severity == "warning":
        return "high"
    return "medium"


def row_to_ticket(row: dict) -> dict:
    keys = row.keys() if hasattr(row, "keys") else row
    time_spent = int(row.get("time_spent_minutes") or 0) if "time_spent_minutes" in keys else 0
    return {
        "id": row["id"],
        "ticketNumber": row["ticket_number"],
        "title": row["title"],
        "description": row.get("description"),
        "severity": row.get("severity"),
        "priority": normalize_priority(row.get("priority"), row.get("severity")),
        "project": row.get("project") or row.get("service") or "platform",
        "service": row.get("service"),
        "status": row.get("status") or "open",
        "statusLabel": STATUS_LABELS.get(row.get("status") or "open", row.get("status")),
        "errorContext": _json_load(row.get("error_context_json"), {}),
        "analysis": _json_load(row.get("analysis_json"), {}),
        "correctiveCode": row.get("corrective_code"),
        "assignee": row.get("assignee"),
        "validatedBy": row.get("validated_by"),
        "validatedAt": row.get("validated_at"),
        "createdBy": row.get("created_by"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "timeSpentMinutes": time_spent,
    }


def log_history(
    ticket_id: str,
    actor: dict,
    action: str,
    from_status: str | None = None,
    to_status: str | None = None,
    meta: dict | None = None,
) -> None:
    try:
        get_db().execute(
            """INSERT INTO monitor_ticket_history
               (id, ticket_id, actor_email, actor_name, action, from_status, to_status, meta_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                uid("thist"),
                ticket_id,
                _email(actor),
                _display_name(actor),
                action,
                from_status,
                to_status,
                json.dumps(meta or {}, ensure_ascii=False),
                _now(),
            ),
        )
        get_db().commit()
    except Exception as exc:
        print(f"[Ticket workflow] history skip: {exc}")


def list_history(ticket_id: str, limit: int = 100) -> list[dict]:
    try:
        rows = get_db().execute(
            """SELECT * FROM monitor_ticket_history
               WHERE ticket_id = ? ORDER BY created_at DESC LIMIT ?""",
            (ticket_id, max(1, min(limit, 200))),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "actorEmail": r["actor_email"],
                "actorName": r["actor_name"],
                "action": r["action"],
                "fromStatus": r["from_status"],
                "toStatus": r["to_status"],
                "meta": _json_load(r["meta_json"], {}),
                "createdAt": r["created_at"],
            }
            for r in rows
        ]
    except Exception:
        return []


def add_comment(actor: dict, ticket_id: str, body: str) -> dict:
    text = str(body or "").strip()
    if not text:
        raise ValueError("INVALID_INPUT")
    row = get_db().execute(
        "SELECT id FROM monitor_dev_tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    cid = uid("tcomment")
    now = _now()
    get_db().execute(
        """INSERT INTO monitor_ticket_comments
           (id, ticket_id, author_email, author_name, body, created_at)
           VALUES (?,?,?,?,?,?)""",
        (cid, ticket_id, _email(actor), _display_name(actor), text[:4000], now),
    )
    get_db().commit()
    log_history(ticket_id, actor, "comment_added", meta={"preview": text[:120]})
    return {
        "id": cid,
        "ticketId": ticket_id,
        "authorEmail": _email(actor),
        "authorName": _display_name(actor),
        "body": text,
        "createdAt": now,
    }


def list_comments(ticket_id: str, limit: int = 100) -> list[dict]:
    try:
        rows = get_db().execute(
            """SELECT * FROM monitor_ticket_comments
               WHERE ticket_id = ? ORDER BY created_at ASC LIMIT ?""",
            (ticket_id, max(1, min(limit, 200))),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "authorEmail": r["author_email"],
                "authorName": r["author_name"],
                "body": r["body"],
                "createdAt": r["created_at"],
            }
            for r in rows
        ]
    except Exception:
        return []


def add_time_entry(actor: dict, ticket_id: str, minutes: int, note: str = "") -> dict:
    mins = int(minutes or 0)
    if mins < 1 or mins > 480:
        raise ValueError("INVALID_INPUT")
    row = get_db().execute(
        "SELECT id, time_spent_minutes FROM monitor_dev_tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    eid = uid("ttime")
    now = _now()
    db = get_db()
    db.execute(
        """INSERT INTO monitor_ticket_time_entries
           (id, ticket_id, developer_email, developer_name, minutes, note, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (eid, ticket_id, _email(actor), _display_name(actor), mins, str(note or "")[:500], now),
    )
    total = int(row["time_spent_minutes"] or 0) + mins
    db.execute(
        "UPDATE monitor_dev_tickets SET time_spent_minutes = ?, updated_at = ? WHERE id = ?",
        (total, now, ticket_id),
    )
    db.commit()
    log_history(ticket_id, actor, "time_logged", meta={"minutes": mins})
    return {"id": eid, "minutes": mins, "totalMinutes": total, "createdAt": now}


def list_time_entries(ticket_id: str, limit: int = 100) -> list[dict]:
    try:
        rows = get_db().execute(
            """SELECT * FROM monitor_ticket_time_entries
               WHERE ticket_id = ? ORDER BY created_at DESC LIMIT ?""",
            (ticket_id, max(1, min(limit, 200))),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "developerEmail": r["developer_email"],
                "developerName": r["developer_name"],
                "minutes": int(r["minutes"] or 0),
                "note": r.get("note"),
                "createdAt": r["created_at"],
            }
            for r in rows
        ]
    except Exception:
        return []


def _can_transition(role: str, from_status: str, to_status: str) -> bool:
    if role in ("superadmin", "techmanager"):
        return to_status in TICKET_STATUSES
    if role == "developpeur":
        allowed = DEV_TRANSITIONS.get(from_status, set())
        return to_status in allowed
    return False


def update_ticket_fields(
    ticket_id: str,
    actor: dict,
    patch: dict,
    *,
    allow_assign: bool = True,
    allow_priority: bool = False,
    allow_validate: bool = False,
) -> dict:
    row = get_db().execute(
        "SELECT * FROM monitor_dev_tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")

    ticket = row_to_ticket(dict(row))
    body = patch or {}
    role = actor.get("role") or ""
    updates: list[str] = []
    params: list = []
    from_status = ticket["status"]

    new_status = body.get("status")
    if new_status and new_status in TICKET_STATUSES:
        if not _can_transition(role, from_status, new_status):
            if role == "developpeur" and new_status == "review" and from_status == "in_progress":
                pass
            elif role not in ("superadmin", "techmanager"):
                raise ValueError("FORBIDDEN")
        updates.append("status = ?")
        params.append(new_status)

    if allow_assign and body.get("assignee") is not None:
        updates.append("assignee = ?")
        params.append(str(body["assignee"]).strip()[:255] or None)

    if allow_priority and body.get("priority"):
        pr = normalize_priority(body["priority"])
        updates.append("priority = ?")
        params.append(pr)

    if allow_validate and new_status == "validated":
        updates.append("validated_by = ?")
        params.append(_email(actor))
        updates.append("validated_at = ?")
        params.append(_now())

    if body.get("project"):
        updates.append("project = ?")
        params.append(str(body["project"]).strip()[:80])

    if not updates:
        return ticket

    now = _now()
    updates.append("updated_at = ?")
    params.append(now)
    params.append(ticket_id)
    get_db().execute(
        f"UPDATE monitor_dev_tickets SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    get_db().commit()

    if new_status and new_status != from_status:
        log_history(ticket_id, actor, "status_changed", from_status, new_status)
    if body.get("assignee") is not None:
        log_history(
            ticket_id,
            actor,
            "assigned",
            meta={"assignee": body.get("assignee")},
        )
    if body.get("priority"):
        log_history(ticket_id, actor, "priority_changed", meta={"priority": body["priority"]})

    fresh = get_db().execute(
        "SELECT * FROM monitor_dev_tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    return row_to_ticket(dict(fresh))


def developer_performance(email: str) -> dict:
    email = email.lower()
    try:
        assigned = get_db().execute(
            "SELECT COUNT(*) AS c FROM monitor_dev_tickets WHERE assignee = ? COLLATE NOCASE",
            (email,),
        ).fetchone()
        in_prog = get_db().execute(
            """SELECT COUNT(*) AS c FROM monitor_dev_tickets
               WHERE assignee = ? COLLATE NOCASE AND status = 'in_progress'""",
            (email,),
        ).fetchone()
        resolved = get_db().execute(
            """SELECT COUNT(*) AS c FROM monitor_dev_tickets
               WHERE assignee = ? COLLATE NOCASE AND status IN ('resolved','closed')""",
            (email,),
        ).fetchone()
        time_row = get_db().execute(
            """SELECT COALESCE(SUM(minutes),0) AS m FROM monitor_ticket_time_entries
               WHERE developer_email = ? COLLATE NOCASE""",
            (email,),
        ).fetchone()
        review = get_db().execute(
            """SELECT COUNT(*) AS c FROM monitor_dev_tickets
               WHERE assignee = ? COLLATE NOCASE AND status = 'review'""",
            (email,),
        ).fetchone()
    except Exception:
        return {
            "assignedTotal": 0,
            "inProgress": 0,
            "resolved": 0,
            "inReview": 0,
            "timeSpentMinutes": 0,
            "resolutionRate": 0,
        }

    assigned_n = int(assigned["c"] or 0)
    resolved_n = int(resolved["c"] or 0)
    rate = round((resolved_n / assigned_n) * 100) if assigned_n else 0
    return {
        "assignedTotal": assigned_n,
        "inProgress": int(in_prog["c"] or 0),
        "resolved": resolved_n,
        "inReview": int(review["c"] or 0),
        "timeSpentMinutes": int(time_row["m"] or 0),
        "resolutionRate": rate,
    }


def list_projects_for_developer(email: str) -> list[dict]:
    email = email.lower()
    try:
        rows = get_db().execute(
            """SELECT COALESCE(project, service, 'platform') AS project_name,
                      COUNT(*) AS total,
                      SUM(CASE WHEN status IN ('resolved','closed') THEN 1 ELSE 0 END) AS done,
                      SUM(CASE WHEN status IN ('in_progress','review','assigned') THEN 1 ELSE 0 END) AS active
               FROM monitor_dev_tickets
               WHERE assignee = ? COLLATE NOCASE OR (status = 'open' AND assignee IS NULL)
               GROUP BY project_name ORDER BY active DESC, total DESC""",
            (email,),
        ).fetchall()
        return [
            {
                "project": r["project_name"],
                "total": int(r["total"] or 0),
                "done": int(r["done"] or 0),
                "active": int(r["active"] or 0),
            }
            for r in rows
        ]
    except Exception:
        return []


def team_resolution_stats() -> dict:
    try:
        total = int(
            get_db().execute("SELECT COUNT(*) AS c FROM monitor_dev_tickets").fetchone()["c"] or 0
        )
        open_n = int(
            get_db().execute(
                """SELECT COUNT(*) AS c FROM monitor_dev_tickets
                   WHERE status NOT IN ('resolved','closed')"""
            ).fetchone()["c"]
            or 0
        )
        resolved = int(
            get_db().execute(
                """SELECT COUNT(*) AS c FROM monitor_dev_tickets
                   WHERE status IN ('resolved','closed')"""
            ).fetchone()["c"]
            or 0
        )
        review = int(
            get_db().execute(
                "SELECT COUNT(*) AS c FROM monitor_dev_tickets WHERE status = 'review'"
            ).fetchone()["c"]
            or 0
        )
        validated = int(
            get_db().execute(
                """SELECT COUNT(*) AS c FROM monitor_dev_tickets
                   WHERE status IN ('validated','production')"""
            ).fetchone()["c"]
            or 0
        )
        avg_time = get_db().execute(
            "SELECT AVG(time_spent_minutes) AS a FROM monitor_dev_tickets WHERE time_spent_minutes > 0"
        ).fetchone()
    except Exception:
        return {"total": 0, "open": 0, "resolved": 0, "inReview": 0, "avgTimeMinutes": 0}

    return {
        "total": total,
        "open": open_n,
        "resolved": resolved,
        "inReview": review,
        "awaitingProduction": validated,
        "avgTimeMinutes": round(float(avg_time["a"] or 0), 1) if avg_time else 0,
        "resolutionRate": round((resolved / total) * 100) if total else 0,
    }
