"""Dev Center — espace développeurs, tickets AI Ops, profils et filtres."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.database import get_db
from app.services import monitor_ai_ops_service
from app.utils.platform_security import uid

DEV_ROLES = frozenset({"developpeur", "superadmin"})
FILTERS = frozenset({"mine", "critical", "in_progress", "done", "new", "all", "urgent"})


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
    return name or _email(user) or "Développeur"


def _is_superadmin(user: dict) -> bool:
    return user.get("role") == "superadmin"


def _assert_dev_access(user: dict) -> None:
    if user.get("role") not in DEV_ROLES:
        raise ValueError("FORBIDDEN")


def _load_profile_row(email: str) -> dict | None:
    try:
        row = get_db().execute(
            "SELECT * FROM dev_center_profiles WHERE user_email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def get_profile(user: dict) -> dict:
    _assert_dev_access(user)
    email = _email(user)
    row = _load_profile_row(email)
    stats = get_stats(user)
    return {
        "email": email,
        "role": user.get("role"),
        "displayName": _display_name(user),
        "prenom": user.get("prenom"),
        "nom": user.get("nom"),
        "fonction": user.get("fonction") or "Développeur EvoSU",
        "bio": row.get("bio") if row else "",
        "speciality": row.get("speciality") if row else "",
        "stack": _json_load(row.get("stack_json") if row else None, []),
        "stats": stats,
        "isSuperAdmin": _is_superadmin(user),
    }


def update_profile(user: dict, patch: dict) -> dict:
    _assert_dev_access(user)
    if user.get("role") != "developpeur":
        raise ValueError("FORBIDDEN")
    email = _email(user)
    body = patch or {}
    bio = str(body.get("bio") or "")[:2000]
    speciality = str(body.get("speciality") or "")[:120]
    stack = body.get("stack") if isinstance(body.get("stack"), list) else []
    stack = [str(s)[:60] for s in stack[:12]]
    now = _now()
    db = get_db()
    existing = _load_profile_row(email)
    if existing:
        db.execute(
            """UPDATE dev_center_profiles
               SET bio = ?, speciality = ?, stack_json = ?, updated_at = ?
               WHERE user_email = ?""",
            (bio, speciality, json.dumps(stack, ensure_ascii=False), now, email),
        )
    else:
        db.execute(
            """INSERT INTO dev_center_profiles
               (user_email, bio, speciality, stack_json, updated_at)
               VALUES (?,?,?,?,?)""",
            (email, bio, speciality, json.dumps(stack, ensure_ascii=False), now),
        )
    db.commit()
    return get_profile(user)


def _count_where(clause: str, params: tuple) -> int:
    try:
        row = get_db().execute(
            f"SELECT COUNT(*) AS c FROM monitor_dev_tickets WHERE {clause}",
            params,
        ).fetchone()
        return int(row["c"] or 0)
    except Exception:
        return 0


def get_stats(user: dict) -> dict:
    _assert_dev_access(user)
    email = _email(user)
    if _is_superadmin(user):
        return {
            "mine": _count_where("assignee = ? COLLATE NOCASE", (email,)),
            "critical": _count_where("severity = 'critical' AND status NOT IN ('resolved','closed')", ()),
            "inProgress": _count_where("status = 'in_progress'", ()),
            "done": _count_where("status IN ('resolved','closed','validated','production')", ()),
            "new": _count_where("status = 'open' AND (assignee IS NULL OR assignee = '')", ()),
            "urgent": _count_where(
                "(severity = 'critical' OR severity = 'warning') AND status NOT IN ('resolved','closed')",
                (),
            ),
            "total": _count_where("1=1", ()),
        }
    return {
        "mine": _count_where("assignee = ? COLLATE NOCASE", (email,)),
        "critical": _count_where(
            "severity = 'critical' AND assignee = ? COLLATE NOCASE AND status NOT IN ('resolved','closed')",
            (email,),
        ),
        "inProgress": _count_where(
            "status = 'in_progress' AND assignee = ? COLLATE NOCASE", (email,)
        ),
        "done": _count_where(
            "status IN ('resolved','closed','validated','production') AND assignee = ? COLLATE NOCASE",
            (email,),
        ),
        "new": _count_where(
            "status = 'open' AND (assignee IS NULL OR assignee = '' OR assignee = ? COLLATE NOCASE)",
            (email,),
        ),
        "urgent": _count_where(
            "severity = 'critical' AND status NOT IN ('resolved','closed') AND "
            "(assignee IS NULL OR assignee = '' OR assignee = ? COLLATE NOCASE)",
            (email,),
        ),
        "total": _count_where(
            "assignee = ? COLLATE NOCASE OR (status = 'open' AND (assignee IS NULL OR assignee = ''))",
            (email,),
        ),
    }


def _filter_clause(user: dict, filter_id: str) -> tuple[str, tuple]:
    f = filter_id if filter_id in FILTERS else "mine"
    email = _email(user)
    superadmin = _is_superadmin(user)

    if f == "all" and superadmin:
        return "1=1", ()
    if f == "mine":
        return "assignee = ? COLLATE NOCASE", (email,)
    if f == "critical":
        if superadmin:
            return "severity = 'critical' AND status NOT IN ('resolved','closed')", ()
        return (
            "severity = 'critical' AND assignee = ? COLLATE NOCASE AND status NOT IN ('resolved','closed')",
            (email,),
        )
    if f == "urgent":
        if superadmin:
            return "severity = 'critical' AND status NOT IN ('resolved','closed')", ()
        return (
            "severity = 'critical' AND status NOT IN ('resolved','closed') AND "
            "(assignee IS NULL OR assignee = '' OR assignee = ? COLLATE NOCASE)",
            (email,),
        )
    if f == "in_progress":
        if superadmin:
            return "status = 'in_progress'", ()
        return "status = 'in_progress' AND assignee = ? COLLATE NOCASE", (email,)
    if f == "done":
        if superadmin:
            return "status IN ('resolved','closed')", ()
        return "status IN ('resolved','closed') AND assignee = ? COLLATE NOCASE", (email,)
    if f == "new":
        if superadmin:
            return "status = 'open' AND (assignee IS NULL OR assignee = '')", ()
        return (
            "status = 'open' AND (assignee IS NULL OR assignee = '' OR assignee = ? COLLATE NOCASE)",
            (email,),
        )
    if superadmin:
        return "1=1", ()
    return (
        "assignee = ? COLLATE NOCASE OR (status = 'open' AND (assignee IS NULL OR assignee = ''))",
        (email,),
    )


def list_tickets(user: dict, filter_id: str = "mine", limit: int = 100) -> list[dict]:
    _assert_dev_access(user)
    clause, params = _filter_clause(user, filter_id)
    lim = max(1, min(limit, 200))
    try:
        rows = get_db().execute(
            f"""SELECT * FROM monitor_dev_tickets
                WHERE {clause}
                ORDER BY
                  CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                  CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                  created_at DESC
                LIMIT ?""",
            params + (lim,),
        ).fetchall()
        return [monitor_ai_ops_service._row_to_ticket(dict(r)) for r in rows]
    except Exception:
        return []


def get_ticket(user: dict, ticket_id: str) -> dict:
    _assert_dev_access(user)
    row = get_db().execute(
        "SELECT * FROM monitor_dev_tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    ticket = monitor_ai_ops_service._row_to_ticket(dict(row))
    if not _can_view_ticket(user, ticket):
        raise ValueError("FORBIDDEN")
    return ticket


def _can_view_ticket(user: dict, ticket: dict) -> bool:
    if _is_superadmin(user):
        return True
    email = _email(user)
    assignee = str(ticket.get("assignee") or "").lower()
    status = ticket.get("status")
    if assignee == email:
        return True
    if status == "open" and not assignee:
        return True
    return False


def assign_ticket(user: dict, ticket_id: str, assignee: str | None = None) -> dict:
    _assert_dev_access(user)
    target = (assignee or _email(user)).strip().lower()
    if not target:
        raise ValueError("INVALID_INPUT")
    if not _is_superadmin(user) and target != _email(user):
        raise ValueError("FORBIDDEN")
    row = get_db().execute(
        "SELECT * FROM monitor_dev_tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    ticket = monitor_ai_ops_service._row_to_ticket(dict(row))
    if not _can_view_ticket(user, ticket):
        raise ValueError("FORBIDDEN")
    patch = {"assignee": target}
    if ticket.get("status") == "open":
        patch["status"] = "assigned" if target != _email(user) else "in_progress"
    return monitor_ai_ops_service.update_dev_ticket(ticket_id, patch, user)


def update_ticket(user: dict, ticket_id: str, patch: dict) -> dict:
    _assert_dev_access(user)
    row = get_db().execute(
        "SELECT * FROM monitor_dev_tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    ticket = monitor_ai_ops_service._row_to_ticket(dict(row))
    if not _can_view_ticket(user, ticket):
        raise ValueError("FORBIDDEN")
    body = patch or {}
    allowed: dict = {}
    if body.get("status"):
        allowed["status"] = body["status"]
    if _is_superadmin(user) and body.get("assignee") is not None:
        allowed["assignee"] = body["assignee"]
    if not allowed:
        return ticket
    return monitor_ai_ops_service.update_dev_ticket(ticket_id, allowed, user)


def list_developers(user: dict) -> list[dict]:
    _assert_dev_access(user)
    try:
        rows = get_db().execute(
            """SELECT email, prenom, nom, fonction FROM users
               WHERE role = 'developpeur' ORDER BY nom, prenom"""
        ).fetchall()
        devs = [
            {
                "email": r["email"],
                "displayName": " ".join(
                    p for p in [r.get("prenom"), r.get("nom")] if p
                ).strip()
                or r["email"],
                "fonction": r.get("fonction"),
            }
            for r in rows
        ]
    except Exception:
        devs = []
    if _is_superadmin(user):
        return devs
    email = _email(user)
    return [d for d in devs if d["email"].lower() == email] or [
        {"email": email, "displayName": _display_name(user), "fonction": user.get("fonction")}
    ]
