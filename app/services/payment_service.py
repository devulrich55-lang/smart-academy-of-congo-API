import json
import re
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.database import get_db
from app.services import email_service
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
    method = str(body.get("method") or "").strip()
    if method not in VALID_METHODS:
        raise ValueError("INVALID_METHOD")

    mobile_tx_id = clean_text(body.get("mobileTransactionId"), 80)
    if method in ("orange", "mpesa"):
        if not mobile_tx_id:
            raise ValueError("MOBILE_TX_REQUIRED")
        from app.services import mobile_money_service

        tx = mobile_money_service.assert_completed_for_use(
            mobile_tx_id, "academic_fee", actor
        )
        if tx.get("universite") and not same_campus(campus, tx["universite"]):
            raise ValueError("FORBIDDEN")
        reference = mobile_tx_id
        amount = float(body.get("amount") or tx.get("amountUsd") or 0)
        currency = clean_text(body.get("currency"), 10) or "USD"
    else:
        bank = get_partner_bank(campus)
        if not bank:
            raise ValueError("BANK_NOT_CONFIGURED")
        suffix = body.get("accountSuffix") or body.get("accountConfirmSuffix") or ""
        if not verify_account_suffix(campus, suffix):
            raise ValueError("ACCOUNT_SUFFIX_MISMATCH")
        amount = float(body.get("amount") or 0)
        currency = clean_text(body.get("currency"), 10) or "USD"
        reference = clean_text(body.get("reference"), 120)
        if not reference or len(reference) < 4:
            raise ValueError("INVALID_REFERENCE")

    if amount <= 0 or amount > 100000:
        raise ValueError("INVALID_AMOUNT")
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
            currency,
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
    if method in ("orange", "mpesa") and mobile_tx_id:
        db.execute(
            "UPDATE mobile_money_transactions SET academic_payment_id = ? WHERE id = ?",
            (payment_id, mobile_tx_id),
        )
        db.commit()
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
    payment = _row_to_payment(row)
    if email_service.smtp_configured() and row["student_email"]:
        if status == "confirmed":
            title = "Paiement confirmé"
            msg = (
                f"Votre paiement « {payment['feeLabel']} » "
                f"({payment['amount']} {payment['currency']}) a été confirmé par l'université."
            )
        else:
            title = "Paiement refusé"
            msg = (
                f"Votre paiement « {payment['feeLabel']} » "
                f"({payment['amount']} {payment['currency']}) n'a pas été validé. "
                "Contactez le service scolarité de votre campus."
            )
        email_service.send_platform_notification_email(
            row["student_email"],
            title,
            msg,
            f"{settings.frontend_url}/dashboard-etudiant.html#frais",
        )
    return payment


PLATFORM_AGGREGATOR_ROLES = frozenset({"superadmin", "techmanager", "developpeur"})


def _mobile_to_aggregator_tx(tx: dict) -> dict:
    purpose = tx.get("purpose") or "mobile"
    category = (
        "Inscription plateforme"
        if purpose == "inscription"
        else "Frais académique (Mobile Money)"
    )
    status = tx.get("status") or ""
    if status == "completed":
        norm_status = "confirmed"
    elif status in ("failed", "expired"):
        norm_status = "rejected"
    else:
        norm_status = "pending"
    amount_usd = float(tx.get("amountUsd") or 0)
    amount_cdf = int(tx.get("amountCdf") or 0)
    currency = "USD" if amount_usd > 0 else (tx.get("currency") or "CDF")
    amount = amount_usd if amount_usd > 0 else amount_cdf
    return {
        "id": tx.get("id"),
        "kind": "mobile",
        "universite": tx.get("universite") or "",
        "studentEmail": tx.get("userEmail") or "",
        "studentNom": "",
        "matricule": "—",
        "amount": amount,
        "currency": currency,
        "method": tx.get("provider") or "mobile",
        "status": norm_status,
        "rawStatus": status,
        "category": category,
        "feeKey": purpose,
        "feeLabel": category,
        "reference": tx.get("referenceExternal") or tx.get("id") or "",
        "createdAt": tx.get("createdAt") or "",
        "confirmedAt": tx.get("completedAt") or "",
        "confirmedBy": "",
    }


