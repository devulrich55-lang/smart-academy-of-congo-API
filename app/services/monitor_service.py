"""EvoMonitor — supervision plateforme, anomalies, incidents."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.database import get_db
from app.services import email_service
from app.utils.platform_security import uid

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None

SEVERITY_ORDER = {"critical": 3, "warning": 2, "info": 1}
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
STATUS_LABELS = {
    "operational": ("🟢", "Plateforme opérationnelle"),
    "warning": ("🟡", "Avertissement"),
    "critical": ("🔴", "Panne critique"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _json_load(val, default):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (TypeError, json.JSONDecodeError):
        return default


def _disk_path() -> Path:
    if settings.use_mysql:
        return settings.upload_dir
    return settings.db_path.parent


def _collect_performance() -> dict:
    cpu = None
    ram_percent = None
    ram_used_mb = None
    ram_total_mb = None
    if psutil:
        try:
            cpu = round(psutil.cpu_percent(interval=0.15), 1)
            mem = psutil.virtual_memory()
            ram_percent = round(mem.percent, 1)
            ram_used_mb = round(mem.used / (1024 * 1024), 1)
            ram_total_mb = round(mem.total / (1024 * 1024), 1)
        except Exception:
            pass

    disk_percent = None
    disk_free_gb = None
    disk_total_gb = None
    try:
        usage = shutil.disk_usage(str(_disk_path()))
        disk_total_gb = round(usage.total / (1024**3), 2)
        disk_free_gb = round(usage.free / (1024**3), 2)
        if usage.total:
            disk_percent = round((usage.used / usage.total) * 100, 1)
    except Exception:
        pass

    load_score = None
    if cpu is not None and ram_percent is not None:
        load_score = round(min(100, (cpu + ram_percent) / 2), 1)

    return {
        "cpuPercent": cpu,
        "ramPercent": ram_percent,
        "ramUsedMb": ram_used_mb,
        "ramTotalMb": ram_total_mb,
        "diskPercent": disk_percent,
        "diskFreeGb": disk_free_gb,
        "diskTotalGb": disk_total_gb,
        "loadScore": load_score,
        "responseMs": None,
    }


def _collect_database() -> dict:
    connected = False
    query_ms = None
    errors_24h = 0
    user_count = 0
    try:
        t0 = time.perf_counter()
        db = get_db()
        db.execute("SELECT 1").fetchone()
        query_ms = round((time.perf_counter() - t0) * 1000, 1)
        connected = True
        user_count = int(db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])
    except Exception:
        connected = False

    try:
        since = _iso_hours_ago(24)
        row = get_db().execute(
            """SELECT COUNT(*) AS c FROM monitor_incidents
               WHERE service = 'database' AND severity = 'critical'
               AND created_at >= ?""",
            (since,),
        ).fetchone()
        errors_24h = int(row["c"]) if row else 0
    except Exception:
        errors_24h = 0

    backup_status = "non_configure"
    backup_label = "Sauvegarde non configurée"
    try:
        from app.services import backup_service

        st = backup_service.get_status()
        latest = st.get("latest")
        if latest:
            age_h = latest.get("ageHours", 999)
            interval = st.get("intervalHours", 6)
            if age_h <= interval + 1:
                backup_status = "ok"
                backup_label = f"Dernière : {latest.get('createdAt', '')[:16]} ({latest.get('sizeLabel', '')})"
            elif age_h <= st.get("retentionHours", 24):
                backup_status = "stale"
                backup_label = f"Sauvegarde il y a {int(age_h)} h — prochaine auto bientôt"
            else:
                backup_status = "stale"
                backup_label = f"Sauvegarde obsolète ({int(age_h)} h)"
        elif st.get("enabled"):
            backup_status = "empty"
            backup_label = "Aucune sauvegarde — création au prochain cycle"
    except Exception:
        pass

    return {
        "connected": connected,
        "queryMs": query_ms,
        "errors24h": errors_24h,
        "userCount": user_count,
        "backend": settings.database_backend,
        "backupStatus": backup_status,
        "backupLabel": backup_label,
    }


def _collect_network(db_query_ms: float | None) -> dict:
    latency_ms = db_query_ms
    internet_ok = True
    try:
        import urllib.request

        t0 = time.perf_counter()
        urllib.request.urlopen("https://www.google.com/generate_204", timeout=3)
        ext_ms = round((time.perf_counter() - t0) * 1000, 1)
        if latency_ms is None:
            latency_ms = ext_ms
    except Exception:
        internet_ok = False

    rpm = 0
    failure_rate = 0.0
    try:
        since_min = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        since_h = _iso_hours_ago(1)
        db = get_db()
        rpm_row = db.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE created_at >= ?",
            (since_min,),
        ).fetchone()
        rpm = int(rpm_row["c"]) if rpm_row else 0
        fail_row = db.execute(
            """SELECT COUNT(*) AS c FROM audit_log
               WHERE created_at >= ? AND action IN ('login_failed', 'auth_error')""",
            (since_h,),
        ).fetchone()
        total_row = db.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE created_at >= ?",
            (since_h,),
        ).fetchone()
        fails = int(fail_row["c"]) if fail_row else 0
        total = int(total_row["c"]) if total_row else 0
        if total:
            failure_rate = round((fails / total) * 100, 1)
    except Exception:
        pass

    return {
        "latencyMs": latency_ms,
        "internetAvailable": internet_ok,
        "requestsPerMinute": rpm,
        "failureRate": failure_rate,
    }


def _collect_users() -> dict:
    db = get_db()
    since_24h = _iso_hours_ago(24)
    since_90s = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()

    online = 0
    try:
        online = int(
            db.execute(
                "SELECT COUNT(*) AS c FROM online_presence WHERE updated_at >= ?",
                (since_90s,),
            ).fetchone()["c"]
        )
    except Exception:
        online = 0

    new_regs = 0
    try:
        new_regs = int(
            db.execute(
                "SELECT COUNT(*) AS c FROM users WHERE created_at >= ?",
                (since_24h,),
            ).fetchone()["c"]
        )
    except Exception:
        pass

    failed_logins = 0
    try:
        failed_logins = int(
            db.execute(
                """SELECT COUNT(*) AS c FROM audit_log
                   WHERE created_at >= ? AND action IN ('login_failed', 'illegal_access', 'account_locked')""",
                (since_24h,),
            ).fetchone()["c"]
        )
    except Exception:
        pass

    locked = 0
    try:
        locked = int(
            db.execute(
                "SELECT COUNT(*) AS c FROM users WHERE COALESCE(failed_login_attempts, 0) >= 3",
            ).fetchone()["c"]
        )
    except Exception:
        pass

    return {
        "online": online,
        "newRegistrations24h": new_regs,
        "expiredSessions": locked,
        "failedLogins24h": failed_logins,
    }


def _detect_anomalies(
    perf: dict, db: dict, net: dict, users: dict
) -> list[dict]:
    items: list[dict] = []

    def add(severity: str, service: str, title: str, message: str, actions: list[str]):
        items.append(
            {
                "severity": severity,
                "service": service,
                "title": title,
                "message": message,
                "actions": actions,
            }
        )

    ram = perf.get("ramPercent")
    if ram is not None:
        if ram >= 95:
            add(
                "critical",
                "performance",
                "Mémoire critique",
                f"Le serveur utilise {ram} % de sa mémoire.",
                ["Redémarrer les services non essentiels", "Augmenter la RAM du serveur", "Vérifier les fuites mémoire"],
            )
        elif ram >= 85:
            add(
                "warning",
                "performance",
                "Mémoire élevée",
                f"Le serveur utilise {ram} % de sa mémoire.",
                ["Surveiller l'évolution", "Planifier une montée en charge"],
            )

    disk = perf.get("diskPercent")
    if disk is not None:
        if disk >= 95:
            add(
                "critical",
                "storage",
                "Stockage saturé",
                f"Le disque est plein à {disk} %.",
                ["Libérer de l'espace", "Archiver les uploads anciens", "Augmenter le disque Render"],
            )
        elif disk >= 85:
            add(
                "warning",
                "storage",
                "Stockage presque plein",
                f"Le stockage est utilisé à {disk} %.",
                ["Nettoyer /data/uploads", "Configurer une sauvegarde externe"],
            )

    qms = db.get("queryMs")
    if qms is not None and qms > 500:
        add(
            "warning",
            "database",
            "Requêtes lentes",
            f"Temps de requête moyen : {qms} ms (seuil 500 ms).",
            ["Vérifier les index MySQL", "Analyser les requêtes lourdes", "Contrôler la charge Render"],
        )

    if not db.get("connected"):
        add(
            "critical",
            "database",
            "Base de données inaccessible",
            "La connexion à la base de données a échoué.",
            ["Vérifier DATABASE_URL", "Contrôler le service MySQL", "Consulter les logs Render"],
        )

    if db.get("backupStatus") == "stale":
        add(
            "warning",
            "database",
            "Sauvegarde obsolète",
            db.get("backupLabel") or "La dernière sauvegarde est trop ancienne.",
            ["Lancer une sauvegarde manuelle", "Configurer un cron de backup"],
        )

    if net.get("latencyMs") is not None and net["latencyMs"] > 800:
        add(
            "warning",
            "network",
            "Latence élevée",
            f"Latence mesurée : {net['latencyMs']} ms.",
            ["Vérifier la région Render", "Contrôler la connexion Internet"],
        )

    if not net.get("internetAvailable"):
        add(
            "critical",
            "network",
            "Internet indisponible",
            "Le serveur ne peut pas joindre Internet (e-mails, webhooks).",
            ["Vérifier le pare-feu", "Contrôler la configuration réseau Render"],
        )

    if net.get("failureRate", 0) >= 15:
        add(
            "warning",
            "api",
            "Taux d'échec élevé",
            f"{net['failureRate']} % des requêtes ont échoué (1 h).",
            ["Consulter les logs API", "Vérifier CORS et tokens expirés"],
        )

    if users.get("failedLogins24h", 0) >= 25:
        add(
            "warning",
            "security",
            "Tentatives de connexion suspectes",
            f"{users['failedLogins24h']} tentatives échouées en 24 h.",
            ["Activer le verrouillage de compte", "Vérifier les IP suspectes"],
        )

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        rows = get_db().execute(
            """SELECT universite, MAX(updated_at) AS last_seen
               FROM online_presence GROUP BY universite"""
        ).fetchall()
        for row in rows:
            uni = row["universite"] or ""
            last = row["last_seen"] or ""
            if uni and last and last < cutoff:
                add(
                    "warning",
                    "sync",
                    f"Campus inactif : {uni}",
                    f"Aucune activité depuis plus de 3 jours.",
                    ["Contacter l'administrateur du campus", "Vérifier la synchronisation des données"],
                )
    except Exception:
        pass

    items.sort(key=lambda x: -SEVERITY_ORDER.get(x["severity"], 0))
    return items


def _health_score(perf: dict, db: dict, net: dict, anomalies: list[dict]) -> int:
    score = 100
    if not db.get("connected"):
        score -= 40
    qms = db.get("queryMs")
    if qms and qms > 200:
        score -= min(15, int((qms - 200) / 50))
    ram = perf.get("ramPercent")
    if ram:
        if ram > 90:
            score -= 25
        elif ram > 80:
            score -= 10
    disk = perf.get("diskPercent")
    if disk:
        if disk > 90:
            score -= 20
        elif disk > 80:
            score -= 8
    if not net.get("internetAvailable"):
        score -= 15
    for a in anomalies:
        if a["severity"] == "critical":
            score -= 12
        elif a["severity"] == "warning":
            score -= 5
    return max(0, min(100, score))


def _overall_status(score: int, anomalies: list[dict]) -> str:
    if any(a["severity"] == "critical" for a in anomalies) or score < 50:
        return "critical"
    if any(a["severity"] == "warning" for a in anomalies) or score < 80:
        return "warning"
    return "operational"


def _persist_anomalies(anomalies: list[dict]) -> None:
    if not anomalies:
        return
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    db = get_db()
    for item in anomalies:
        if item["severity"] not in ("warning", "critical"):
            continue
        existing = db.execute(
            """SELECT id FROM monitor_incidents
               WHERE service = ? AND title = ? AND status = 'open' AND created_at >= ?""",
            (item["service"], item["title"], since),
        ).fetchone()
        if existing:
            continue
        iid = uid("minc")
        db.execute(
            """INSERT INTO monitor_incidents
               (id, severity, service, title, message, status, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
            (
                iid,
                item["severity"],
                item["service"],
                item["title"],
                item.get("message") or "",
                json.dumps({"actions": item.get("actions") or []}),
                _now(),
            ),
        )
    db.commit()


