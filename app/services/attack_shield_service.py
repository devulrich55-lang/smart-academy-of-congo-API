"""Attack Shield — scoring requêtes, blocage IP, honeypot."""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.database import get_db
from app.utils.platform_security import hash_ip, uid

HONEYPOT_PATHS = frozenset(
    {
        "/api/admin/backup.zip",
        "/api/.env",
        "/api/wp-admin",
        "/api/wp-login.php",
        "/api/config.php",
        "/api/phpmyadmin",
        "/api/admin/config",
        "/.env",
        "/wp-admin",
    }
)

SUSPICIOUS_PATH_RE = re.compile(
    r"(\.\./|/etc/passwd|/proc/|\.git/|\.sql\b|backup\.zip|shell\.php|eval\(|/admin/login\b)",
    re.IGNORECASE,
)

BOT_UA_RE = re.compile(
    r"(sqlmap|nikto|nmap|masscan|curl/|wget/|python-requests|Go-http-client|scrapy|bot\b|spider|crawler)",
    re.IGNORECASE,
)

ACTION_ALLOW = "allow"
ACTION_THROTTLE = "throttle"
ACTION_BLOCK = "block"
ACTION_HONEYPOT = "honeypot"

_recent_ip_hits: dict[str, list[float]] = defaultdict(list)
_recent_ip_lock = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_load(val: Any, default: Any) -> Any:
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (TypeError, json.JSONDecodeError):
        return default


