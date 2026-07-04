"""EvoMonitor SATA — logs centralisés, prédiction, auto-healing, alertes multi-canaux."""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.database import get_db
from app.services import audit_service, email_service
from app.utils.platform_security import uid

_simulation: dict | None = None

LOG_CATEGORIES = {
    "login_failed": "security",
    "illegal_access": "security",
    "access_denied": "security",
    "account_locked": "security",
    "rate_limited": "security",
    "auth_error": "security",
    "sql_injection_blocked": "security",
    "create_document": "api",
    "delete_document": "api",
    "create_library": "api",
    "login": "user",
    "logout": "user",
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


def _avg(nums: list[float]) -> float:
    vals = [n for n in nums if n is not None]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def persist_metrics_snapshot(overview: dict) -> None:
    perf = overview.get("performance") or {}
    net = overview.get("network") or {}
    users = overview.get("users") or {}
    rpm = float(net.get("requestsPerMinute") or 0)
    fail = float(net.get("failureRate") or 0)
    epm = round(rpm * fail / 100, 2) if rpm else 0.0
    snap = {
        "cpu": perf.get("cpuPercent"),
        "ram": perf.get("ramPercent"),
        "rpm": rpm,
        "latencyMs": net.get("latencyMs"),
        "failureRate": fail,
        "responseMs": perf.get("responseMs"),
        "onlineUsers": users.get("online"),
        "epm": epm,
        "healthScore": overview.get("healthScore"),
    }
    try:
        db = get_db()
        db.execute(
            """INSERT INTO monitor_metrics_snapshots
               (id, cpu, ram, rpm, latency_ms, failure_rate, response_ms, online_users, epm, snapshot_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uid("msnap"),
                snap.get("cpu"),
                snap.get("ram"),
                snap.get("rpm"),
                snap.get("latencyMs"),
                snap.get("failureRate"),
                snap.get("responseMs"),
                snap.get("onlineUsers"),
                snap.get("epm"),
                json.dumps(snap),
                _now(),
            ),
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        db.execute("DELETE FROM monitor_metrics_snapshots WHERE created_at < ?", (cutoff,))
        db.commit()
    except Exception as exc:
        print(f"[SATA] metrics snapshot skip: {exc}")


def _load_recent_snapshots(limit: int = 120) -> list[dict]:
    try:
        rows = get_db().execute(
            """SELECT * FROM monitor_metrics_snapshots
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        items = []
        for row in reversed(rows):
            items.append(
                {
                    "cpu": float(row["cpu"] or 0),
                    "rpm": float(row["rpm"] or 0),
                    "latencyMs": float(row["latency_ms"] or 0),
                    "failureRate": float(row["failure_rate"] or 0),
                    "epm": float(row["epm"] or 0),
                    "createdAt": row["created_at"],
                }
            )
        return items
    except Exception:
        return []


def predict_anomalies(overview: dict) -> list[dict]:
    history = _load_recent_snapshots()
    if len(history) < 5:
        return []

    preds: list[dict] = []
    last10 = history[-10:]
    prev = history[:-10][-30:] if len(history) > 10 else history[:-10]

    rpm_now = _avg([h["rpm"] for h in last10])
    rpm_base = _avg([h["rpm"] for h in prev]) or rpm_now
    cpu_now = _avg([h["cpu"] for h in last10])
    cpu_base = _avg([h["cpu"] for h in prev]) or cpu_now
    fail_now = _avg([h["failureRate"] for h in last10])
    fail_base = _avg([h["failureRate"] for h in prev]) or fail_now

    if rpm_base > 0 and rpm_now > rpm_base * 1.4 and rpm_now > 15:
        rise = int(((rpm_now - rpm_base) / rpm_base) * 100)
        preds.append(
            {
                "severity": "critical" if rise > 80 else "warning",
                "service": "api",
                "title": "Trafic API en hausse anormale",
                "message": (
                    f"Le trafic API augmente de {rise}% depuis ~10 min "
                    f"({int(rpm_now)} req/min vs {int(rpm_base)} en moyenne). "
                    "Risque de surcharge dans ~30 min si la tendance continue."
                ),
                "actions": [
                    "Surveiller CPU et latence",
                    "Activer le cache ou la montée en charge",
                    "Consulter l'onglet Live EvoMonitor",
                ],
                "kind": "prediction",
            }
        )

    if cpu_base > 0 and cpu_now > max(75, cpu_base * 1.35):
        preds.append(
            {
                "severity": "critical" if cpu_now > 90 else "warning",
                "service": "performance",
                "title": "CPU en saturation progressive",
                "message": (
                    f"CPU moyen {int(cpu_now)}% (baseline {int(cpu_base)}%). "
                    "Panne possible sous 15–30 min."
                ),
                "actions": ["Lancer auto-healing", "Réduire la charge ou redémarrer l'API"],
                "kind": "prediction",
            }
        )

    if fail_now > max(5, fail_base * 2):
        preds.append(
            {
                "severity": "critical",
                "service": "network",
                "title": "Taux d'échec API en hausse",
                "message": (
                    f"Taux d'échec ~{fail_now:.1f}% (baseline {fail_base:.1f}%). "
                    "Vérifiez les logs centralisés."
                ),
                "actions": ["Ouvrir les logs SATA", "Tester la reconnexion DB"],
                "kind": "prediction",
            }
        )

    users = overview.get("users") or {}
    if int(users.get("failedLogins24h") or 0) >= 20:
        preds.append(
            {
                "severity": "warning",
                "service": "security",
                "title": "Brute force suspecté",
                "message": f"{users['failedLogins24h']} échecs de connexion sur 24 h.",
                "actions": ["Bloquer IP suspectes", "Renforcer rate-limit auth"],
                "kind": "security",
            }
        )

    return preds


def compute_module_scores(overview: dict) -> dict:
    perf = overview.get("performance") or {}
    db = overview.get("database") or {}
    net = overview.get("network") or {}
    users = overview.get("users") or {}
    alerts = overview.get("alerts") or {}

    def clamp(n: float) -> int:
        return max(0, min(100, int(round(n))))

    api_score = clamp(
        100
        - float(net.get("failureRate") or 0) * 2
        - max(0, float(net.get("latencyMs") or 0) - 200) / 20
        - max(0, float(perf.get("responseMs") or 0) - 300) / 15
    )
    db_score = clamp(
        100
        - (0 if db.get("connected") else 60)
        - max(0, float(db.get("queryMs") or 0) - 100) / 5
        - min(30, float(db.get("errors24h") or 0) / 2)
    )
    auth_score = clamp(100 - min(50, float(users.get("failedLogins24h") or 0) / 2))
    storage_score = clamp(
        100
        - max(0, float(perf.get("diskPercent") or 0) - 70) * 2
        - max(0, float(perf.get("ramPercent") or 0) - 85)
    )
    security_score = clamp(
        100
        - int(alerts.get("openSecurityIncidents") or 0) * 12
        - min(25, float(users.get("failedLogins24h") or 0) / 3)
    )
    modules = [
        {"id": "api", "label": "API", "score": api_score, "icon": "🌐"},
        {"id": "db", "label": "Base de données", "score": db_score, "icon": "🗄️"},
        {"id": "auth", "label": "Auth", "score": auth_score, "icon": "🔐"},
        {"id": "storage", "label": "Storage", "score": storage_score, "icon": "💾"},
        {"id": "security", "label": "Sécurité", "score": security_score, "icon": "🛡️"},
    ]
    global_score = int(round(sum(m["score"] for m in modules) / len(modules)))
    return {"global": global_score, "modules": modules}


def _classify_log(action: str, meta: dict) -> str:
    if action in audit_service.SECURITY_ACTIONS or action == "sql_injection_blocked":
        return "security"
    if action in LOG_CATEGORIES:
        return LOG_CATEGORIES[action]
    text = json.dumps(meta or {}).lower()
    if any(k in text for k in ("injection", "sql", "brute", "403", "401")):
        return "security"
    if any(k in action for k in ("error", "fail", "crash")):
        return "api"
    if any(k in action for k in ("login", "register", "student", "user")):
        return "user"
    return "server"


def _log_level(action: str) -> str:
    if action in audit_service.SECURITY_ACTIONS or action == "sql_injection_blocked":
        return "error"
    if "fail" in action or "error" in action:
        return "warn"
    return "info"


def list_logs(
  *,
  q: str | None = None,
  category: str | None = None,
  level: str | None = None,
  limit: int = 200,
) -> list[dict]:
    lim = max(1, min(int(limit or 200), 500))
    rows = get_db().execute(
        "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?",
        (lim,),
    ).fetchall()
    items = []
    needle = (q or "").strip().lower()
    cat_filter = (category or "all").lower()
    level_filter = (level or "all").lower()

    for row in rows:
        meta = _json_load(row["meta"] if "meta" in row.keys() else None, {})
        action = row["action"] or ""
        cat = _classify_log(action, meta)
        lvl = _log_level(action)
        message = audit_service.ACTION_LABELS.get(action, action.replace("_", " ").title())
        if meta.get("reason"):
            message += f" — {meta['reason']}"
        if meta.get("path"):
            message += f" ({meta['path']})"

        if cat_filter != "all" and cat != cat_filter:
            continue
        if level_filter != "all" and lvl != level_filter:
            continue
        if needle:
            hay = f"{action} {message} {json.dumps(meta)} {row['actor_email']}".lower()
            if needle not in hay:
                continue

        items.append(
            {
                "id": row["id"],
                "at": row["created_at"],
                "level": lvl,
                "category": cat,
                "action": action,
                "message": message,
                "meta": {
                    "email": row["actor_email"],
                    "role": row["actor_role"],
                    "resource": row["resource"],
                    "universite": row["universite"],
                    **meta,
                },
                "source": "audit_log",
            }
        )
    return items


def detect_repeated_errors(logs: list[dict], min_count: int = 3) -> list[dict]:
    counts: dict[str, int] = {}
    for log in logs:
        if log.get("level") not in ("error", "warn"):
            continue
        key = f"{log.get('action')}|{str(log.get('message', ''))[:80]}"
        counts[key] = counts.get(key, 0) + 1
    return [
        {"key": k, "count": c}
        for k, c in sorted(counts.items(), key=lambda x: -x[1])
        if c >= min_count
    ][:10]


def update_incident(actor: dict, incident_id: str, patch: dict) -> dict:
    from app.services import monitor_service

    iid = str(incident_id or "").strip()
    if not iid:
        raise ValueError("INVALID_INPUT")

    status = patch.get("status")
    assignee = patch.get("assignee")
    if status:
        incident = monitor_service.resolve_incident(actor, iid, status)
    else:
        row = get_db().execute(
            "SELECT * FROM monitor_incidents WHERE id = ?", (iid,)
        ).fetchone()
        if not row:
            raise ValueError("NOT_FOUND")
        incident = monitor_service._row_to_incident(row)

    if assignee:
        row = get_db().execute(
            "SELECT * FROM monitor_incidents WHERE id = ?", (iid,)
        ).fetchone()
        if row:
            meta = _json_load(row["meta_json"] if "meta_json" in row.keys() else None, {})
            meta["assignee"] = str(assignee)[:255]
            get_db().execute(
                "UPDATE monitor_incidents SET meta_json = ? WHERE id = ?",
                (json.dumps(meta), iid),
            )
            get_db().commit()
            row = get_db().execute(
                "SELECT * FROM monitor_incidents WHERE id = ?", (iid,)
            ).fetchone()
            incident = monitor_service._row_to_incident(row)
            incident["assignee"] = assignee

    return incident


def _send_telegram(message: str, chat_id: str | None = None) -> bool:
    token = settings.evomonitor_telegram_bot_token
    cid = (chat_id or settings.evomonitor_telegram_chat_id or "").strip()
    if not token or not cid:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": cid, "text": message[:4000], "parse_mode": "HTML"}
    ).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=8) as res:
            return res.status == 200
    except Exception as exc:
        print(f"[SATA] Telegram skip: {exc}")
        return False


