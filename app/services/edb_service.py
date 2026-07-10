"""EvoDigitalBooks — auteurs, achats, limite appareils, activation compte auteur."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.database import get_db, is_duplicate_key_error
from app.services.user_service import find_user_by_email, pwd_context
from app.utils.sanitize import clean_email, clean_phone, clean_text, validate_email_strict, validate_password

PLATFORM_FEE_RATE = 0.25
MAX_DEVICES = 3
SOURCE = "evodigitalbooks"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_fee(amount: float) -> dict[str, float]:
    total = float(amount or 0)
    platform_fee = round(total * PLATFORM_FEE_RATE, 2)
    author_share = round(total - platform_fee, 2)
    return {"total": total, "platform_fee": platform_fee, "author_share": author_share}


def _row_author(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "penName": row["pen_name"],
        "mobileMoney": row["mobile_money"],
        "bio": row["bio"] or "",
        "status": row["status"],
        "createdAt": row["created_at"],
        "reviewedAt": row["reviewed_at"] or "",
        "reviewedBy": row["reviewed_by"] or "",
    }


def get_author_by_email(email: str) -> dict[str, Any] | None:
    email = clean_email(email) or str(email or "").strip().lower()
    if not email:
        return None
    row = get_db().execute(
        "SELECT * FROM edb_authors WHERE email = ? COLLATE NOCASE", (email,)
    ).fetchone()
    return _row_author(row) if row else None


def get_author_status(email: str) -> str | None:
    author = get_author_by_email(email)
    return author["status"] if author else None


def register_author(
    email: str,
    password: str,
    pen_name: str,
    mobile_money: str,
    bio: str = "",
) -> dict[str, Any]:
    email = validate_email_strict(email) or clean_email(email)
    if not email:
        raise ValueError("INVALID_EMAIL")
    if not validate_password(password):
        raise ValueError("INVALID_PASSWORD")
    pen_name = clean_text(pen_name, 120)
    if not pen_name or len(pen_name) < 2:
        raise ValueError("INVALID_PROFILE")
    try:
        mobile_norm = normalize_phone(mobile_money)
    except ValueError:
        raise ValueError("INVALID_PHONE") from None

    if find_user_by_email(email):
        raise ValueError("EMAIL_EXISTS")
    if get_author_by_email(email):
        raise ValueError("EMAIL_EXISTS")

    password_hash = pwd_context.hash(password)
    author_id = f"edb_author_{uuid.uuid4().hex[:12]}"
    now = _now()
    db = get_db()
    try:
        db.execute(
            """INSERT INTO edb_authors
               (id, email, pen_name, mobile_money, bio, password_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                author_id,
                email,
                pen_name,
                mobile_norm,
                clean_text(bio, 2000),
                password_hash,
                now,
            ),
        )
        db.commit()
    except Exception as exc:
        if is_duplicate_key_error(exc):
            raise ValueError("EMAIL_EXISTS") from exc
        raise

    return {
        "id": author_id,
        "email": email,
        "penName": pen_name,
        "mobileMoney": mobile_norm,
        "status": "pending",
        "createdAt": now,
    }


def list_pending_authors() -> list[dict[str, Any]]:
    rows = get_db().execute(
        """SELECT * FROM edb_authors
           WHERE status = 'pending'
           ORDER BY created_at DESC"""
    ).fetchall()
    return [_row_author(r) for r in rows]


