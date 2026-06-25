import json
import re
import uuid
from datetime import datetime, timezone

from app.database import get_db
from app.services.tariff_service import find_university_by_code
from app.utils.campus_catalog import resolve_campus_id, same_campus
from app.utils.sanitize import clean_text

VALID_METHODS = {"bank_usd", "bank_cdf", "orange", "mpesa"}
VALID_STATUSES = {"pending", "confirmed", "rejected"}
APPROVAL_SLOTS = ("recteur", "daf", "scolarite")
MAX_BANK_CHANGES = 1

SLOT_ROLE_MAP = {
    "recteur": {"universite"},
    "daf": {"universite", "professeur"},
    "scolarite": {"section"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def mask_account_number(account_number: str) -> str:
    digits = _digits_only(account_number)
    if not digits:
        return "—"
    if len(digits) <= 8:
        tail = digits[-min(4, len(digits)) :]
        return ("X" * max(0, len(digits) - len(tail))) + tail
    show_start = 2
    show_end = 5
    middle = len(digits) - show_start - show_end
    return digits[:show_start] + ("X" * middle) + digits[-show_end:]


def mask_bank_for_student(bank: dict | None) -> dict | None:
    if not bank or not isinstance(bank, dict):
        return bank
    out = dict(bank)
    raw = out.get("accountNumber") or ""
    out["accountNumberMasked"] = mask_account_number(raw)
    out.pop("accountNumber", None)
    if out.get("accountUsd") and isinstance(out["accountUsd"], dict):
        usd = dict(out["accountUsd"])
        if usd.get("accountRaw"):
            usd["accountDisplay"] = mask_account_number(usd["accountRaw"])
            usd.pop("accountRaw", None)
        out["accountUsd"] = usd
    if out.get("accountCdf") and isinstance(out["accountCdf"], dict):
        cdf = dict(out["accountCdf"])
        if cdf.get("accountRaw"):
            cdf["accountDisplay"] = mask_account_number(cdf["accountRaw"])
            cdf.pop("accountRaw", None)
        out["accountCdf"] = cdf
    if out.get("mobileOrange"):
        out["mobileOrangeMasked"] = mask_account_number(out["mobileOrange"])
        out.pop("mobileOrange", None)
    if out.get("mobileMpesa"):
        out["mobileMpesaMasked"] = mask_account_number(out["mobileMpesa"])
        out.pop("mobileMpesa", None)
    out.pop("pendingChange", None)
    out.pop("changeHistory", None)
    return out


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
    digits = _digits_only(account_number)
    if len(digits) < 8:
        raise ValueError("INVALID_ACCOUNT_NUMBER")
    out = {
        "bankName": bank_name,
        "accountName": account_name,
        "accountNumber": digits,
        "accountNumberMasked": mask_account_number(digits),
        "currency": clean_text(raw.get("currency"), 10) or "USD",
        "note": clean_text(raw.get("note"), 300) or "",
        "locked": True,
        "registeredAt": _now(),
        "changeCount": 0,
        "maxChanges": MAX_BANK_CHANGES,
        "pendingChange": None,
        "changeHistory": [],
    }
    if raw.get("accountUsd"):
        out["accountUsd"] = raw["accountUsd"]
    if raw.get("accountCdf"):
        out["accountCdf"] = raw["accountCdf"]
    if raw.get("mobileOrange"):
        out["mobileOrange"] = clean_text(raw.get("mobileOrange"), 40)
        out["mobileOrangeMasked"] = mask_account_number(out["mobileOrange"])
    if raw.get("mobileMpesa"):
        out["mobileMpesa"] = clean_text(raw.get("mobileMpesa"), 40)
        out["mobileMpesaMasked"] = mask_account_number(out["mobileMpesa"])
    return out


def _load_bank(uni_id: str) -> dict | None:
    row = get_db().execute(
        "SELECT campus_partner_bank FROM users WHERE id = ?", (uni_id,)
    ).fetchone()
    if not row or not row["campus_partner_bank"]:
        return None
    try:
        bank = json.loads(row["campus_partner_bank"])
        return bank if isinstance(bank, dict) else None
    except json.JSONDecodeError:
        return None


def _save_bank(uni_id: str, bank: dict) -> None:
    now = _now()
    get_db().execute(
        "UPDATE users SET campus_partner_bank = ?, updated_at = ? WHERE id = ?",
        (json.dumps(bank), now, uni_id),
    )
    get_db().commit()


def get_partner_bank(universite: str) -> dict | None:
    campus = resolve_campus_id(universite) or str(universite or "").strip().lower()
    if not campus:
        return None
    uni = find_university_by_code(campus)
    if not uni:
        return None
    return _load_bank(uni["id"])


def _actor_campus(actor: dict) -> str:
    return resolve_campus_id(
        actor.get("universite") or actor.get("sigle") or actor.get("codeUni")
    ) or ""


def update_partner_bank(actor: dict, body: dict) -> dict:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    existing = _load_bank(actor["id"])
    if existing and (existing.get("locked") or existing.get("accountNumber")):
        raise ValueError("BANK_LOCKED")
    bank = _normalize_bank(body)
    _save_bank(actor["id"], bank)
    campus = actor.get("universite") or actor.get("sigle") or actor.get("codeUni")
    return {"universite": campus, "bank": bank}


def request_bank_change(actor: dict, body: dict) -> dict:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    existing = _load_bank(actor["id"])
    if not existing:
        raise ValueError("BANK_NOT_CONFIGURED")
    if existing.get("pendingChange"):
        raise ValueError("CHANGE_ALREADY_PENDING")
    change_count = int(existing.get("changeCount") or 0)
    if change_count >= int(existing.get("maxChanges") or MAX_BANK_CHANGES):
        raise ValueError("CHANGE_LIMIT_REACHED")
    new_bank = _normalize_bank(body)
    reason = clean_text(body.get("reason"), 400) or ""
    if not reason or len(reason) < 10:
        raise ValueError("CHANGE_REASON_REQUIRED")
    existing["pendingChange"] = {
        "newBank": new_bank,
        "reason": reason,
        "requestedBy": actor.get("email") or actor.get("id"),
        "requestedAt": _now(),
        "approvals": {slot: None for slot in APPROVAL_SLOTS},
    }
    _save_bank(actor["id"], existing)
    campus = actor.get("universite") or actor.get("sigle") or actor.get("codeUni")
    return {"universite": campus, "bank": existing, "pendingChange": existing["pendingChange"]}


def approve_bank_change(actor: dict, body: dict) -> dict:
    slot = str(body.get("slot") or "").strip().lower()
    if slot not in APPROVAL_SLOTS:
        raise ValueError("INVALID_APPROVAL_SLOT")
    allowed_roles = SLOT_ROLE_MAP.get(slot, set())
    if actor.get("role") not in allowed_roles:
        raise ValueError("FORBIDDEN_APPROVAL_ROLE")
    if not same_campus(_actor_campus(actor), _actor_campus(actor)):
        pass
    uni_row = None
    if actor.get("role") == "universite":
        uni_row = actor
    else:
        campus = _actor_campus(actor)
        uni_row = find_university_by_code(campus)
    if not uni_row:
        raise ValueError("UNIVERSITY_NOT_FOUND")
    if not same_campus(_actor_campus(actor), _actor_campus(uni_row)):
        raise ValueError("FORBIDDEN")
    bank = _load_bank(uni_row["id"])
    if not bank or not bank.get("pendingChange"):
        raise ValueError("NO_PENDING_CHANGE")
    pending = bank["pendingChange"]
    requester = pending.get("requestedBy") or ""
    actor_email = (actor.get("email") or actor.get("id") or "").lower()
    if actor_email and requester.lower() == actor_email:
        raise ValueError("REQUESTER_CANNOT_APPROVE")
    approvals = pending.get("approvals") or {}
    for other_slot, entry in approvals.items():
        if entry and (entry.get("email") or "").lower() == actor_email:
            raise ValueError("ALREADY_APPROVED")
    if approvals.get(slot):
        raise ValueError("SLOT_ALREADY_APPROVED")
    approvals[slot] = {
        "email": actor.get("email") or actor.get("id"),
        "role": actor.get("role"),
        "at": _now(),
    }
    pending["approvals"] = approvals
    all_done = all(approvals.get(s) for s in APPROVAL_SLOTS)
    if all_done:
        old_masked = bank.get("accountNumberMasked") or mask_account_number(
            bank.get("accountNumber") or ""
        )
        new_bank = pending["newBank"]
        history = list(bank.get("changeHistory") or [])
        history.append(
            {
                "fromMasked": old_masked,
                "toMasked": new_bank.get("accountNumberMasked"),
                "reason": pending.get("reason"),
                "requestedBy": pending.get("requestedBy"),
                "approvedAt": _now(),
                "approvals": approvals,
            }
        )
        new_bank["locked"] = True
        new_bank["registeredAt"] = bank.get("registeredAt") or _now()
        new_bank["changeCount"] = int(bank.get("changeCount") or 0) + 1
        new_bank["maxChanges"] = int(bank.get("maxChanges") or MAX_BANK_CHANGES)
        new_bank["pendingChange"] = None
        new_bank["changeHistory"] = history
        bank = new_bank
    else:
        bank["pendingChange"] = pending
    _save_bank(uni_row["id"], bank)
    campus = uni_row.get("universite") or uni_row.get("sigle") or uni_row.get("codeUni")
    return {
        "universite": campus,
        "bank": bank,
        "applied": all_done,
        "pendingChange": bank.get("pendingChange"),
    }


def verify_account_suffix(universite: str, suffix: str) -> bool:
    bank = get_partner_bank(universite)
    if not bank:
        return False
    digits = _digits_only(bank.get("accountNumber") or "")
    suffix_digits = _digits_only(suffix)
    if len(suffix_digits) < 4:
        return False
    return digits.endswith(suffix_digits)


def create_academic_payment(actor: dict, body: dict) -> dict:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")
    campus = resolve_campus_id(actor.get("universite") or "") or actor.get("universite") or ""
    bank = get_partner_bank(campus)
    if not bank:
        raise ValueError("BANK_NOT_CONFIGURED")
    suffix = body.get("accountSuffix") or body.get("accountConfirmSuffix") or ""
    if not verify_account_suffix(campus, suffix):
        raise ValueError("ACCOUNT_SUFFIX_MISMATCH")
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