def client_ip(request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return ""


def mask_ip(ip: str) -> str:
    ip = str(ip or "").strip()
    if not ip:
        return "?"
    if ":" in ip:
        parts = ip.split(":")
        if len(parts) >= 4:
            return ":".join(parts[:3]) + ":*"
        return ip[:8] + ":*"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    return ip[:6] + "*"


def _prune_recent(now: float | None = None) -> None:
    now = now or time.time()
    cutoff = now - 120
    stale = [k for k, hits in _recent_ip_hits.items() if not hits or hits[-1] < cutoff]
    for k in stale:
        _recent_ip_hits.pop(k, None)
    for k, hits in list(_recent_ip_hits.items()):
        _recent_ip_hits[k] = [t for t in hits if t >= cutoff]


def _recent_hit_count(ip_hash: str, window_seconds: int = 60) -> int:
    now = time.time()
    _prune_recent(now)
    cutoff = now - window_seconds
    hits = _recent_ip_hits.get(ip_hash, [])
    return sum(1 for t in hits if t >= cutoff)


def _record_recent_hit(ip_hash: str) -> None:
    _recent_ip_hits[ip_hash].append(time.time())
    if len(_recent_ip_hits[ip_hash]) > 200:
        _recent_ip_hits[ip_hash] = _recent_ip_hits[ip_hash][-200:]


def is_honeypot_path(path: str) -> bool:
    p = (path or "").split("?")[0].rstrip("/") or "/"
    if p in HONEYPOT_PATHS:
        return True
    lower = p.lower()
    return any(h in lower for h in (".env", "wp-admin", "phpmyadmin", "backup.zip"))


def is_blocked(ip_hash: str) -> dict | None:
    if not ip_hash:
        return None
    row = get_db().execute(
        """SELECT * FROM blocked_ips
           WHERE ip_hash = ? AND blocked_until > ?""",
        (ip_hash, _now()),
    ).fetchone()
    return dict(row) if row else None


def score_request(request, *, body_preview: str = "") -> tuple[int, list[str], str]:
    """Retourne (score 0-100, raisons, action)."""
    if not settings.attack_shield_enabled:
        return 0, [], ACTION_ALLOW

    path = str(request.url.path or "")
    method = (request.method or "GET").upper()
    ua = (request.headers.get("user-agent") or "")[:400]
    ip = client_ip(request)
    ip_hash = hash_ip(ip) or ""
    reasons: list[str] = []
    score = 0

    if is_honeypot_path(path):
        return 95, ["honeypot_path"], ACTION_HONEYPOT

    blocked = is_blocked(ip_hash)
    if blocked:
        return 85, ["ip_blocked"], ACTION_BLOCK

    if not ua or len(ua) < 8:
        score += 12
        reasons.append("empty_user_agent")
    elif BOT_UA_RE.search(ua):
        score += 25
        reasons.append("bot_user_agent")

    if SUSPICIOUS_PATH_RE.search(path):
        score += 35
        reasons.append("suspicious_path")

    auth_header = (request.headers.get("authorization") or "").strip()
    if path.startswith("/api/admin/") and not auth_header:
        score += 18
        reasons.append("admin_without_auth")

    if method in ("DELETE", "TRACE", "CONNECT"):
        score += 15
        reasons.append(f"method_{method.lower()}")

    if body_preview and re.search(
        r"(<script|UNION\s+SELECT|DROP\s+TABLE|__proto__|;\s*--)",
        body_preview,
        re.IGNORECASE,
    ):
        score += 40
        reasons.append("malicious_payload")

    recent = _recent_hit_count(ip_hash, 60)
    if recent >= 30:
        score += 30
        reasons.append("burst_traffic")
    elif recent >= 15:
        score += 18
        reasons.append("high_frequency")

    _record_recent_hit(ip_hash)
    score = min(100, score)

    if score >= settings.attack_shield_honeypot_threshold:
        action = ACTION_HONEYPOT
    elif score >= settings.attack_shield_block_threshold:
        action = ACTION_BLOCK
    elif score >= settings.attack_shield_throttle_threshold:
        action = ACTION_THROTTLE
    else:
        action = ACTION_ALLOW

    return score, reasons, action


def log_event(
    request,
    score: int,
    reasons: list[str],
    action: str,
    *,
    body_preview: str = "",
) -> str | None:
    if not settings.attack_shield_enabled:
        return None
    if action == ACTION_ALLOW and score < 20:
        return None

    ip = client_ip(request)
    ip_hash = hash_ip(ip) or "unknown"
    path = str(request.url.path or "")
    ua = (request.headers.get("user-agent") or "")[:400]
    event_id = uid("atk")
    blocked_until = None

    if action in (ACTION_BLOCK, ACTION_HONEYPOT):
        blocked_until = (
            datetime.now(timezone.utc)
            + timedelta(minutes=settings.attack_shield_block_minutes)
        ).isoformat()
        _upsert_blocked(ip_hash, mask_ip(ip), score, ", ".join(reasons), blocked_until)

    if action == ACTION_HONEYPOT:
        _log_honeypot(ip_hash, path, ua, body_preview[:200])

    get_db().execute(
        """INSERT INTO attack_events
           (id, ip_hash, ip_masked, method, path, user_agent, score, action,
            reasons_json, blocked_until, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            ip_hash,
            mask_ip(ip),
            (request.method or "GET").upper(),
            path[:500],
            ua,
            score,
            action,
            json.dumps(reasons, ensure_ascii=False),
            blocked_until,
            _now(),
        ),
    )
    get_db().commit()
    _maybe_dispatch_alert(score, action, mask_ip(ip), path, reasons, event_id)
    return event_id


def _maybe_dispatch_alert(
    score: int,
    action: str,
    ip_masked: str,
    path: str,
    reasons: list[str],
    event_id: str,
) -> None:
    if not settings.attack_shield_alerts_enabled:
        return
    if action not in (ACTION_BLOCK, ACTION_HONEYPOT):
        return
    if score < settings.attack_shield_alert_min_score:
        return
    fingerprint = f"{action}:{ip_masked}:{path[:80]}"
    if _recent_shield_alert_exists(fingerprint, within_seconds=600):
        return

    action_label = "Blocage IP" if action == ACTION_BLOCK else "Piège honeypot"
    message = (
        f"Bouclier anti-attaque — {action_label}\n"
        f"Score : {score}/100\n"
        f"IP : {ip_masked}\n"
        f"Chemin : {path[:200]}\n"
        f"Raisons : {', '.join(reasons[:5]) or '—'}"
    )
    channels: dict[str, dict] = {}
    try:
        from app.services import monitor_sata_service

        channels["email"] = monitor_sata_service.dispatch_alert(
            {"channel": "email", "message": message, "severity": "critical"}
        )
        phone = settings.attack_shield_alert_whatsapp_phone
        if phone:
            channels["whatsapp"] = monitor_sata_service.dispatch_alert(
                {
                    "channel": "whatsapp",
                    "message": message,
                    "severity": "critical",
                    "whatsappPhone": phone,
                }
            )
    except Exception as exc:
        print(f"[AttackShield] alert dispatch skip: {exc}")

    try:
        get_db().execute(
            """INSERT INTO attack_shield_alert_log
               (id, event_id, action, score, ip_masked, path, channels_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uid("shalert"),
                event_id,
                action,
                score,
                ip_masked,
                path[:500],
                json.dumps({"fingerprint": fingerprint, "channels": channels}, ensure_ascii=False),
                _now(),
            ),
        )
        get_db().commit()
    except Exception:
        pass


def _recent_shield_alert_exists(fingerprint: str, within_seconds: int = 600) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(seconds=within_seconds)).isoformat()
    try:
        rows = get_db().execute(
            """SELECT channels_json FROM attack_shield_alert_log
               WHERE created_at >= ? ORDER BY created_at DESC LIMIT 30""",
            (since,),
        ).fetchall()
        for row in rows:
            meta = _json_load(row["channels_json"], {})
            if meta.get("fingerprint") == fingerprint:
                return True
    except Exception:
        pass
    return False


