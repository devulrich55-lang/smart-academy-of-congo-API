"""Renouvellement annuel d'inscription — échéance 30 juillet, annonce J-10."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

from app.database import get_db
from app.services.user_service import find_user_by_id

RENEWAL_MONTH = 7
RENEWAL_DAY = 30
WARNING_DAYS = 10
CAMPUS_RENEWAL_ROLES = frozenset({"etudiant", "professeur", "assistant"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def requires_renewal(user: dict | None) -> bool:
    return bool(user and user.get("role") in CAMPUS_RENEWAL_ROLES)


def _is_onboarded(user: dict) -> bool:
    if not requires_renewal(user):
        return True
    payment = user.get("payment") if isinstance(user.get("payment"), dict) else {}
    section = user.get("sectionApproval") or payment.get("sectionApproval")
    if section == "approved":
        return True
    if payment.get("status") in ("verified", "completed") and payment.get("verifiedBy"):
        return True
    if payment.get("status") == "verified" and payment.get("method") in (
        "section_delegate",
        "section_validation",
        "superadmin_validation",
        "mobile_money",
        "orange_money",
        "mpesa",
    ):
        return True
    return False


def access_required_year(dt: date | None = None) -> str:
    dt = dt or _utc_now().date()
    y = dt.year
    if dt.month > RENEWAL_MONTH or (dt.month == RENEWAL_MONTH and dt.day > RENEWAL_DAY):
        return f"{y}-{y + 1}"
    return f"{y - 1}-{y}"


def renewal_target_year(dt: date | None = None) -> str:
    dt = dt or _utc_now().date()
    y = dt.year
    if dt.month >= RENEWAL_MONTH:
        return f"{y}-{y + 1}"
    return f"{y - 1}-{y}"


def next_renewal_deadline(dt: date | None = None) -> date:
    dt = dt or _utc_now().date()
    y = dt.year
    deadline = date(y, RENEWAL_MONTH, RENEWAL_DAY)
    if dt > deadline:
        return date(y + 1, RENEWAL_MONTH, RENEWAL_DAY)
    return deadline


def is_past_renewal_deadline(dt: date | None = None) -> bool:
    dt = dt or _utc_now().date()
    return dt.month > RENEWAL_MONTH or (dt.month == RENEWAL_MONTH and dt.day > RENEWAL_DAY)


def is_renewal_warning_period(dt: date | None = None) -> bool:
    dt = dt or _utc_now().date()
    deadline = next_renewal_deadline(dt)
    if dt > deadline:
        return False
    days_left = (deadline - dt).days
    return 0 <= days_left <= WARNING_DAYS


def _get_enrollment(user_id: str, academic_year: str) -> dict | None:
    row = get_db().execute(
        """SELECT * FROM annual_enrollments
           WHERE user_id = ? AND academic_year = ?""",
        (user_id, academic_year),
    ).fetchone()
    if not row:
        return None
    payment = None
    raw = row["payment_json"]
    if raw:
        try:
            payment = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            payment = None
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "academicYear": row["academic_year"],
        "status": row["status"],
        "payment": payment,
        "renewedAt": row["renewed_at"],
        "createdAt": row["created_at"],
    }


def _upsert_enrollment(
    user_id: str,
    academic_year: str,
    status: str,
    payment: dict | None = None,
    renewed_at: str | None = None,
) -> dict:
    db = get_db()
    existing = _get_enrollment(user_id, academic_year)
    now = _iso_now()
    pay_json = json.dumps(payment) if payment else None
    if existing:
        db.execute(
            """UPDATE annual_enrollments
               SET status = ?, payment_json = ?, renewed_at = COALESCE(?, renewed_at)
               WHERE id = ?""",
            (status, pay_json, renewed_at, existing["id"]),
        )
        db.commit()
        return _get_enrollment(user_id, academic_year) or existing

    row_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO annual_enrollments
           (id, user_id, academic_year, status, payment_json, renewed_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (row_id, user_id, academic_year, status, pay_json, renewed_at, now),
    )
    db.commit()
    return _get_enrollment(user_id, academic_year) or {
        "id": row_id,
        "userId": user_id,
        "academicYear": academic_year,
        "status": status,
        "payment": payment,
        "renewedAt": renewed_at,
        "createdAt": now,
    }


def seed_initial_enrollment(user: dict) -> None:
    """À la première inscription — rattache l'année académique en cours."""
    if not requires_renewal(user):
        return
    year = access_required_year()
    if _get_enrollment(user["id"], year):
        return
    payment = user.get("payment") if isinstance(user.get("payment"), dict) else None
    status = "pending_payment"
    renewed = None
    if payment and payment.get("status") in ("verified", "completed"):
        status = "active"
        renewed = payment.get("paidAt") or payment.get("verifiedAt") or _iso_now()
    elif payment and payment.get("status") == "pending_verification":
        status = "pending_payment"
    _upsert_enrollment(user["id"], year, status, payment, renewed)


