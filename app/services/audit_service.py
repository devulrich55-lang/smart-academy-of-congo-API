import json
from datetime import datetime, timezone

from app.database import get_db
from app.utils.platform_security import hash_ip, uid


def log_audit(request, action: str, resource: str, **kwargs) -> None:
    user = getattr(request.state, "user", None)
    now = datetime.now(timezone.utc).isoformat()
    client = request.client.host if request.client else None
    get_db().execute(
        """INSERT INTO audit_log
           (id, actor_email, actor_role, action, resource, resource_id, universite, ip_hash, meta, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            uid("aud"),
            user.get("email") if user else None,
            user.get("role") if user else "public",
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
