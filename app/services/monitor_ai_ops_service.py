"""EvoMonitor AI Ops — analyse d'erreurs, correctifs, tickets dev, prédiction de pannes."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from app.config import settings
from app.database import get_db
from app.services import email_service, monitor_sata_service, monitor_service, ticket_workflow_service
from app.utils.platform_security import uid

TICKET_STATUSES = ticket_workflow_service.TICKET_STATUSES


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


def llm_available() -> bool:
    return bool(settings.openai_api_key and settings.evomonitor_ai_ops_use_llm)


def get_status() -> dict:
    return {
        "llmAvailable": llm_available(),
        "mode": "llm" if llm_available() else "rules",
        "model": settings.openai_model if llm_available() else None,
        "features": {
            "explainErrors": True,
            "suggestFixes": True,
            "generateCode": True,
            "devTickets": True,
            "failurePrediction": True,
        },
    }


def _call_openai(system: str, user_msg: str) -> str | None:
    if not settings.openai_api_key:
        return None
    payload = {
        "model": settings.openai_model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.openai_api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, IndexError) as exc:
        print(f"[AI Ops] OpenAI error: {exc}")
        return None


def _rules_analyze(context: dict) -> dict:
    msg = str(context.get("errorMessage") or context.get("message") or "").lower()
    code = str(context.get("errorCode") or context.get("status") or "")
    service = str(context.get("service") or "api")
    stack = str(context.get("stackTrace") or "")

    patterns = [
        (
            r"403|acc[eè]s refus|forbidden|access.denied",
            "Accès refusé (403)",
            "L'utilisateur ou le rôle n'a pas les permissions requises pour cette ressource.",
            [
                "Vérifier le rôle JWT (ministere, superadmin, etc.)",
                "Contrôler require_roles() sur la route API",
                "Reconnexion avec un compte autorisé",
            ],
            "# Python FastAPI — autoriser superadmin\n"
            '@router.post("/library/publish")\n'
            'def publish(user: dict = Depends(require_roles("ministere", "superadmin"))):\n'
            "    ...",
        ),
        (
            r"401|unauthorized|token|jwt|session",
            "Authentification invalide (401)",
            "Le jeton d'accès est absent, expiré ou invalide.",
            [
                "Se reconnecter (Ctrl+F5 puis login)",
                "Vérifier CROSS_ORIGIN_AUTH et cookies sur Render",
                "Contrôler JWT_ACCESS_SECRET identique frontend/backend",
            ],
            "// Frontend — rafraîchir la session\n"
            "await SAC_API.ensureApiSession({ soft: false });\n"
            "const data = await SAC_API.request('/platform/...');",
        ),
        (
            r"database|mysql|sqlite|connection|db|pymysql",
            "Erreur base de données",
            "La connexion DB a échoué ou une requête a provoqué une erreur SQL.",
            [
                "Tester GET /admin/monitor/heal avec action reconnect_db",
                "Vérifier DATABASE_URL / MYSQL_* sur Render",
                "Consulter les logs MySQL et l'onglet Base de données",
            ],
            "# Auto-healing EvoMonitor\n"
            "POST /api/admin/monitor/heal\n"
            '{"action": "reconnect_db"}',
        ),
        (
            r"timeout|timed out|econnrefused|network|fetch",
            "Timeout réseau / API inaccessible",
            "L'API ne répond pas dans le délai imparti ou le service est arrêté.",
            [
                "Vérifier le statut du service Render API-1",
                "Lancer Manual Deploy si cold start",
                "Tester ping_api via auto-healing",
            ],
            "POST /api/admin/monitor/heal\n" '{"action": "ping_api"}',
        ),
        (
            r"upload|media|file|pdf|couverture|multipart",
            "Échec upload fichier",
            "Le fichier n'a pas été accepté ou l'URL media n'est pas renvoyée correctement.",
            [
                "Vérifier UPLOAD_DIR persistant (/data/uploads sur Render)",
                "Contrôler la taille et le type MIME du fichier",
                "Lire fileUrl / mediaUrl dans la réponse upload",
            ],
            "// Frontend — extraire l'URL upload\n"
            "function extractUploadUrl(res) {\n"
            "  return res?.fileUrl || res?.mediaUrl || res?.url || '';\n"
            "}",
        ),
        (
            r"500|internal server|traceback|exception",
            "Erreur serveur interne (500)",
            "Une exception non gérée s'est produite côté backend.",
            [
                "Consulter les logs Render API",
                "Reproduire en mode Debug EvoMonitor",
                "Créer un ticket développeur avec la stack trace",
            ],
            "# Python — journaliser proprement\n"
            "import logging\n"
            "logger = logging.getLogger('sac')\n"
            "logger.exception('Contexte erreur: %s', detail)",
        ),
    ]

    for regex, title, explanation, fixes, code in patterns:
        if re.search(regex, msg) or re.search(regex, stack):
            return {
                    "source": "rules",
                    "title": title,
                    "explanation": explanation,
                    "rootCause": title,
                    "fixes": fixes,
                    "correctiveCode": code,
                    "confidence": 0.72,
                    "severity": context.get("severity") or "warning",
                    "service": service,
                }

    return {
        "source": "rules",
        "title": "Erreur à investiguer",
        "explanation": (
            "L'IA n'a pas reconnu un motif précis. Analysez le message, le service concerné "
            "et les logs centralisés EvoMonitor."
        ),
        "rootCause": "Cause non identifiée automatiquement",
        "fixes": [
            "Copier le message dans l'onglet Logs et filtrer par erreur",
            "Vérifier les anomalies et incidents ouverts",
            "Créer un ticket développeur avec le contexte complet",
        ],
        "correctiveCode": (
            "# Checklist diagnostic\n"
            "# 1. Reproduire l'erreur\n"
            "# 2. Lire stack trace Render\n"
            "# 3. Tester endpoint isolé avec curl\n"
            f"# Message: {context.get('errorMessage', '')[:200]}"
        ),
        "confidence": 0.45,
        "severity": context.get("severity") or "info",
        "service": service,
    }


def _llm_analyze(context: dict) -> dict | None:
    system = (
        "Tu es un ingénieur SRE/DevOps pour la plateforme Evo-smartUni (FastAPI + JS). "
        "Réponds UNIQUEMENT en JSON valide avec les clés: "
        "title, explanation, rootCause, fixes (array de strings), correctiveCode (string code), "
        "confidence (0-1), severity (info|warning|critical), service. "
        "Le correctiveCode doit être du code ou des commandes actionnables. Français."
    )
    user_msg = json.dumps(context, ensure_ascii=False)
    raw = _call_openai(system, user_msg)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        data["source"] = "llm"
        data["fixes"] = data.get("fixes") or []
        data["correctiveCode"] = data.get("correctiveCode") or ""
        data["confidence"] = float(data.get("confidence") or 0.8)
        return data
    except (TypeError, json.JSONDecodeError, ValueError):
        return None


def analyze_error(context: dict) -> dict:
    ctx = context or {}
    if llm_available():
        llm_result = _llm_analyze(ctx)
        if llm_result:
            return llm_result
    return _rules_analyze(ctx)


def _next_ticket_number() -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"DEV-{today}-"
    try:
        row = get_db().execute(
            "SELECT COUNT(*) AS c FROM monitor_dev_tickets WHERE ticket_number LIKE ?",
            (prefix + "%",),
        ).fetchone()
        seq = int(row["c"] or 0) + 1
    except Exception:
        seq = 1
    return f"{prefix}{seq:03d}"


def create_dev_ticket(actor: dict, payload: dict) -> dict:
    body = payload or {}
    title = str(body.get("title") or "Incident EvoMonitor").strip()[:200]
    if not title:
        raise ValueError("INVALID_INPUT")

    ticket_id = uid("dticket")
    ticket_number = _next_ticket_number()
    now = _now()
    analysis = body.get("analysis") or {}
    error_ctx = body.get("errorContext") or body.get("context") or {}

    row = {
        "id": ticket_id,
        "ticket_number": ticket_number,
        "title": title,
        "description": str(body.get("description") or analysis.get("explanation") or "")[:4000],
        "severity": str(body.get("severity") or analysis.get("severity") or "warning")[:20],
        "service": str(body.get("service") or analysis.get("service") or "api")[:40],
        "status": "open",
        "error_context_json": json.dumps(error_ctx, ensure_ascii=False),
        "analysis_json": json.dumps(analysis, ensure_ascii=False),
        "corrective_code": str(
            body.get("correctiveCode") or analysis.get("correctiveCode") or ""
        )[:8000],
        "assignee": str(body.get("assignee") or "").strip()[:255] or None,
        "created_by": actor.get("email") or actor.get("identifiant") or "superadmin",
        "created_at": now,
        "updated_at": now,
    }

    db = get_db()
    priority = ticket_workflow_service.normalize_priority(None, row["severity"])
    db.execute(
        """INSERT INTO monitor_dev_tickets
           (id, ticket_number, title, description, severity, service, status,
            error_context_json, analysis_json, corrective_code, assignee, created_by,
            created_at, updated_at, priority, project, time_spent_minutes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["id"],
            row["ticket_number"],
            row["title"],
            row["description"],
            row["severity"],
            row["service"],
            row["status"],
            row["error_context_json"],
            row["analysis_json"],
            row["corrective_code"],
            row["assignee"],
            row["created_by"],
            row["created_at"],
            row["updated_at"],
            priority,
            row["service"],
            0,
        ),
    )
    db.commit()

    try:
        get_db().execute(
            """INSERT INTO audit_log
               (id, actor_email, actor_role, action, resource, resource_id, universite, ip_hash, meta, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                uid("aud"),
                actor.get("email"),
                actor.get("role") or "superadmin",
                "ai_ops_ticket_created",
                "monitor_dev_ticket",
                ticket_id,
                None,
                None,
                json.dumps({"ticketNumber": ticket_number, "title": title}),
                now,
            ),
        )
        db.commit()
    except Exception as exc:
        print(f"[AI Ops] audit skip: {exc}")

    row["priority"] = priority
    row["project"] = row["service"]
    row["time_spent_minutes"] = 0
    _notify_dev_team(row)
    ticket_workflow_service.log_history(
        ticket_id, actor, "created", None, "open", {"ticketNumber": ticket_number}
    )
    return ticket_workflow_service.row_to_ticket(row)


def _notify_dev_team(ticket: dict) -> None:
    emails = settings.evomonitor_dev_ticket_emails
    if not emails or not email_service.smtp_configured():
        return
    for addr in emails:
        try:
            email_service.send_platform_notification_email(
                addr,
                f"Ticket {ticket['ticket_number']}",
                (
                    f"Ticket développeur créé par AI Ops.\n\n"
                    f"Gravité : {ticket['severity']}\n"
                    f"Service : {ticket['service']}\n\n"
                    f"{ticket['description']}\n\n"
                    f"Code correctif suggéré :\n{ticket.get('corrective_code', '')[:1200]}"
                ),
                action_url=settings.frontend_url + "/devcenter/",
            )
        except Exception as exc:
            print(f"[AI Ops] ticket email skip {addr}: {exc}")


def list_dev_tickets(limit: int = 50) -> list[dict]:
    try:
        rows = get_db().execute(
            """SELECT * FROM monitor_dev_tickets
               ORDER BY created_at DESC LIMIT ?""",
            (max(1, min(limit, 200)),),
        ).fetchall()
        return [ticket_workflow_service.row_to_ticket(dict(r)) for r in rows]
    except Exception:
        return []


def update_dev_ticket(ticket_id: str, patch: dict, actor: dict | None = None) -> dict:
    if not ticket_id:
        raise ValueError("INVALID_INPUT")
    user = actor or {"email": "system", "role": "superadmin"}
    return ticket_workflow_service.update_ticket_fields(
        ticket_id,
        user,
        patch or {},
        allow_assign=True,
        allow_priority=user.get("role") in ("techmanager", "superadmin"),
        allow_validate=user.get("role") in ("techmanager", "superadmin"),
    )


def _row_to_ticket(row: dict) -> dict:
    return ticket_workflow_service.row_to_ticket(row)


def get_predictions() -> dict:
    overview = monitor_service.get_overview(
        {"email": "ai-ops@system"}, persist=False, notify=False
    )
    preds = monitor_sata_service.predict_anomalies(overview)
    anomalies = overview.get("anomalies") or []

    for a in anomalies[:5]:
        preds.append(
            {
                "severity": a.get("severity") or "warning",
                "service": a.get("service") or "platform",
                "title": a.get("title") or "Anomalie active",
                "message": a.get("message") or "",
                "actions": a.get("actions") or [],
                "kind": "active_anomaly",
            }
        )

    summary = _build_prediction_summary(preds, overview)
    return {
        "predictions": preds,
        "count": len(preds),
        "healthScore": overview.get("healthScore"),
        "summary": summary,
        "source": "llm" if llm_available() else "rules",
    }


def _build_prediction_summary(predictions: list[dict], overview: dict) -> str:
    if not predictions:
        return "Aucune panne prévue à court terme. Tous les indicateurs sont stables."

    if llm_available() and len(predictions) >= 1:
        ctx = {
            "healthScore": overview.get("healthScore"),
            "predictions": predictions[:8],
        }
        system = (
            "Tu es un SRE. Résume en 2-3 phrases en français les risques de panne "
            "pour un super-admin. Sois concis et actionnable. JSON: {\"summary\": \"...\"}"
        )
        raw = _call_openai(system, json.dumps(ctx, ensure_ascii=False))
        if raw:
            try:
                return json.loads(raw).get("summary") or ""
            except json.JSONDecodeError:
                pass

    critical = sum(1 for p in predictions if p.get("severity") == "critical")
    warn = sum(1 for p in predictions if p.get("severity") == "warning")
    parts = []
    if critical:
        parts.append(f"{critical} risque(s) critique(s)")
    if warn:
        parts.append(f"{warn} avertissement(s)")
    titles = ", ".join(p.get("title", "") for p in predictions[:3] if p.get("title"))
    return (
        f"{' et '.join(parts) or 'Signaux faibles'} détectés. "
        f"Surveiller : {titles}. Consultez l'onglet Live et lancez l'auto-healing si nécessaire."
    )