def _purge_resolved_incidents() -> int:
    """Supprime les incidents résolus pour éviter la surcharge."""
    if not settings.evomonitor_purge_resolved:
        return 0
    try:
        db = get_db()
        before = int(
            db.execute("SELECT COUNT(*) AS c FROM monitor_incidents").fetchone()["c"]
        )
        db.execute("DELETE FROM monitor_incidents WHERE status = 'resolved'")
        ack_cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        db.execute(
            "DELETE FROM monitor_incidents WHERE status = 'acknowledged' AND resolved_at IS NOT NULL AND resolved_at < ?",
            (ack_cutoff,),
        )
        db.commit()
        after = int(
            db.execute("SELECT COUNT(*) AS c FROM monitor_incidents").fetchone()["c"]
        )
        return max(0, before - after)
    except Exception:
        return 0


def _auto_resolve_cleared_anomalies(current_anomalies: list[dict]) -> None:
    """Clôture puis purge les pannes système devenues OK."""
    titles = {a.get("title") for a in current_anomalies if a.get("title")}
    try:
        db = get_db()
        rows = db.execute(
            """SELECT id, title, service FROM monitor_incidents
               WHERE status = 'open' AND service != 'security'"""
        ).fetchall()
        now = _now()
        for row in rows:
            if row["title"] not in titles:
                db.execute(
                    """UPDATE monitor_incidents
                       SET status = 'resolved', resolved_at = ?, resolved_by = 'system'
                       WHERE id = ?""",
                    (now, row["id"]),
                )
        db.commit()
        _purge_resolved_incidents()
    except Exception:
        pass