def _upsert_blocked(
    ip_hash: str,
    ip_masked: str,
    score: int,
    reason: str,
    blocked_until: str,
) -> None:
    db = get_db()
    row = db.execute(
        "SELECT hit_count FROM blocked_ips WHERE ip_hash = ?",
        (ip_hash,),
    ).fetchone()
    now = _now()
    if row:
        db.execute(
            """UPDATE blocked_ips
               SET ip_masked = ?, score = ?, reason = ?, blocked_until = ?,
                   hit_count = hit_count + 1, updated_at = ?
               WHERE ip_hash = ?""",
            (ip_masked, score, reason[:500], blocked_until, now, ip_hash),
        )
    else:
        db.execute(
            """INSERT INTO blocked_ips
               (ip_hash, ip_masked, score, reason, blocked_until, hit_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
            (ip_hash, ip_masked, score, reason[:500], blocked_until, now, now),
        )


def _log_honeypot(ip_hash: str, path: str, ua: str, snippet: str) -> None:
    get_db().execute(
        """INSERT INTO honeypot_hits (id, ip_hash, path, user_agent, payload_snippet, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (uid("hp"), ip_hash, path[:500], ua[:400], snippet[:200], _now()),
    )


def honeypot_response(path: str) -> tuple[bytes, str, int]:
    lower = path.lower()
    if ".env" in lower:
        body = b"APP_ENV=production\nDB_PASSWORD=not-a-real-password\nSECRET_KEY=redacted\n"
        return body, "text/plain", 200
    if "backup.zip" in lower:
        return b"PK\x03\x04fake-backup-not-real", "application/zip", 200
    if "wp-admin" in lower or "wp-login" in lower:
        html = b"<html><title>WordPress Admin</title><body>Login</body></html>"
        return html, "text/html", 200
    return b'{"status":"ok","admin":false}', "application/json", 200


def get_overview() -> dict:
    db = get_db()
    since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    events_24h = int(
        db.execute(
            "SELECT COUNT(*) AS c FROM attack_events WHERE created_at >= ?",
            (since_24h,),
        ).fetchone()["c"]
    )
    blocked_active = int(
        db.execute(
            "SELECT COUNT(*) AS c FROM blocked_ips WHERE blocked_until > ?",
            (_now(),),
        ).fetchone()["c"]
    )
    honeypot_24h = int(
        db.execute(
            "SELECT COUNT(*) AS c FROM honeypot_hits WHERE created_at >= ?",
            (since_24h,),
        ).fetchone()["c"]
    )
    avg_row = db.execute(
        "SELECT AVG(score) AS avg FROM attack_events WHERE created_at >= ?",
        (since_24h,),
    ).fetchone()
    avg_score = round(float(avg_row["avg"] or 0), 1)
    by_action_rows = db.execute(
        """SELECT action, COUNT(*) AS c FROM attack_events
           WHERE created_at >= ? GROUP BY action""",
        (since_24h,),
    ).fetchall()
    by_action = {r["action"]: int(r["c"]) for r in by_action_rows}
    return {
        "enabled": settings.attack_shield_enabled,
        "thresholds": {
            "throttle": settings.attack_shield_throttle_threshold,
            "block": settings.attack_shield_block_threshold,
            "honeypot": settings.attack_shield_honeypot_threshold,
        },
        "blockMinutes": settings.attack_shield_block_minutes,
        "events24h": events_24h,
        "blockedActive": blocked_active,
        "honeypot24h": honeypot_24h,
        "avgScore24h": avg_score,
        "byAction24h": by_action,
        "alertsEnabled": settings.attack_shield_alerts_enabled,
        "alertMinScore": settings.attack_shield_alert_min_score,
        "updatedAt": _now(),
    }


def get_pulse(since: str | None = None) -> dict:
    since_iso = since or (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    db = get_db()
    new_count = int(
        db.execute(
            "SELECT COUNT(*) AS c FROM attack_events WHERE created_at >= ?",
            (since_iso,),
        ).fetchone()["c"]
    )
    rows = db.execute(
        """SELECT * FROM attack_events
           WHERE created_at >= ?
           ORDER BY created_at DESC LIMIT 15""",
        (since_iso,),
    ).fetchall()
    blocked = int(
        db.execute(
            "SELECT COUNT(*) AS c FROM blocked_ips WHERE blocked_until > ?",
            (_now(),),
        ).fetchone()["c"]
    )
    return {
        "since": since_iso,
        "newEvents": new_count,
        "blockedActive": blocked,
        "events": [_row_to_event(r) for r in rows],
        "updatedAt": _now(),
    }


def get_trends(hours: int = 24) -> dict:
    hours = max(1, min(hours, 72))
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    db = get_db()

    hourly_rows = db.execute(
        """SELECT substr(created_at, 1, 13) AS hour_key, action, COUNT(*) AS c
           FROM attack_events
           WHERE created_at >= ?
           GROUP BY hour_key, action
           ORDER BY hour_key ASC""",
        (since,),
    ).fetchall()
    hourly_map: dict[str, dict] = {}
    for row in hourly_rows:
        key = row["hour_key"]
        if key not in hourly_map:
            hourly_map[key] = {"hour": key, "total": 0, "block": 0, "honeypot": 0, "throttle": 0}
        cnt = int(row["c"])
        hourly_map[key]["total"] += cnt
        action = row["action"]
        if action in hourly_map[key]:
            hourly_map[key][action] += cnt
    hourly = sorted(hourly_map.values(), key=lambda x: x["hour"])

    top_paths_rows = db.execute(
        """SELECT path, COUNT(*) AS c, MAX(score) AS max_score
           FROM attack_events WHERE created_at >= ?
           GROUP BY path ORDER BY c DESC LIMIT 8""",
        (since,),
    ).fetchall()
    top_paths = [
        {"path": r["path"], "count": int(r["c"]), "maxScore": int(r["max_score"] or 0)}
        for r in top_paths_rows
    ]

    top_ips_rows = db.execute(
        """SELECT ip_hash, ip_masked, COUNT(*) AS c, MAX(score) AS max_score
           FROM attack_events WHERE created_at >= ?
           GROUP BY ip_hash ORDER BY c DESC LIMIT 8""",
        (since,),
    ).fetchall()
    top_ips = [
        {
            "ipHash": r["ip_hash"],
            "ipMasked": r["ip_masked"],
            "count": int(r["c"]),
            "maxScore": int(r["max_score"] or 0),
        }
        for r in top_ips_rows
    ]

    alert_rows = db.execute(
        """SELECT id, action, score, ip_masked, path, created_at
           FROM attack_shield_alert_log
           WHERE created_at >= ?
           ORDER BY created_at DESC LIMIT 10""",
        (since,),
    ).fetchall()
    recent_alerts = [
        {
            "id": r["id"],
            "action": r["action"],
            "score": int(r["score"]),
            "ipMasked": r["ip_masked"],
            "path": r["path"],
            "createdAt": r["created_at"],
        }
        for r in alert_rows
    ]

    return {
        "hours": hours,
        "hourly": hourly,
        "topPaths": top_paths,
        "topIps": top_ips,
        "recentAlerts": recent_alerts,
        "updatedAt": _now(),
    }


def manual_block_ip(ip: str, reason: str = "manual_block", minutes: int | None = None) -> dict:
    ip = str(ip or "").strip()
    if not ip:
        raise ValueError("INVALID_INPUT")
    ip_hash = hash_ip(ip) or ""
    if not ip_hash:
        raise ValueError("INVALID_INPUT")
    block_min = minutes or settings.attack_shield_block_minutes
    blocked_until = (
        datetime.now(timezone.utc) + timedelta(minutes=block_min)
    ).isoformat()
    _upsert_blocked(ip_hash, mask_ip(ip), 100, reason[:500], blocked_until)
    get_db().commit()
    return {
        "ipHash": ip_hash,
        "ipMasked": mask_ip(ip),
        "blockedUntil": blocked_until,
        "reason": reason,
    }


def get_alerts_status() -> dict:
    from app.services import monitor_sata_service

    sata = monitor_sata_service.alerts_config_status()
    since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    sent_24h = 0
    try:
        sent_24h = int(
            get_db().execute(
                "SELECT COUNT(*) AS c FROM attack_shield_alert_log WHERE created_at >= ?",
                (since_24h,),
            ).fetchone()["c"]
        )
    except Exception:
        pass
    return {
        "enabled": settings.attack_shield_alerts_enabled,
        "minScore": settings.attack_shield_alert_min_score,
        "whatsappPhone": settings.attack_shield_alert_whatsapp_phone or None,
        "sent24h": sent_24h,
        "channels": sata,
        "updatedAt": _now(),
    }


def test_alert() -> dict:
    from app.services import monitor_sata_service

    message = (
        "Test Bouclier anti-attaque Tech Manager\n"
        "Canaux opérationnels — alerte simulée."
    )
    email_out = monitor_sata_service.dispatch_alert(
        {"channel": "email", "message": message, "severity": "info"}
    )
    whatsapp_out = {"ok": False, "note": "Numéro WhatsApp non configuré"}
    phone = settings.attack_shield_alert_whatsapp_phone
    if phone:
        whatsapp_out = monitor_sata_service.dispatch_alert(
            {
                "channel": "whatsapp",
                "message": message,
                "severity": "info",
                "whatsappPhone": phone,
            }
        )
    return {"ok": True, "email": email_out, "whatsapp": whatsapp_out}


def list_events(limit: int = 50) -> list[dict]:
    lim = max(1, min(limit, 200))
    rows = get_db().execute(
        """SELECT * FROM attack_events
           ORDER BY created_at DESC LIMIT ?""",
        (lim,),
    ).fetchall()
    return [_row_to_event(r) for r in rows]


def list_blocked() -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM blocked_ips
           WHERE blocked_until > ?
           ORDER BY updated_at DESC""",
        (_now(),),
    ).fetchall()
    return [_row_to_blocked(r) for r in rows]


def list_honeypot_hits(limit: int = 50) -> list[dict]:
    lim = max(1, min(limit, 200))
    rows = get_db().execute(
        """SELECT * FROM honeypot_hits ORDER BY created_at DESC LIMIT ?""",
        (lim,),
    ).fetchall()
    return [_row_to_honeypot(r) for r in rows]


def unblock_ip(ip_hash: str) -> bool:
    ip_hash = str(ip_hash or "").strip()
    if not ip_hash:
        raise ValueError("INVALID_INPUT")
    db = get_db()
    row = db.execute(
        "SELECT ip_hash FROM blocked_ips WHERE ip_hash = ?",
        (ip_hash,),
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    db.execute("DELETE FROM blocked_ips WHERE ip_hash = ?", (ip_hash,))
    db.commit()
    return True


def _row_to_event(row) -> dict:
    return {
        "id": row["id"],
        "ipHash": row["ip_hash"],
        "ipMasked": row["ip_masked"],
        "method": row["method"],
        "path": row["path"],
        "userAgent": row["user_agent"],
        "score": int(row["score"]),
        "action": row["action"],
        "reasons": _json_load(row["reasons_json"], []),
        "blockedUntil": row["blocked_until"],
        "createdAt": row["created_at"],
    }


def _row_to_blocked(row) -> dict:
    return {
        "ipHash": row["ip_hash"],
        "ipMasked": row["ip_masked"],
        "score": int(row["score"]),
        "reason": row["reason"],
        "blockedUntil": row["blocked_until"],
        "hitCount": int(row["hit_count"] or 0),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _row_to_honeypot(row) -> dict:
    return {
        "id": row["id"],
        "ipHash": row["ip_hash"],
        "path": row["path"],
        "userAgent": row["user_agent"],
        "payloadSnippet": row["payload_snippet"],
        "createdAt": row["created_at"],
    }