def _send_sms_webhook(message: str, phone: str | None = None, severity: str = "info") -> bool:
    url = settings.evomonitor_sms_webhook_url
    if not url:
        return False
    payload = json.dumps(
        {
            "message": message[:500],
            "phone": (phone or "").strip(),
            "severity": severity,
            "source": "evomonitor",
        }
    ).encode()
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as res:
            return 200 <= res.status < 300
    except Exception as exc:
        print(f"[SATA] SMS webhook skip: {exc}")
        return False


def _normalize_phone_digits(phone: str | None) -> str:
    return re.sub(r"\D", "", str(phone or ""))


def _infobip_whatsapp_configured() -> bool:
    return bool(settings.infobip_api_key and settings.infobip_base_url)


def _infobip_base_url() -> str:
    base = (settings.infobip_base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.startswith("http://") or base.startswith("https://"):
        return base
    return f"https://{base}"


def _send_infobip_whatsapp(message: str, phone: str | None = None) -> bool:
    api_key = settings.infobip_api_key
    base_url = _infobip_base_url()
    to_digits = _normalize_phone_digits(phone)
    sender = _normalize_phone_digits(settings.infobip_whatsapp_from) or "447860099299"
    if not api_key or not base_url or not to_digits:
        return False
    payload = json.dumps(
        {
            "from": sender,
            "to": to_digits,
            "content": {"text": message[:4096]},
        }
    ).encode()
    url = f"{base_url}/whatsapp/1/message/text"
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"App {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as res:
            return 200 <= res.status < 300
    except Exception as exc:
        print(f"[SATA] Infobip WhatsApp skip: {exc}")
        return False


def _send_whatsapp(message: str, phone: str | None = None, severity: str = "info") -> tuple[bool, str]:
    text = message if message.startswith("EvoMonitor") else f"EvoMonitor [{severity}]: {message}"
    if _infobip_whatsapp_configured():
        return _send_infobip_whatsapp(text, phone), "infobip"
    if settings.evomonitor_whatsapp_webhook_url:
        return _send_whatsapp_webhook(text, phone=phone, severity=severity), "webhook"
    return False, "none"


def _send_whatsapp_webhook(message: str, phone: str | None = None, severity: str = "info") -> bool:
    url = settings.evomonitor_whatsapp_webhook_url
    if not url:
        return False
    payload = json.dumps(
        {
            "message": message[:500],
            "phone": (phone or "").strip(),
            "severity": severity,
            "source": "evomonitor",
            "channel": "whatsapp",
        }
    ).encode()
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as res:
            return 200 <= res.status < 300
    except Exception as exc:
        print(f"[SATA] WhatsApp webhook skip: {exc}")
        return False


def _send_alert_email(message: str, severity: str) -> dict:
    recipients = list(settings.evomonitor_alert_emails)
    if not recipients:
        rows = get_db().execute(
            "SELECT email FROM users WHERE role = 'superadmin' AND email IS NOT NULL"
        ).fetchall()
        recipients = [str(r["email"]).strip().lower() for r in rows if r["email"]]
    count = 0
    for addr in recipients[:5]:
        if email_service.send_platform_notification_email(
            addr,
            f"EvoMonitor — {severity}",
            message,
            settings.frontend_url + "/evomonitor/",
        ):
            count += 1
    return {"ok": count > 0, "emailsSent": count}


def dispatch_alert(payload: dict) -> dict:
    channel = (payload.get("channel") or "dashboard").lower()
    message = str(payload.get("message") or payload.get("title") or "Alerte EvoMonitor")
    severity = str(payload.get("severity") or "info")
    telegram_chat = str(payload.get("telegramChatId") or payload.get("chatId") or "").strip()
    sms_phone = str(payload.get("smsPhone") or payload.get("phone") or "").strip()
    whatsapp_phone = str(
        payload.get("whatsappPhone") or payload.get("smsPhone") or payload.get("phone") or ""
    ).strip()
    sent = {"channel": channel, "ok": False}

    if channel == "email":
        out = _send_alert_email(message, severity)
        sent["ok"] = out["ok"]
        sent["emailsSent"] = out.get("emailsSent", 0)
    elif channel == "telegram":
        sent["ok"] = _send_telegram(
            f"<b>EvoMonitor</b> [{severity}]\n{message}",
            chat_id=telegram_chat or None,
        )
    elif channel == "sms":
        if not sms_phone:
            sent["note"] = "Numéro SMS manquant"
        else:
            sent["ok"] = _send_sms_webhook(
                f"EvoMonitor [{severity}]: {message}",
                phone=sms_phone,
                severity=severity,
            )
    elif channel == "whatsapp":
        if not whatsapp_phone:
            sent["note"] = "Numéro WhatsApp manquant"
        else:
            ok, provider = _send_whatsapp(message, phone=whatsapp_phone, severity=severity)
            sent["ok"] = ok
            sent["provider"] = provider
            if not ok and provider == "none":
                sent["note"] = "Configurez INFOBIP_API_KEY et INFOBIP_BASE_URL sur Render"
    elif channel == "dashboard":
        sent["ok"] = True
        sent["note"] = "Notification dashboard (client)"
    elif channel == "push":
        sent["ok"] = True
        sent["note"] = "Push navigateur (client)"
    else:
        sent["note"] = f"Canal inconnu: {channel}"

    return sent


def test_alert_channels(payload: dict) -> dict:
    """Envoie un message test sur les canaux demandés (Super Admin)."""
    message = str(
        payload.get("message")
        or "Test canaux d'alerte EvoMonitor — configuration OK."
    )
    severity = str(payload.get("severity") or "warning")
    channels = payload.get("channels") or [
        "email",
        "telegram",
        "sms",
        "whatsapp",
    ]
    results = []
    for ch in channels:
        item = dispatch_alert(
            {
                "channel": str(ch).lower(),
                "message": message,
                "severity": severity if str(ch).lower() != "sms" else "critical",
                "telegramChatId": payload.get("telegramChatId"),
                "smsPhone": payload.get("smsPhone"),
                "whatsappPhone": payload.get("whatsappPhone") or payload.get("smsPhone"),
            }
        )
        results.append(item)
    ok_count = sum(1 for r in results if r.get("ok"))
    return {
        "ok": ok_count > 0,
        "tested": len(results),
        "succeeded": ok_count,
        "results": results,
    }


def trigger_heal(action: str) -> dict:
    act = (action or "ping_api").strip().lower()

    if act == "ping_api":
        t0 = time.perf_counter()
        get_db().execute("SELECT 1").fetchone()
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return {"ok": True, "action": act, "message": f"API et DB OK ({ms} ms)."}

    if act == "reconnect_db":
        db = get_db()
        db.execute("SELECT 1").fetchone()
        db.commit()
        return {"ok": True, "action": act, "message": "Connexion base de données vérifiée."}

    if act == "warm_cache":
        get_db().execute("SELECT COUNT(*) FROM users").fetchone()
        return {"ok": True, "action": act, "message": "Warm-up base effectué."}

    if act == "clear_local_cache":
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        db = get_db()
        db.execute("DELETE FROM monitor_metrics_snapshots WHERE created_at < ?", (cutoff,))
        db.commit()
        return {"ok": True, "action": act, "message": "Snapshots métriques > 24 h supprimés."}

    if act == "restart_api":
        return {
            "ok": False,
            "action": act,
            "message": "Redémarrage automatique indisponible sur Render — utilisez Manual Deploy ou redémarrez le service.",
        }

    raise ValueError("INVALID_INPUT")


def start_simulation(scenario: str) -> dict:
    global _simulation
    allowed = {"traffic", "cpu", "api_crash"}
    sc = (scenario or "traffic").strip().lower()
    if sc not in allowed:
        raise ValueError("INVALID_INPUT")
    _simulation = {"scenario": sc, "startedAt": _now()}
    return {"ok": True, "simulation": _simulation}


def stop_simulation() -> dict:
    global _simulation
    _simulation = None
    return {"ok": True, "simulation": None}


def apply_simulation(overview: dict) -> dict:
    if not _simulation:
        return overview
    out = json.loads(json.dumps(overview))
    sc = _simulation["scenario"]
    perf = out.setdefault("performance", {})
    net = out.setdefault("network", {})
    if sc == "traffic":
        net["requestsPerMinute"] = int(net.get("requestsPerMinute") or 0) + 80
        net["failureRate"] = float(net.get("failureRate") or 0) + 5
    elif sc == "cpu":
        perf["cpuPercent"] = min(99, float(perf.get("cpuPercent") or 0) + 35)
    elif sc == "api_crash":
        net["failureRate"] = float(net.get("failureRate") or 0) + 25
        out["status"] = "critical"
        out["statusLabel"] = "🧪 Simulation crash API"
    out.setdefault("anomalies", []).append(
        {
            "severity": "warning",
            "service": "simulation",
            "title": "Mode test SATA actif",
            "message": f"Scénario « {sc} » — aucun impact production.",
            "actions": ["Arrêter la simulation"],
            "kind": "simulation",
        }
    )
    return out


def enrich_overview(overview: dict) -> dict:
    overview = apply_simulation(overview)
    persist_metrics_snapshot(overview)
    predictions = predict_anomalies(overview)
    module_scores = compute_module_scores(overview)
    overview["anomalies"] = (overview.get("anomalies") or []) + predictions
    overview["moduleScores"] = module_scores
    overview["healthScore"] = module_scores["global"]
    net = overview.get("network") or {}
    rpm = float(net.get("requestsPerMinute") or 0)
    fail = float(net.get("failureRate") or 0)
    overview["epm"] = round(rpm * fail / 100, 2) if rpm else 0
    overview["simulationActive"] = _simulation is not None
    return overview


def log_sql_injection_attempt(request, snippet: str) -> None:
    audit_service.log_audit(
        request,
        "sql_injection_blocked",
        str(request.url.path),
        meta={"snippet": snippet[:120], "ip": request.client.host if request.client else ""},
    )