def _alert_recipients() -> list[str]:
    emails = list(settings.evomonitor_alert_emails)
    if emails:
        return emails
    try:
        rows = get_db().execute(
            "SELECT email FROM users WHERE role = 'superadmin' AND email IS NOT NULL"
        ).fetchall()
        blocked = {"ulrichcibamba55@gmail.com", "devulrich55@gmail.com"}
        return [
            str(r["email"]).strip().lower()
            for r in rows
            if r["email"] and str(r["email"]).strip().lower() not in blocked
        ]
    except Exception:
        return []


def _security_fingerprint(action: str, actor_email: str, resource: str, meta: dict) -> str:
    reason = str(meta.get("reason") or meta.get("path") or "")
    return f"{action}:{actor_email}:{resource}:{reason}"[:200]


def _recent_security_alert_exists(fingerprint: str, within_seconds: int = 300) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(seconds=within_seconds)).isoformat()
    rows = get_db().execute(
        """SELECT meta_json FROM monitor_incidents
           WHERE service = 'security' AND created_at >= ?""",
        (since,),
    ).fetchall()
    for row in rows:
        meta = _json_load(row["meta_json"] if "meta_json" in row.keys() else None, {})
        if meta.get("fingerprint") == fingerprint:
            return True
    return False


def _send_security_emails(title: str, message: str) -> int:
    if not email_service.smtp_configured():
        return 0
    recipients = _alert_recipients()
    if not recipients:
        return 0
    sent = 0
    body = message + "\n\n— EvoMonitor · alerte sécurité (< 30 s)"
    for addr in recipients[:5]:
        try:
            if email_service.send_platform_notification_email(
                addr,
                title,
                body,
                f"{settings.frontend_url}/evomonitor/",
            ):
                sent += 1
        except Exception:
            pass
    return sent


