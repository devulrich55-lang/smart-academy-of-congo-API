import random
import time
import uuid
from datetime import datetime, timedelta, timezone

from app.database import get_db, row_to_user
from app.services.user_service import (
    clear_failed_logins,
    find_user_by_identifier,
    is_account_locked,
    record_failed_login,
    user_to_session,
    verify_password,
)
from app.utils.tokens import (
    generate_refresh_token_raw,
    hash_token,
    sign_access_token,
)


REFRESH_DAYS = 7


def _store_refresh_token(user_id: str, refresh_raw: str) -> str:
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=REFRESH_DAYS)
    ).isoformat()
    get_db().execute(
        """INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user_id,
            hash_token(refresh_raw),
            expires_at,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    get_db().commit()
    return refresh_raw


def issue_tokens(user: dict) -> dict:
    payload = {"sub": user["id"], "role": user["role"], "email": user["email"]}
    access_token = sign_access_token(payload)
    refresh_raw = generate_refresh_token_raw()
    _store_refresh_token(user["id"], refresh_raw)
    return {
        "accessToken": access_token,
        "refreshRaw": refresh_raw,
        "session": user_to_session(user),
    }


def login(
    identifier: str,
    password: str,
    expected_role: str | None = None,
    options: dict | None = None,
) -> dict:
    options = options or {}
    user = find_user_by_identifier(identifier)
    if not user:
        time.sleep(0.3 + random.random() * 0.2)
        raise ValueError("INVALID_CREDENTIALS")

    if expected_role and user["role"] != expected_role:
        raise ValueError("ROLE_MISMATCH")

    if options.get("adminPortal"):
        if user["role"] not in ("ministere", "superadmin"):
            raise ValueError("ROLE_MISMATCH")
    elif user["role"] in ("ministere", "superadmin"):
        raise ValueError("ADMIN_PORTAL_REQUIRED")

    registered_uni = (
        user.get("universite") or user.get("sigle") or user.get("codeUni")
        if user["role"] == "universite"
        else user.get("universite")
    )
    if (
        options.get("universite")
        and registered_uni
        and user["role"] in ("etudiant", "professeur", "assistant", "section")
        and options["universite"] != registered_uni
    ):
        raise ValueError("UNIVERSITY_MISMATCH")
    if (
        options.get("codeUni")
        and user["role"] == "universite"
        and user.get("codeUni")
        and options["codeUni"].strip().upper() != user["codeUni"].strip().upper()
    ):
        raise ValueError("CODE_UNI_MISMATCH")

    if is_account_locked(user):
        raise ValueError("ACCOUNT_LOCKED")

    if not verify_password(user, password):
        record_failed_login(user["id"])
        raise ValueError("INVALID_CREDENTIALS")

    clear_failed_logins(user["id"])
    return issue_tokens(user)


def refresh_session(refresh_raw: str | None) -> dict:
    if not refresh_raw or not isinstance(refresh_raw, str):
        raise ValueError("INVALID_REFRESH")

    db = get_db()
    token_hash = hash_token(refresh_raw)
    now = datetime.now(timezone.utc).isoformat()
    stored = db.execute(
        "SELECT * FROM refresh_tokens WHERE token_hash = ? AND expires_at > ?",
        (token_hash, now),
    ).fetchone()
    if not stored:
        raise ValueError("INVALID_REFRESH")

    user_row = db.execute(
        "SELECT * FROM users WHERE id = ?", (stored["user_id"],)
    ).fetchone()
    if not user_row:
        raise ValueError("INVALID_REFRESH")

    db.execute("DELETE FROM refresh_tokens WHERE id = ?", (stored["id"],))
    db.commit()
    return issue_tokens(row_to_user(user_row))


def logout(refresh_raw: str | None) -> None:
    if not refresh_raw:
        return
    get_db().execute(
        "DELETE FROM refresh_tokens WHERE token_hash = ?",
        (hash_token(refresh_raw),),
    )
    get_db().commit()