def _pen_name_parts(pen_name: str) -> tuple[str, str]:
    parts = pen_name.split()
    if not parts:
        return "Auteur", "EvoDigitalBooks"
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def _activate_author_user(email: str, password_hash: str, pen_name: str, mobile_money: str) -> None:
    existing = find_user_by_email(email)
    if existing:
        if existing.get("role") != "auteur":
            raise ValueError("IDENTITY_CONFLICT")
        return

    prenom, nom = _pen_name_parts(pen_name)
    user_id = str(uuid.uuid4())
    now = _now()
    phone = clean_phone(mobile_money)
    get_db().execute(
        """INSERT INTO users (
            id, email, password_hash, role, prenom, nom, telephone,
            universite, filiere, niveau, matricule, date_naissance, departement,
            grade, service, fonction, num_employe, num_assist, nom_universite,
            sigle, ville, adresse, nb_etudiants, site_web, responsable, code_uni,
            cours_classes, payment, inscription_fee, classe, section_id, logo_url,
            country_code, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            user_id,
            email,
            password_hash,
            "auteur",
            prenom,
            nom,
            phone,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "[]",
            None,
            None,
            None,
            None,
            None,
            "CD",
            now,
            now,
        ),
    )
    get_db().commit()


def activate_auteur_if_approved(identifier: str, password: str) -> dict | None:
    """Crée le compte users si l'auteur est approuvé mais pas encore activé."""
    email = clean_email(identifier) or str(identifier or "").strip().lower()
    if not email:
        return None
    row = get_db().execute(
        "SELECT * FROM edb_authors WHERE email = ? COLLATE NOCASE", (email,)
    ).fetchone()
    if not row or row["status"] != "approved":
        return None

    existing = find_user_by_email(email)
    if existing:
        return existing if existing.get("role") == "auteur" else None

    if not pwd_context.verify(password, row["password_hash"]):
        return None

    _activate_author_user(email, row["password_hash"], row["pen_name"], row["mobile_money"])
    return find_user_by_email(email)


def auteur_login_hint(identifier: str) -> str | None:
    """pending | rejected | None"""
    author = get_author_by_email(identifier)
    if not author:
        return None
    status = author.get("status")
    if status in ("pending", "rejected"):
        return status
    return None


def set_author_status(email: str, status: str, reviewer: str = "") -> dict[str, Any]:
    email = clean_email(email) or str(email or "").strip().lower()
    if status not in ("approved", "rejected", "pending"):
        raise ValueError("INVALID_STATUS")

    row = get_db().execute(
        "SELECT * FROM edb_authors WHERE email = ? COLLATE NOCASE", (email,)
    ).fetchone()
    if not row:
        raise ValueError("AUTHOR_NOT_FOUND")

    now = _now()
    db = get_db()
    db.execute(
        """UPDATE edb_authors
           SET status = ?, reviewed_at = ?, reviewed_by = ?
           WHERE email = ? COLLATE NOCASE""",
        (status, now, clean_text(reviewer, 255), email),
    )
    db.commit()

    if status == "approved":
        _activate_author_user(
            email,
            row["password_hash"],
            row["pen_name"],
            row["mobile_money"],
        )

    updated = get_db().execute(
        "SELECT * FROM edb_authors WHERE email = ? COLLATE NOCASE", (email,)
    ).fetchone()
    return _row_author(updated)


def normalize_phone(phone: str) -> str:
    from app.services.mobile_money_service import normalize_phone as mm_normalize

    return mm_normalize(phone)


def _device_count(buyer_email: str) -> int:
    row = get_db().execute(
        "SELECT COUNT(*) AS c FROM edb_devices WHERE buyer_email = ? COLLATE NOCASE",
        (buyer_email,),
    ).fetchone()
    return int(row["c"] or 0)


def register_device(buyer_email: str, device_id: str) -> dict[str, Any]:
    buyer_email = clean_email(buyer_email) or str(buyer_email or "").strip().lower()
    device_id = clean_text(device_id, 120)
    if not buyer_email or not device_id:
        return {"ok": False, "reason": "invalid"}

    db = get_db()
    exists = db.execute(
        """SELECT 1 FROM edb_devices
           WHERE buyer_email = ? COLLATE NOCASE AND device_id = ?""",
        (buyer_email, device_id),
    ).fetchone()
    if exists:
        return {"ok": True, "deviceId": device_id}

    if _device_count(buyer_email) >= MAX_DEVICES:
        return {"ok": False, "reason": "device_limit", "max": MAX_DEVICES}

    db.execute(
        """INSERT INTO edb_devices (buyer_email, device_id, registered_at)
           VALUES (?, ?, ?)""",
        (buyer_email, device_id, _now()),
    )
    db.commit()
    return {"ok": True, "deviceId": device_id}


def record_purchase(payload: dict[str, Any]) -> dict[str, Any]:
    book_id = clean_text(payload.get("bookId") or payload.get("book_id"), 80)
    buyer_email = clean_email(payload.get("email") or payload.get("buyer_email") or "")
    if not buyer_email:
        buyer_email = str(payload.get("email") or payload.get("buyer_email") or "").strip().lower()
    device_id = clean_text(payload.get("deviceId") or payload.get("device_id"), 120)
    amount = float(payload.get("amount") or 0)
    currency = clean_text(payload.get("currency"), 10).upper() or "USD"
    fees = split_fee(amount)
    author_share = float(payload.get("authorShare") or fees["author_share"])
    platform_fee = float(payload.get("platformFee") or fees["platform_fee"])
    author_mobile = clean_text(
        payload.get("authorMobileMoney") or payload.get("author_mobile_money"), 30
    )
    author_email = clean_email(payload.get("authorEmail") or payload.get("author_email") or "")

    if not book_id or not buyer_email:
        raise ValueError("INVALID_PAYLOAD")

    if device_id:
        dev = register_device(buyer_email, device_id)
        if not dev.get("ok"):
            raise ValueError(dev.get("reason", "device_limit"))

    purchase_id = f"edb_purchase_{uuid.uuid4().hex[:12]}"
    db = get_db()
    db.execute(
        """INSERT INTO edb_purchases
           (id, book_id, buyer_email, amount, currency, author_share, platform_fee,
            author_email, author_mobile_money, device_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            purchase_id,
            book_id,
            buyer_email,
            amount,
            currency,
            author_share,
            platform_fee,
            author_email or "",
            author_mobile or "",
            device_id or "",
            _now(),
        ),
    )
    db.commit()
    return {
        "id": purchase_id,
        "bookId": book_id,
        "buyerEmail": buyer_email,
        "amount": amount,
        "authorShare": author_share,
        "platformFee": platform_fee,
        "ok": True,
    }


def record_purchase_from_mobile_tx(tx: dict) -> dict | None:
    if tx.get("purpose") != "evodigitalbooks" or tx.get("status") != "completed":
        return None
    meta = tx.get("metadata") if isinstance(tx.get("metadata"), dict) else {}
    book_id = clean_text(meta.get("bookId"), 80)
    buyer_email = clean_email(tx.get("userEmail") or meta.get("email") or "")
    if not book_id or not buyer_email:
        return None

    existing = get_db().execute(
        """SELECT id FROM edb_purchases
           WHERE book_id = ? AND buyer_email = ? COLLATE NOCASE
           LIMIT 1""",
        (book_id, buyer_email),
    ).fetchone()
    if existing:
        return {"id": existing["id"], "bookId": book_id, "buyerEmail": buyer_email, "ok": True}

    amount = float(meta.get("amountUsd") or tx.get("amountUsd") or 0)
    if not amount and tx.get("amountCdf"):
        amount = round(float(tx["amountCdf"]) / 2800, 2)
    currency = clean_text(meta.get("currency"), 10).upper() or "USD"
    fees = split_fee(amount)
    return record_purchase(
        {
            "bookId": book_id,
            "email": buyer_email,
            "amount": amount,
            "currency": currency,
            "authorShare": meta.get("authorShare") or fees["author_share"],
            "platformFee": meta.get("platformFee") or fees["platform_fee"],
            "authorEmail": meta.get("authorEmail") or "",
            "authorMobileMoney": meta.get("authorMobileMoney") or "",
            "deviceId": meta.get("deviceId") or "",
        }
    )


def buyer_owns_book(buyer_email: str, book_id: str) -> bool:
    buyer_email = clean_email(buyer_email) or str(buyer_email or "").strip().lower()
    book_id = clean_text(book_id, 80)
    if not buyer_email or not book_id:
        return False
    row = get_db().execute(
        """SELECT 1 FROM edb_purchases
           WHERE buyer_email = ? COLLATE NOCASE AND book_id = ?
           LIMIT 1""",
        (buyer_email, book_id),
    ).fetchone()
    return row is not None


def list_purchased_book_ids(buyer_email: str) -> list[str]:
    buyer_email = clean_email(buyer_email) or str(buyer_email or "").strip().lower()
    if not buyer_email:
        return []
    rows = get_db().execute(
        """SELECT DISTINCT book_id FROM edb_purchases
           WHERE buyer_email = ? COLLATE NOCASE
           ORDER BY created_at DESC""",
        (buyer_email,),
    ).fetchall()
    return [r["book_id"] for r in rows if r.get("book_id")]