def on_security_event(
    request,
    action: str,
    resource: str,
    *,
    actor_email: str | None = None,
    actor_role: str | None = None,
    meta: dict | None = None,
) -> dict | None:
    """Enregistre et alerte immédiatement (< 30 s) tout accès hors procédure."""
    meta = meta or {}
    email = (actor_email or meta.get("identifier") or "inconnu").strip().lower()[:255]
    role = (actor_role or meta.get("role") or "public")[:40]
    client = request.client.host if request and request.client else ""
    path = meta.get("path") or (str(request.url.path) if request else "")
    reason = meta.get("reason") or action
    fingerprint = _security_fingerprint(action, email, resource, {**meta, "path": path})

    if _recent_security_alert_exists(fingerprint, within_seconds=300):
        return None

    title_map = {
        "login_failed": "Tentative de connexion échouée",
        "illegal_access": "Accès illégal hors procédure",
        "access_denied": "Accès refusé (403)",
        "account_locked": "Compte verrouillé — force brute",
        "rate_limited": "Attaque par déni (rate limit)",
        "auth_error": "Erreur authentification suspecte",
    }
    title = title_map.get(action, "Alerte sécurité")
    message = (
        f"Action : {action}\n"
        f"Ressource : {resource}\n"
        f"Identifiant : {email}\n"
        f"Rôle déclaré : {role}\n"
        f"Raison : {reason}\n"
        f"Chemin : {path}\n"
        f"IP : {client or '—'}"
    )

    iid = uid("minc")
    now = _now()
    get_db().execute(
        """INSERT INTO monitor_incidents
           (id, severity, service, title, message, status, meta_json, created_at)
           VALUES (?, 'critical', 'security', ?, ?, 'open', ?, ?)""",
        (
            iid,
            title,
            message,
            json.dumps(
                {
                    "action": action,
                    "resource": resource,
                    "fingerprint": fingerprint,
                    "meta": meta,
                }
            ),
            now,
        ),
    )
    get_db().commit()
    emails_sent = _send_security_emails(f"EvoMonitor — {title}", message)
    return {"id": iid, "emailsSent": emails_sent, "title": title}


