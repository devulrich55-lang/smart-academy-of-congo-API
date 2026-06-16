import uuid
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.database import get_db
from app.services.email_service import send_password_reset_email
from app.services.user_service import (
    clear_failed_logins,
    find_user_by_email,
    get_display_name_from_user,
    revoke_all_refresh_tokens,
    update_password,
)
from app.utils.sanitize import validate_email_strict, validate_password
from app.utils.tokens import generate_reset_code, generate_reset_token_raw, hash_token

RESET_HOURS = settings.reset_token_hours


def request_password_reset(email: str) -> None:
    """Crée un token + code à 6 chiffres et envoie l'e-mail si le compte existe."""
    normalized = validate_email_strict(email)
    if not normalized:
        return

    user = find_user_by_email(normalized)
    if not user:
        return

    db = get_db()
    now = datetime.now(timezone.utc)
    db.execute(
        "DELETE FROM password_reset_tokens WHERE user_id = ? AND used_at IS NULL",
        (user["id"],),
    )

    reset_raw = generate_reset_token_raw()
    reset_code = generate_reset_code()
    expires_at = (now + timedelta(hours=RESET_HOURS)).isoformat()
    db.execute(
        """INSERT INTO password_reset_tokens
           (id, user_id, token_hash, code_hash, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user["id"],
            hash_token(reset_raw),
            hash_token(reset_code),
            expires_at,
            now.isoformat(),
        ),
    )
    db.commit()

    reset_url = f"{settings.frontend_url}/reinitialisation.html?token={reset_raw}"
    display_name = get_display_name_from_user(user)
    sent = send_password_reset_email(user["email"], reset_url, display_name, reset_code)

    if settings.is_prod and not sent:
        db.execute(
            "DELETE FROM password_reset_tokens WHERE user_id = ? AND used_at IS NULL",
            (user["id"],),
        )
        db.commit()


def _consume_reset_row(stored: dict, new_password: str) -> None:
    if not validate_password(new_password):
        raise ValueError("INVALID_PASSWORD")

    db = get_db()
    user_id = stored["user_id"]
    update_password(user_id, new_password)
    clear_failed_logins(user_id)
    revoke_all_refresh_tokens(user_id)

    used_at = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
        (used_at, stored["id"]),
    )
    db.execute(
        "DELETE FROM password_reset_tokens WHERE user_id = ? AND id != ?",
        (user_id, stored["id"]),
    )
    db.commit()


def reset_password(token: str, new_password: str) -> None:
    if not token or not isinstance(token, str):
        raise ValueError("INVALID_RESET_TOKEN")

    db = get_db()
    token_hash = hash_token(token.strip())
    now = datetime.now(timezone.utc).isoformat()
    stored = db.execute(
        """SELECT * FROM password_reset_tokens
           WHERE token_hash = ? AND expires_at > ? AND used_at IS NULL""",
        (token_hash, now),
    ).fetchone()
    if not stored:
        raise ValueError("INVALID_RESET_TOKEN")

    _consume_reset_row(stored, new_password)


def reset_password_with_code(email: str, code: str, new_password: str) -> None:
    normalized = validate_email_strict(email)
    if not normalized or not code or not isinstance(code, str):
        raise ValueError("INVALID_RESET_TOKEN")

    code_clean = code.strip().replace(" ", "")
    if not code_clean.isdigit() or len(code_clean) != 6:
        raise ValueError("INVALID_RESET_TOKEN")

    user = find_user_by_email(normalized)
    if not user:
        raise ValueError("INVALID_RESET_TOKEN")

    db = get_db()
    code_hash = hash_token(code_clean)
    now = datetime.now(timezone.utc).isoformat()
    stored = db.execute(
        """SELECT * FROM password_reset_tokens
           WHERE user_id = ? AND code_hash = ? AND expires_at > ? AND used_at IS NULL""",
        (user["id"], code_hash, now),
    ).fetchone()
    if not stored:
        raise ValueError("INVALID_RESET_TOKEN")

    _consume_reset_row(stored, new_password)
