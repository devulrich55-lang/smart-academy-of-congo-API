import json
from datetime import datetime, timezone

from app.database import get_db
from app.utils.platform_security import hash_ip, uid

ACTION_LABELS = {
    "login": "Connexion",
    "logout": "Déconnexion",
    "delete_account": "Suppression compte campus",
    "create_institutional_admin": "Création administrateur",
    "delete_institutional_admin": "Suppression administrateur",
    "create_document": "Publication document",
    "delete_document": "Suppression document",
    "login_failed": "Échec connexion",
    "illegal_access": "Accès illégal",
    "access_denied": "Accès refusé",
    "account_locked": "Compte verrouillé",
    "rate_limited": "Limite de requêtes",
    "auth_error": "Erreur authentification",
    "sql_injection_blocked": "Injection SQL bloquée",
}

SECURITY_ACTIONS = frozenset(
    {
        "login_failed",
        "illegal_access",
        "access_denied",
        "account_locked",
        "rate_limited",
        "auth_error",
    }
)


def log_audit(request, action: str, resource: str, **kwargs) -> None:
    user = getattr(request.state, "user", None)
    now = datetime.now(timezone.utc).isoformat()
    client = request.client.host if request.client else None
    try:
        get_db().execute(
            """INSERT INTO audit_log
               (id, actor_email, actor_role, action, resource, resource_id, universite, ip_hash, meta, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                uid("aud"),
                user.get("email") if user else kwargs.get("actor_email"),
                user.get("role") if user else kwargs.get("actor_role", "public"),
                action,
                resource,
                kwargs.get("resource_id"),
                kwargs.get("universite") or (user.get("universite") if user else None),
                hash_ip(client),
                json.dumps(kwargs.get("meta") or {}),
                now,
            ),
        )
        get_db().commit()
        if action in SECURITY_ACTIONS:
            try:
                from app.services import monitor_service

                monitor_service.on_security_event(
                    request,
                    action,
                    resource,
                    actor_email=user.get("email") if user else kwargs.get("actor_email"),
                    actor_role=user.get("role") if user else kwargs.get("actor_role", "public"),
                    meta=kwargs.get("meta") or {},
                )
            except Exception as exc:
                print(f"[EvoMonitor] security alert skip: {exc}")
    except Exception as exc:
        print(f"[SAC] audit_log skip: {exc}")


def log_security_event(request, action: str, resource: str, **kwargs) -> None:
    """Journalise un accès suspect et déclenche l'alerte EvoMonitor (< 30 s)."""
    if action not in SECURITY_ACTIONS:
        action = "illegal_access"
    log_audit(request, action, resource, **kwargs)


def _row_to_activity(row) -> dict:
    meta = {}
    try:
        meta = json.loads(row["meta"] or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    action = row["action"]
    return {
        "id": row["id"],
        "action": action,
        "actionLabel": ACTION_LABELS.get(action, action.replace("_", " ").title()),
        "resource": row["resource"],
        "resourceId": row["resource_id"],
        "actorEmail": row["actor_email"],
        "actorRole": row["actor_role"],
        "universite": row["universite"],
        "meta": meta,
        "createdAt": row["created_at"],
    }


def list_activities(actor: dict, limit: int = 80) -> list[dict]:
    role = actor.get("role")
    if role not in ("superadmin", "ministere", "universite"):
        raise ValueError("FORBIDDEN")

    db = get_db()
    limit = max(1, min(int(limit or 80), 200))

    if role == "superadmin":
        rows = db.execute(
            "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    elif role == "ministere":
        rows = db.execute(
            """SELECT * FROM audit_log
               WHERE actor_role IN ('superadmin', 'ministere', 'universite')
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    else:
        campus = actor.get("universite") or actor.get("codeUni") or actor.get("sigle")
        email = actor.get("email")
        rows = db.execute(
            """SELECT * FROM audit_log
               WHERE actor_email = ? COLLATE NOCASE
                  OR (universite IS NOT NULL AND universite = ?)
               ORDER BY created_at DESC LIMIT ?""",
            (email, campus, limit),
        ).fetchall()

    return [_row_to_activity(r) for r in rows]


def activities_summary(actor: dict) -> dict:
    items = list_activities(actor, limit=200)
    by_action: dict[str, int] = {}
    by_role: dict[str, int] = {}
    for item in items:
        by_action[item["action"]] = by_action.get(item["action"], 0) + 1
        r = item.get("actorRole") or "unknown"
        by_role[r] = by_role.get(r, 0) + 1
    return {
        "total": len(items),
        "byAction": by_action,
        "byRole": by_role,
        "lastAt": items[0]["createdAt"] if items else None,
    }


def _activity_row_visible_to_actor(row, actor: dict) -> bool:
    role = actor.get("role")
    if role == "superadmin":
        return True
    if role == "ministere":
        return (row["actor_role"] or "") in ("superadmin", "ministere", "universite")
    if role == "universite":
        campus = actor.get("universite") or actor.get("codeUni") or actor.get("sigle")
        email = (actor.get("email") or "").lower()
        actor_email = (row["actor_email"] or "").lower()
        row_uni = row["universite"]
        return actor_email == email or bool(campus and row_uni and row_uni == campus)
    return False


def delete_activities(
    actor: dict, ids: list[str] | None = None, delete_all: bool = False
) -> dict:
    role = actor.get("role")
    if role not in ("superadmin", "ministere", "universite"):
        raise ValueError("FORBIDDEN")

    db = get_db()

    if delete_all:
        if role == "superadmin":
            count_row = db.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()
            deleted = int(count_row["c"] or 0) if count_row else 0
            db.execute("DELETE FROM audit_log")
        elif role == "ministere":
            count_row = db.execute(
                """SELECT COUNT(*) AS c FROM audit_log
                   WHERE actor_role IN ('superadmin', 'ministere', 'universite')"""
            ).fetchone()
            deleted = int(count_row["c"] or 0) if count_row else 0
            db.execute(
                """DELETE FROM audit_log
                   WHERE actor_role IN ('superadmin', 'ministere', 'universite')"""
            )
        else:
            campus = actor.get("universite") or actor.get("codeUni") or actor.get("sigle")
            email = actor.get("email")
            if not campus and not email:
                raise ValueError("FORBIDDEN")
            count_row = db.execute(
                """SELECT COUNT(*) AS c FROM audit_log
                   WHERE actor_email = ? COLLATE NOCASE
                      OR (universite IS NOT NULL AND universite = ?)""",
                (email, campus),
            ).fetchone()
            deleted = int(count_row["c"] or 0) if count_row else 0
            db.execute(
                """DELETE FROM audit_log
                   WHERE actor_email = ? COLLATE NOCASE
                      OR (universite IS NOT NULL AND universite = ?)""",
                (email, campus),
            )
        db.commit()
        return {"ok": True, "deleted": deleted}

    id_list = [str(item).strip() for item in (ids or []) if str(item).strip()]
    if not id_list:
        raise ValueError("INVALID_INPUT")

    deleted = 0
    for activity_id in id_list:
        row = db.execute(
            "SELECT * FROM audit_log WHERE id = ?", (activity_id,)
        ).fetchone()
        if not row or not _activity_row_visible_to_actor(row, actor):
            continue
        db.execute("DELETE FROM audit_log WHERE id = ?", (activity_id,))
        deleted += 1
    db.commit()
    return {"ok": True, "deleted": deleted}