def record_http_denial(request, status_code: int) -> None:
    """Middleware : accès API refusé hors procédure."""
    path = str(request.url.path)
    if path.endswith("/health") or "/health" in path:
        return
    if status_code == 401:
        return
    # Refus d'auth attendus sur l'API EvoMonitor (poll sans JWT, rôle insuffisant).
    if "/admin/monitor/" in path:
        return
    action = "access_denied"
    if status_code == 423:
        action = "account_locked"
    elif status_code == 429:
        action = "rate_limited"
    user = getattr(request.state, "user", None)
    on_security_event(
        request,
        action,
        path,
        actor_email=user.get("email") if user else None,
        actor_role=user.get("role") if user else "anonymous",
        meta={"reason": f"HTTP_{status_code}", "path": path, "status": status_code},
    )


def scan_recent_security_events(within_seconds: int | None = None) -> list[dict]:
    """Scan audit_log récent pour alertes non traitées (fenêtre < 30 s par défaut)."""
    window = within_seconds or settings.evomonitor_security_alert_seconds
    since = (datetime.now(timezone.utc) - timedelta(seconds=window + 5)).isoformat()
    rows = get_db().execute(
        """SELECT * FROM audit_log
           WHERE created_at >= ? AND action IN ({})
           ORDER BY created_at DESC LIMIT 20""".format(
            ",".join("?" for _ in SECURITY_ACTIONS)
        ),
        (since, *SECURITY_ACTIONS),
    ).fetchall()
    alerts = []
    for row in rows:
        resource = row["resource"] or "api"
        if "/admin/monitor/" in str(resource):
            continue
        meta = _json_load(row["meta"] if "meta" in row.keys() else None, {})

        class _Req:
            client = type("C", (), {"host": meta.get("ip") or ""})()
            url = type("U", (), {"path": meta.get("path") or row["resource"] or ""})()

        req = _Req()
        out = on_security_event(
            req,
            row["action"],
            row["resource"] or "api",
            actor_email=row["actor_email"],
            actor_role=row["actor_role"],
            meta=meta,
        )
        if out:
            alerts.append(out)
    return alerts