def _academic_to_aggregator_tx(payment: dict) -> dict:
    return {
        **payment,
        "kind": "academic",
        "category": payment.get("feeLabel") or payment.get("feeKey") or "Frais académiques",
        "rawStatus": payment.get("status") or "",
    }


def _summarize_transactions(transactions: list[dict]) -> dict:
    summary = {
        "totalCount": len(transactions),
        "confirmedCount": 0,
        "pendingCount": 0,
        "rejectedCount": 0,
        "totalAmountUsd": 0.0,
        "totalAmountCdf": 0,
        "byMethod": {},
        "byCategory": {},
        "byStatus": {},
    }
    for tx in transactions:
        status = tx.get("status") or "pending"
        summary["byStatus"][status] = summary["byStatus"].get(status, 0) + 1
        if status == "confirmed":
            summary["confirmedCount"] += 1
        elif status == "rejected":
            summary["rejectedCount"] += 1
        else:
            summary["pendingCount"] += 1
        method = tx.get("method") or "—"
        summary["byMethod"][method] = summary["byMethod"].get(method, 0) + 1
        category = tx.get("category") or "—"
        summary["byCategory"][category] = summary["byCategory"].get(category, 0) + 1
        currency = str(tx.get("currency") or "USD").upper()
        amount = float(tx.get("amount") or 0)
        if currency == "CDF":
            summary["totalAmountCdf"] += int(amount)
        else:
            summary["totalAmountUsd"] += amount
    summary["totalAmountUsd"] = round(summary["totalAmountUsd"], 2)
    return summary


def _group_by_university(transactions: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for tx in transactions:
        uni = str(tx.get("universite") or "").strip().lower() or "__platform__"
        if uni not in groups:
            groups[uni] = {
                "universite": uni if uni != "__platform__" else "",
                "label": uni if uni != "__platform__" else "Plateforme (inscription)",
                "transactions": [],
            }
        groups[uni]["transactions"].append(tx)
    out = []
    for group in groups.values():
        group["summary"] = _summarize_transactions(group["transactions"])
        group["transactionCount"] = len(group["transactions"])
        out.append(group)
    out.sort(key=lambda g: (-g["transactionCount"], g.get("label") or ""))
    return out


def campus_aggregator(actor: dict) -> dict:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    from app.services import mobile_money_service

    campus = resolve_campus_id(
        actor.get("universite") or actor.get("sigle") or actor.get("codeUni")
    ) or ""
    academic = [_academic_to_aggregator_tx(p) for p in list_payments_for_campus(actor)]
    mobile = [
        _mobile_to_aggregator_tx(tx)
        for tx in mobile_money_service.list_for_campus(campus)
    ]
    transactions = sorted(
        academic + mobile,
        key=lambda t: t.get("createdAt") or "",
        reverse=True,
    )
    return {
        "universite": campus,
        "summary": _summarize_transactions(transactions),
        "transactions": transactions,
    }


def platform_aggregator(actor: dict) -> dict:
    if actor.get("role") not in PLATFORM_AGGREGATOR_ROLES:
        raise ValueError("FORBIDDEN")
    from app.services import mobile_money_service

    rows = get_db().execute(
        "SELECT * FROM academic_payments ORDER BY created_at DESC LIMIT 3000"
    ).fetchall()
    academic = [_academic_to_aggregator_tx(_row_to_payment(r)) for r in rows]
    mobile = [_mobile_to_aggregator_tx(tx) for tx in mobile_money_service.list_all(3000)]
    transactions = sorted(
        academic + mobile,
        key=lambda t: t.get("createdAt") or "",
        reverse=True,
    )
    by_university = _group_by_university(transactions)
    return {
        "summary": _summarize_transactions(transactions),
        "byUniversity": by_university,
        "transactions": transactions,
    }