def _bootstrap_legacy_enrollment(user: dict, academic_year: str) -> dict | None:
    """Comptes existants avant cette fonctionnalité — inscription initiale valide."""
    if not _is_onboarded(user):
        return None
    payment = user.get("payment") if isinstance(user.get("payment"), dict) else None
    if not payment:
        return None
    status = "active" if payment.get("status") in ("verified", "completed") else "pending_payment"
    renewed = payment.get("paidAt") or payment.get("verifiedAt") or user.get("createdAt")
    if status == "active":
        return _upsert_enrollment(user["id"], academic_year, status, payment, renewed)
    return _upsert_enrollment(user["id"], academic_year, status, payment, None)


def get_renewal_status(user: dict) -> dict:
    if not requires_renewal(user):
        return {"applies": False}

    now = _utc_now().date()
    access_year = access_required_year(now)
    target_year = renewal_target_year(now)
    deadline = next_renewal_deadline(now)
    days_left = max(0, (deadline - now).days)

    past_deadline = is_past_renewal_deadline(now)

    enrollment = _get_enrollment(user["id"], access_year)
    if not enrollment and _is_onboarded(user) and not past_deadline:
        enrollment = _bootstrap_legacy_enrollment(user, access_year)

    target_enrollment = _get_enrollment(user["id"], target_year)
    warning = is_renewal_warning_period(now)

    active = enrollment and enrollment.get("status") == "active"
    pending = enrollment and enrollment.get("status") == "pending_payment"
    target_active = target_enrollment and target_enrollment.get("status") == "active"
    target_pending = target_enrollment and target_enrollment.get("status") == "pending_payment"

    access_blocked = bool(_is_onboarded(user) and past_deadline and not active)

    message = ""
    if access_blocked:
        message = (
            f"Votre inscription pour l'année {access_year} n'est pas renouvelée. "
            f"Payez avant de continuer — échéance annuelle le 30 juillet."
        )
    elif warning and active and not target_active:
        message = (
            f"Renouvellement d'inscription : il reste {days_left} jour(s) "
            f"(échéance le {deadline.strftime('%d/%m/%Y')}). "
            f"Vos données sont conservées — régularisez pour l'année {target_year}."
        )
    elif pending and not past_deadline:
        message = "Paiement de renouvellement en attente de validation par l'administration."

    return {
        "applies": True,
        "accessYear": access_year,
        "renewalYear": target_year,
        "deadline": deadline.isoformat(),
        "warningActive": warning,
        "daysUntilDeadline": days_left,
        "accessBlocked": access_blocked,
        "enrollmentStatus": (enrollment or {}).get("status") or "missing",
        "renewalEnrollmentStatus": (target_enrollment or {}).get("status"),
        "pendingPayment": bool(pending or target_pending),
        "needsRenewalPayment": warning and active and not target_active and not target_pending,
        "message": message,
    }


def enrich_session(session: dict | None, user: dict | None = None) -> dict | None:
    if not session:
        return session
    if not requires_renewal(user or session):
        return session
    full_user = user or find_user_by_id(session.get("userId") or "")
    if not full_user:
        return session
    renewal = get_renewal_status(full_user)
    return {**session, "enrollmentRenewal": renewal}