def background_tick() -> None:
    """Boucle serveur : scan sécurité + purge des incidents résolus."""
    scan_recent_security_events()
    _purge_resolved_incidents()


def security_pulse() -> dict:
    """Point de contrôle léger pour le frontend (polling ~20 s)."""
    alerts = scan_recent_security_events()
    open_security = int(
        get_db().execute(
            "SELECT COUNT(*) AS c FROM monitor_incidents WHERE service = 'security' AND status = 'open'"
        ).fetchone()["c"]
    )
    return {
        "windowSeconds": settings.evomonitor_security_alert_seconds,
        "newAlerts": len(alerts),
        "openSecurityIncidents": open_security,
        "alertRecipients": len(_alert_recipients()),
        "alerts": alerts,
        "updatedAt": _now(),
    }


def _notify_admins(anomalies: list[dict], actor: dict) -> int:
    if not email_service.smtp_configured():
        return 0
    critical = [a for a in anomalies if a["severity"] == "critical"]
    if not critical:
        return 0
    sent = 0
    recipients = _alert_recipients()
    if actor.get("email"):
        actor_mail = actor.get("email").strip().lower()
        if actor_mail and actor_mail not in recipients:
            recipients = [actor_mail] + recipients
    if not recipients:
        return 0
    lines = "\n".join(f"• {a['title']}: {a['message']}" for a in critical[:5])
    body = f"EvoMonitor a détecté {len(critical)} alerte(s) critique(s) :\n\n{lines}"
    for addr in recipients[:5]:
        try:
            if email_service.send_platform_notification_email(
                addr,
                "EvoMonitor — alerte critique",
                body,
                f"{settings.frontend_url}/evomonitor/",
            ):
                sent += 1
        except Exception:
            pass
    return sent


