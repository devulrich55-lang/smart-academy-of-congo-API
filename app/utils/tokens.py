import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.config import settings


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign_access_token(payload: dict) -> str:
    data = {
        **payload,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    return jwt.encode(data, settings.jwt_access_secret, algorithm="HS256")


def verify_access_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.jwt_access_secret,
            algorithms=["HS256"],
        )
    except JWTError as exc:
        raise ValueError("TOKEN_EXPIRED") from exc


def generate_refresh_token_raw() -> str:
    return secrets.token_urlsafe(48)


def generate_reset_token_raw() -> str:
    return secrets.token_urlsafe(48)


def generate_reset_code() -> str:
    """Code numérique à 6 chiffres pour réinitialisation par e-mail."""
    return f"{secrets.randbelow(1_000_000):06d}"
