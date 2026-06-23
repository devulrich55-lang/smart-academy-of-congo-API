import json
import uuid
from datetime import datetime, timezone

from app.database import get_db, row_to_user
from app.services.tariff_service import find_university_by_code
from app.utils.campus_catalog import resolve_campus_id, same_campus
from app.utils.sanitize import clean_text

VALID_METHODS = {"bank_usd", "bank_cdf", "orange", "mpesa"}
VALID_STATUSES = {"pending", "confirmed", "rejected"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_payment(row) -> dict:
    return {
        "id": row["id"],
        "studentEmail": row["student_email"],
        "studentNom": row["student_nom"] or "",
        "matricule": row["matricule"] or "—",
        "universite": row["universite"],
        "feeKey": row["fee_key"],
        "feeLabel": row["fee_label"],
        "amount": float(row["amount"]),
        "currency": row["currency"] or "USD",
        "method": row["method"],
        "reference": row["reference"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "confirmedAt": row["confirmed_at"],
        "confirmedBy": row["confirmed_by"] or "",
    }


def _normalize_bank(raw: dict | None) -> dict:
    if not raw or not isinstance(raw, dict):
        raise ValueError("INVALID_BANK")
    bank_name = clean_text(raw.get("bankName"), 120)
    account_name = clean_text(raw.get("accountName"), 200)
    account_number = clean_text(raw.get("accountNumber"), 80)
    if not bank_name or not account_name or not account_number:
        raise ValueError("INVALID_BANK")
    out = {
        "bankName": bank_name,
        "accountName": account_name,
        "accountNumber": account_number,
        "currency": clean_text(raw.get("currency"), 10) or "USD",
        "note": clean_text(raw.get("note"), 300) or "",
    }
    if raw.get("accountUsd"):
        out["accountUsd"] = raw["accountUsd"]
    if raw.get("accountCdf"):
        out["accountCdf"] = raw["accountCdf"]
    if raw.get("mobileOrange"):
        out["mobileOrange"] = clean_text(raw["mobileOrange"], 40)
    if raw.get("mobileMpesa"):
        out["mobileMpesa"] = clean_text(raw["mobileMpesa"], 40)
    return out


def get_partner_bank(universite: str) -> dict | None:
    campus = resolve_campus_id(universite) or str(universite or "").strip().lower()
    if not campus:
        return None
    uni = find_university_by_code(campus)
    if not uni:
        return None
    row = get_db().execute(
        "SELECT campus_partner_bank FROM users WHERE id = ?", (uni["id"],)
    ).fetchone()
    if not row or not row["campus_partner_bank"]:
        return None
    try:
        bank = json.loads(row["campus_partner_bank"])
        return bank if isinstance(bank, dict) else None
    except json.JSONDecodeError:
        return None


def update_partner_bank(actor: dict, body: dict) -> dict:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    bank = _normalize_bank(body)
    now = _now()
    get_db().execute(
        "UPDATE users SET campus_partner_bank = ?, updated_at = ? WHERE id = ?",
        (json.dumps(bank), now, actor["id"]),
    )
    get_db().commit()
    campus = actor.get("universite") or actor.get("sigle") or actor.get("codeUni")
    return {"universite": campus, "bank": bank}


def create_academic_payment(actor: dict, body: dict) -> dict:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")
    amount = float(body.get("amount") or 0)
    if amount <= 0 or amount > 100000:
        raise ValueError("INVALID_AMOUNT")
    method = str(body.get("method") or "").strip()
    if method not in VALID_METHODS:
        raise ValueError("INVALID_METHOD")
    reference = clean_text(body.get("reference"), 120)
    if not reference or len(reference) < 4:
        raise ValueError("INVALID_REFERENCE")
    fee_key = clean_text(body.get("feeKey"), 40) or "academic"
    fee_label = clean_text(body.get("feeLabel"), 200) or "Frais académiques"
    campus = resolve_campus_id(actor.get("universite") or "") or actor.get("universite") or ""
    payment_id = clean_text(body.get("id"), 80) or f"PAY-{uuid.uuid4().hex[:12].upper()}"
    now = _now()
    student_nom = " ".join(
        filter(None, [actor.get("prenom"), actor.get("nom")])
    ).strip() or actor.get("email", "")
    db = get_db()
    db.execute(
        """INSERT INTO academic_payments (
           id, student_id, student_email, student_nom, matricule, universite,
           fee_key, fee_label, amount, currency, method, reference, status,
           created_at, confirmed_at, confirmed_by
         ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            payment_id,
            actor.get("id") or actor.get("email"),
            actor["email"],
            student_nom,
            actor.get("matricule") or "—",
            campus,
            fee_key,
            fee_label,
            round(amount, 2),
            clean_text(body.get("currency"), 10) or "USD",
            method,
            reference,
            "pending",
            now,
            None,
            "",
        ),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM academic_payments WHERE id = ?", (payment_id,)
    ).fetchone()
    return _row_to_payment(row)


def list_payments_for_student(actor: dict) -> list[dict]:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")
    rows = get_db().execute(
        """SELECT * FROM academic_payments
           WHERE student_email = ? COLLATE NOCASE
           ORDER BY created_at DESC""",
        (actor["email"],),
    ).fetchall()
    return [_row_to_payment(r) for r in rows]


def list_payments_for_campus(actor: dict) -> list[dict]:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    campus = resolve_campus_id(
        actor.get("universite") or actor.get("sigle") or actor.get("codeUni")
    )
    rows = get_db().execute(
        """SELECT * FROM academic_payments
           ORDER BY created_at DESC LIMIT 500"""
    ).fetchall()
    return [
        _row_to_payment(r)
        for r in rows
        if same_campus(campus, r["universite"])
    ]


def update_payment_status(actor: dict, payment_id: str, body: dict) -> dict:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    status = str(body.get("status") or "").strip()
    if status not in ("confirmed", "rejected"):
        raise ValueError("INVALID_STATUS")
    db = get_db()
    row = db.execute(
        "SELECT * FROM academic_payments WHERE id = ?", (payment_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    if not same_campus(actor.get("universite"), row["universite"]):
        raise ValueError("FORBIDDEN")
    now = _now()
    db.execute(
        """UPDATE academic_payments SET status = ?, confirmed_at = ?, confirmed_by = ?
           WHERE id = ?""",
        (status, now, actor.get("email") or actor.get("id"), payment_id),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM academic_payments WHERE id = ?", (payment_id,)
    ).fetchone()
    return _row_to_payment(row)