def get_overview(actor: dict, *, persist: bool = True, notify: bool = False) -> dict:
    purged = _purge_resolved_incidents()
    perf = _collect_performance()
    db_info = _collect_database()
    perf["responseMs"] = db_info.get("queryMs")
    net = _collect_network(db_info.get("queryMs"))
    users = _collect_users()
    anomalies = _detect_anomalies(perf, db_info, net, users)
    score = _health_score(perf, db_info, net, anomalies)
    status = _overall_status(score, anomalies)
    icon, label = STATUS_LABELS[status]

    if persist:
        _persist_anomalies(anomalies)
        _auto_resolve_cleared_anomalies(anomalies)
    scan_recent_security_events()
    emails_sent = 0
    if notify:
        emails_sent = _notify_admins(anomalies, actor)

    open_count = 0
    recent_incidents: list[dict] = []
    try:
        open_count = int(
            get_db().execute(
                "SELECT COUNT(*) AS c FROM monitor_incidents WHERE status = 'open'"
            ).fetchone()["c"]
        )
        rows = get_db().execute(
            """SELECT * FROM monitor_incidents
               WHERE status IN ('open', 'acknowledged')
               ORDER BY created_at DESC LIMIT 30"""
        ).fetchall()
        recent_incidents = [_row_to_incident(r) for r in rows]
    except Exception:
        pass

    payload = {
        "status": status,
        "statusIcon": icon,
        "statusLabel": label,
        "healthScore": score,
        "updatedAt": _now(),
        "general": {
            "status": status,
            "icon": icon,
            "label": label,
            "platform": settings.platform_name,
            "runtime": "python",
            "environment": settings.env,
        },
        "performance": perf,
        "database": db_info,
        "network": net,
        "users": users,
        "anomalies": anomalies,
        "alerts": {
            "emailConfigured": email_service.smtp_configured(),
            "emailsSent": emails_sent,
            "openIncidents": open_count,
            "openSecurityIncidents": int(
                get_db().execute(
                    "SELECT COUNT(*) AS c FROM monitor_incidents WHERE service = 'security' AND status = 'open'"
                ).fetchone()["c"]
            ),
            "alertRecipients": len(_alert_recipients()),
            "securityWindowSeconds": settings.evomonitor_security_alert_seconds,
            "purgedResolved": purged,
        },
        "incidents": {
            "open": open_count,
            "recent": recent_incidents,
        },
    }
    from app.services import monitor_sata_service

    return monitor_sata_service.enrich_overview(payload)


def _row_to_incident(row) -> dict:
    meta = _json_load(row["meta_json"] if "meta_json" in row.keys() else None, {})
    created = row["created_at"] or ""
    resolved = (row["resolved_at"] if "resolved_at" in row.keys() else None) or ""
    resolution_ms = None
    if created and resolved:
        try:
            t0 = datetime.fromisoformat(created.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
            resolution_ms = int((t1 - t0).total_seconds() * 1000)
        except (ValueError, TypeError):
            pass
    return {
        "id": row["id"],
        "severity": row["severity"] or "info",
        "service": row["service"] or "",
        "title": row["title"] or "",
        "message": row["message"] or "",
        "status": row["status"] or "open",
        "resolvedAt": resolved or None,
        "resolvedBy": (row["resolved_by"] if "resolved_by" in row.keys() else None) or None,
        "resolutionMs": resolution_ms,
        "actions": meta.get("actions") or [],
        "assignee": meta.get("assignee"),
        "createdAt": created,
    }


def list_incidents(limit: int = 50) -> list[dict]:
    lim = max(1, min(int(limit or 50), 200))
    rows = get_db().execute(
        """SELECT * FROM monitor_incidents
           WHERE status IN ('open', 'acknowledged')
           ORDER BY created_at DESC LIMIT ?""",
        (lim,),
    ).fetchall()
    return [_row_to_incident(r) for r in rows]


def resolve_incident(actor: dict, incident_id: str, status: str = "resolved") -> dict:
    iid = str(incident_id or "").strip()
    if not iid:
        raise ValueError("INVALID_INPUT")
    status = status if status in ("resolved", "acknowledged", "open") else "resolved"
    row = get_db().execute(
        "SELECT * FROM monitor_incidents WHERE id = ?", (iid,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    now = _now()
    email = (actor.get("email") or "").lower()
    resolved_at = now if status != "open" else None
    resolved_by = email if status != "open" else None
    get_db().execute(
        """UPDATE monitor_incidents
           SET status = ?, resolved_at = ?, resolved_by = ?
           WHERE id = ?""",
        (status, resolved_at, resolved_by, iid),
    )
    get_db().commit()
    if status == "resolved":
        _purge_resolved_incidents()
        row = get_db().execute("SELECT * FROM monitor_incidents WHERE id = ?", (iid,)).fetchone()
        if not row:
            return {"id": iid, "status": "resolved", "purged": True}
    row = get_db().execute("SELECT * FROM monitor_incidents WHERE id = ?", (iid,)).fetchone()
    return _row_to_incident(row)
