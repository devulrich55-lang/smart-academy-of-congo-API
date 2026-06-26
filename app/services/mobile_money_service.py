import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import error, request as urlrequest

from app.config import settings
from app.database import get_db
from app.utils.sanitize import clean_text

PROVIDERS = frozenset({"orange", "mpesa"})
PURPOSES = frozenset({"inscription", "academic_fee"})
STATUSES = frozenset(
    {"pending", "awaiting_pin", "processing", "completed", "failed", "expired"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_phone(phone: str) -> str:
    digits = _digits_only(phone)
    if digits.startswith("243") and len(digits) >= 12:
        return "+" + digits
    if len(digits) == 9 and digits[0] in "89":
        return "+243" + digits
    if len(digits) == 10 and digits.startswith("0"):
        return "+243" + digits[1:]
    if phone.strip().startswith("+"):
        return "+" + digits
    raise ValueError("INVALID_PHONE")


def _row_to_tx(row) -> dict:
    meta = {}
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        meta = {}
    return {
        "id": row["id"],
        "provider": row["provider"],
        "payerPhone": row["payer_phone"],
        "payerPhoneMasked": mask_phone(row["payer_phone"]),
        "amountCdf": int(row["amount_cdf"] or 0),
        "amountUsd": float(row["amount_usd"] or 0),
        "currency": row["currency"] or "CDF",
        "purpose": row["purpose"],
        "status": row["status"],
        "referenceExternal": row["reference_external"] or "",
        "metadata": meta,
        "userEmail": row["user_email"] or "",
        "universite": row["universite"] or "",
        "academicPaymentId": row["academic_payment_id"] or "",
        "errorMessage": row["error_message"] or "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "completedAt": row["completed_at"] or "",
        "merchantPhone": merchant_phone(row["provider"]),
        "providerMode": settings.mobile_money_provider,
    }


def mask_phone(phone: str) -> str:
    digits = _digits_only(phone)
    if len(digits) < 6:
        return "—"
    return "+" + digits[:3] + " " + digits[3:5] + "X" * max(0, len(digits) - 7) + digits[-2:]


def merchant_phone(provider: str) -> str:
    if provider == "mpesa":
        return settings.sac_mpesa_merchant_phone
    return settings.sac_orange_merchant_phone


def _new_id() -> str:
    return "MM-" + uuid.uuid4().hex[:12].upper()


def _provider_ready() -> bool:
    return (
        settings.mobile_money_provider == "flexpay"
        and bool(settings.flexpay_api_url)
        and bool(settings.flexpay_api_key)
    )


def _flexpay_request(path: str, payload: dict) -> dict:
    base = settings.flexpay_api_url.rstrip("/")
    url = base + path
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.flexpay_api_key}",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ValueError(f"PROVIDER_ERROR:{detail}") from exc
    except error.URLError as exc:
        raise ValueError("PROVIDER_OFFLINE") from exc


def _dispatch_provider(tx_id: str, provider: str, phone: str, amount_cdf: int) -> dict:
    if not _provider_ready():
        return {"status": "awaiting_pin", "referenceExternal": ""}
    reference = tx_id
    payload = {
        "merchant_id": settings.flexpay_merchant_id,
        "reference": reference,
        "amount": amount_cdf,
        "currency": "CDF",
        "phone": phone,
        "provider": "orange_money" if provider == "orange" else "mpesa",
        "callback_url": f"{settings.api_public_url.rstrip('/')}/api/payments/mobile/webhook",
    }
    data = _flexpay_request("/v1/payments/request", payload)
    ext_ref = (
        data.get("transaction_id")
        or data.get("reference")
        or data.get("id")
        or reference
    )
    status = str(data.get("status") or "processing").lower()
    if status in ("success", "completed", "paid"):
        return {"status": "completed", "referenceExternal": str(ext_ref)}
    if status in ("failed", "error", "cancelled"):
        raise ValueError("PROVIDER_DECLINED")
    return {"status": "processing", "referenceExternal": str(ext_ref)}


def initiate(
    body: dict,
    actor: dict | None = None,
) -> dict:
    provider = clean_text(body.get("provider"), 20) or ""
    if provider not in PROVIDERS:
        raise ValueError("INVALID_PROVIDER")
    purpose = clean_text(body.get("purpose"), 40) or "inscription"
    if purpose not in PURPOSES:
        raise ValueError("INVALID_PURPOSE")

    if purpose == "academic_fee":
        if not actor or actor.get("role") != "etudiant":
            raise ValueError("FORBIDDEN")
    elif purpose == "inscription" and actor and actor.get("role") not in (
        None,
        "etudiant",
        "professeur",
        "assistant",
        "universite",
    ):
        pass

    try:
        phone = normalize_phone(body.get("payerPhone") or body.get("phone") or "")
    except ValueError:
        raise ValueError("INVALID_PHONE") from None

    amount_cdf = int(body.get("amountCdf") or body.get("amount") or 0)
    if amount_cdf < 500 or amount_cdf > 50_000_000:
        raise ValueError("INVALID_AMOUNT")

    amount_usd = float(body.get("amountUsd") or 0)
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    metadata = {str(k): clean_text(v, 200) for k, v in metadata.items()}

    email = ""
    universite = ""
    if actor:
        email = (actor.get("email") or "").lower()
        universite = clean_text(
            actor.get("universite") or actor.get("codeUni"), 80
        )
    else:
        email = clean_text(body.get("email"), 255).lower()
        universite = clean_text(body.get("universite"), 80)

    tx_id = _new_id()
    now = _now()
    initial_status = "awaiting_pin" if not _provider_ready() else "processing"

    get_db().execute(
        """INSERT INTO mobile_money_transactions (
            id, provider, payer_phone, amount_cdf, amount_usd, currency, purpose,
            status, reference_external, metadata_json, user_email, universite,
            academic_payment_id, error_message, created_at, updated_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            tx_id,
            provider,
            phone,
            amount_cdf,
            round(amount_usd, 2),
            "CDF",
            purpose,
            initial_status,
            "",
            json.dumps(metadata),
            email,
            universite,
            "",
            "",
            now,
            now,
            "",
        ),
    )
    get_db().commit()

    if _provider_ready():
        try:
            result = _dispatch_provider(tx_id, provider, phone, amount_cdf)
            status = result["status"]
            ext = result.get("referenceExternal") or ""
            get_db().execute(
                """UPDATE mobile_money_transactions
                   SET status = ?, reference_external = ?, updated_at = ?
                   WHERE id = ?""",
                (status, ext, _now(), tx_id),
            )
            get_db().commit()
        except ValueError as exc:
            code = str(exc)
            get_db().execute(
                """UPDATE mobile_money_transactions
                   SET status = 'failed', error_message = ?, updated_at = ?
                   WHERE id = ?""",
                (code, _now(), tx_id),
            )
            get_db().commit()
            raise

    row = get_db().execute(
        "SELECT * FROM mobile_money_transactions WHERE id = ?", (tx_id,)
    ).fetchone()
    return _row_to_tx(row)


def get_status(tx_id: str, actor: dict | None = None) -> dict:
    row = get_db().execute(
        "SELECT * FROM mobile_money_transactions WHERE id = ?",
        (clean_text(tx_id, 80),),
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    if actor:
        email = (actor.get("email") or "").lower()
        if row["user_email"] and row["user_email"].lower() != email:
            raise ValueError("FORBIDDEN")
    if row["status"] == "processing" and _provider_ready():
        _refresh_provider_status(row)
        row = get_db().execute(
            "SELECT * FROM mobile_money_transactions WHERE id = ?", (row["id"],)
        ).fetchone()
    return _row_to_tx(row)


def _refresh_provider_status(row) -> None:
    if not row["reference_external"]:
        return
    try:
        data = _flexpay_request(
            "/v1/payments/status",
            {"reference": row["reference_external"]},
        )
    except ValueError:
        return
    status = str(data.get("status") or "").lower()
    if status in ("success", "completed", "paid"):
        _mark_completed(row["id"], row["reference_external"])
    elif status in ("failed", "error", "cancelled"):
        get_db().execute(
            """UPDATE mobile_money_transactions
               SET status = 'failed', error_message = ?, updated_at = ?
               WHERE id = ?""",
            ("PROVIDER_DECLINED", _now(), row["id"]),
        )
        get_db().commit()


def confirm_pin(tx_id: str, pin: str, actor: dict | None = None) -> dict:
    if _provider_ready():
        raise ValueError("PIN_NOT_REQUIRED")
    row = get_db().execute(
        "SELECT * FROM mobile_money_transactions WHERE id = ?",
        (clean_text(tx_id, 80),),
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    if actor and row["user_email"]:
        if row["user_email"].lower() != (actor.get("email") or "").lower():
            raise ValueError("FORBIDDEN")
    if row["status"] not in ("awaiting_pin", "pending", "processing"):
        if row["status"] == "completed":
            return _row_to_tx(row)
        raise ValueError("INVALID_STATUS")

    pin_clean = _digits_only(pin)
    if len(pin_clean) < 4:
        raise ValueError("INVALID_PIN")
    if settings.mobile_money_sandbox_pin:
        if pin_clean != _digits_only(settings.mobile_money_sandbox_pin):
            raise ValueError("INVALID_PIN")

    _mark_completed(row["id"], "SANDBOX-" + pin_clean[:4])
    updated = get_db().execute(
        "SELECT * FROM mobile_money_transactions WHERE id = ?", (row["id"],)
    ).fetchone()
    return _row_to_tx(updated)


def _mark_completed(tx_id: str, external_ref: str = "") -> None:
    now = _now()
    get_db().execute(
        """UPDATE mobile_money_transactions
           SET status = 'completed', reference_external = COALESCE(NULLIF(?, ''), reference_external),
               completed_at = ?, updated_at = ?, error_message = ''
           WHERE id = ?""",
        (external_ref, now, now, tx_id),
    )
    get_db().commit()


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    secret = settings.mobile_money_webhook_secret
    if not secret:
        return settings.mobile_money_provider != "flexpay"
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, (signature or "").strip())


def handle_webhook(payload: dict) -> dict:
    reference = (
        payload.get("reference")
        or payload.get("transaction_id")
        or payload.get("merchantReference")
        or ""
    )
    if not reference:
        raise ValueError("INVALID_WEBHOOK")
    row = get_db().execute(
        """SELECT * FROM mobile_money_transactions
           WHERE id = ? OR reference_external = ?
           ORDER BY created_at DESC LIMIT 1""",
        (clean_text(str(reference), 80), clean_text(str(reference), 120)),
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    status = str(payload.get("status") or "").lower()
    if status in ("success", "completed", "paid"):
        _mark_completed(row["id"], str(reference))
    elif status in ("failed", "error", "cancelled"):
        get_db().execute(
            """UPDATE mobile_money_transactions
               SET status = 'failed', error_message = ?, updated_at = ?
               WHERE id = ?""",
            ("PROVIDER_DECLINED", _now(), row["id"]),
        )
        get_db().commit()
    return get_status(row["id"])


def assert_completed_for_use(tx_id: str, expected_purpose: str, actor: dict | None = None) -> dict:
    tx = get_status(tx_id, actor)
    if tx["purpose"] != expected_purpose:
        raise ValueError("INVALID_PURPOSE")
    if tx["status"] != "completed":
        raise ValueError("PAYMENT_NOT_COMPLETED")
    return tx


def expire_stale_transactions() -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    cur = get_db().execute(
        """UPDATE mobile_money_transactions
           SET status = 'expired', updated_at = ?
           WHERE status IN ('pending', 'awaiting_pin', 'processing')
           AND created_at < ?""",
        (_now(), cutoff),
    )
    get_db().commit()
    return getattr(cur, "rowcount", 0) or 0