def submit_renewal_payment(user: dict, payment: dict) -> dict:
    if not requires_renewal(user):
        raise ValueError("NOT_APPLICABLE")
    if not _is_onboarded(user):
        raise ValueError("NOT_ONBOARDED")

    now = _utc_now().date()
    target_year = renewal_target_year(now)
    if is_past_renewal_deadline(now):
        target_year = access_required_year(now)

    existing = _get_enrollment(user["id"], target_year)
    if existing and existing.get("status") == "active":
        raise ValueError("ALREADY_RENEWED")

    pay = dict(payment or {})
    pay["purpose"] = "enrollment_renewal"
    pay["academicYear"] = target_year
    pay["submittedAt"] = _iso_now()

    status = "pending_payment"
    renewed_at = None
    if pay.get("status") in ("completed", "verified"):
        status = "active"
        renewed_at = pay.get("paidAt") or _iso_now()
    elif pay.get("status") == "pending_verification":
        status = "pending_payment"

    row = _upsert_enrollment(user["id"], target_year, status, pay, renewed_at)
    return {"ok": True, "enrollment": row, "renewal": get_renewal_status(user)}


def verify_renewal_payment(actor: dict, enrollment_id: str) -> dict:
    role = actor.get("role")
    if role not in ("assistant", "universite", "section", "superadmin"):
        raise ValueError("FORBIDDEN")

    db = get_db()
    row = db.execute(
        "SELECT * FROM annual_enrollments WHERE id = ?", (enrollment_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")

    student = find_user_by_id(row["user_id"])
    if not student:
        raise ValueError("NOT_FOUND")

    if role in ("assistant", "section", "universite"):
        campus = actor.get("universite") or actor.get("codeUni")
        if campus and student.get("universite") and campus != student.get("universite"):
            from app.utils.campus_catalog import same_campus

            if not same_campus(campus, student.get("universite")):
                raise ValueError("FORBIDDEN")

    payment = {}
    if row["payment_json"]:
        try:
            payment = json.loads(row["payment_json"])
        except (TypeError, json.JSONDecodeError):
            payment = {}

    now = _iso_now()
    payment["status"] = "verified"
    payment["verifiedAt"] = now
    payment["verifiedBy"] = actor.get("email")

    db.execute(
        """UPDATE annual_enrollments
           SET status = 'active', payment_json = ?, renewed_at = ?
           WHERE id = ?""",
        (json.dumps(payment), now, enrollment_id),
    )
    db.commit()

    refreshed = find_user_by_id(student["id"])
    return {
        "ok": True,
        "enrollment": _get_enrollment(student["id"], row["academic_year"]),
        "renewal": get_renewal_status(refreshed or student),
    }


def list_pending_renewals(actor: dict, universite: str | None = None) -> list[dict]:
    role = actor.get("role")
    if role not in ("assistant", "universite", "section", "superadmin"):
        raise ValueError("FORBIDDEN")

    query = """SELECT e.*, u.email, u.prenom, u.nom, u.role, u.universite, u.filiere
               FROM annual_enrollments e
               JOIN users u ON u.id = e.user_id
               WHERE e.status = 'pending_payment'"""
    params: list = []
    campus = universite or actor.get("universite") or actor.get("codeUni")
    if role != "superadmin" and campus:
        query += " AND u.universite = ?"
        params.append(campus)

    rows = get_db().execute(query, tuple(params)).fetchall()
    out = []
    for row in rows:
        payment = None
        if row["payment_json"]:
            try:
                payment = json.loads(row["payment_json"])
            except (TypeError, json.JSONDecodeError):
                payment = None
        out.append(
            {
                "id": row["id"],
                "academicYear": row["academic_year"],
                "status": row["status"],
                "payment": payment,
                "createdAt": row["created_at"],
                "studentEmail": row["email"],
                "studentNom": " ".join(
                    p for p in [row["prenom"], row["nom"]] if p
                ).strip()
                or row["email"],
                "studentRole": row["role"],
                "universite": row["universite"],
                "filiere": row["filiere"],
            }
        )
    return out
