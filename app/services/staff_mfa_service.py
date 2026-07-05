"""Vérification e-mail (code 6 chiffres) pour portails staff."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.services.auth_service import INSTITUTIONAL_PORTAL_ROLES, issue_tokens
from app.services.email_service import send_staff_login_code_email, smtp_configured
from app.services.user_service import get_display_name_from_user
from app.utils.tokens import generate_reset_code, hash_token, sign_mfa_challenge, verify_mfa_challenge


MFA_MINUTES = 10


def staff_mfa_enabled() -> bool:
    return bool(settings.staff_mfa_enabled and smtp_configured())


def should_require_staff_mfa(user: dict, options: dict | None) -> bool:
    options = options or {}
    if not options.get("adminPortal"):
        return False
    if user.get("role") not in INSTITUTIONAL_PORTAL_ROLES:
        return False
    return staff_mfa_enabled()


def _mask_email(email: str) -> str:
    email = str(email or "").strip()
    if "@" not in email:
        return email[:2] + "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked = local[0] + "*"
    else:
        masked = local[0] + "*" * (len(local) - 2) + local[-1]
    return masked + "@" + domain


def start_staff_mfa(user: dict) -> dict:
    from app.database import get_db

    if not smtp_configured():
        raise ValueError("EMAIL_NOT_CONFIGURED")

    db = get_db()
    now = datetime.now(timezone.utc)
    challenge_id = str(uuid.uuid4())
    code = generate_reset_code()
    expires_at = (now + timedelta(minutes=MFA_MINUTES)).isoformat()

    db.execute(
        "DELETE FROM staff_mfa_challenges WHERE user_id = ? AND used_at IS NULL",
        (user["id"],),
    )
    db.execute(
        """INSERT INTO staff_mfa_challenges
           (id, user_id, code_hash, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            challenge_id,
            user["id"],
            hash_token(code),
            expires_at,
            now.isoformat(),
        ),
    )
    db.commit()

    display = get_display_name_from_user(user)
    sent = send_staff_login_code_email(user["email"], display, code, MFA_MINUTES)
    if not sent:
        db.execute(
            "DELETE FROM staff_mfa_challenges WHERE id = ?",
            (challenge_id,),
        )
        db.commit()
        raise ValueError("EMAIL_SEND_FAILED")

    token = sign_mfa_challenge({"cid": challenge_id, "uid": user["id"]})
    return {
        "mfaRequired": True,
        "mfaChallenge": token,
        "emailHint": _mask_email(user["email"]),
    }


def verify_staff_mfa(challenge_token: str, code: str) -> dict:
    from app.database import get_db, row_to_user

    payload = verify_mfa_challenge(challenge_token)
    challenge_id = payload.get("cid")
    user_id = payload.get("uid")
    if not challenge_id or not user_id:
        raise ValueError("INVALID_MFA")

    normalized = str(code or "").strip()
    if len(normalized) != 6 or not normalized.isdigit():
        raise ValueError("INVALID_MFA")

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    row = db.execute(
        """SELECT * FROM staff_mfa_challenges
           WHERE id = ? AND user_id = ? AND used_at IS NULL AND expires_at > ?""",
        (challenge_id, user_id, now),
    ).fetchone()
    if not row:
        raise ValueError("INVALID_MFA")

    if hash_token(normalized) != row["code_hash"]:
        raise ValueError("INVALID_MFA")

    db.execute(
        "UPDATE staff_mfa_challenges SET used_at = ? WHERE id = ?",
        (now, challenge_id),
    )
    db.commit()

    user_row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    user = row_to_user(user_row)
    if not user:
        raise ValueError("INVALID_MFA")

    return issue_tokens(user)
