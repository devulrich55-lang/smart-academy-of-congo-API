import hashlib
import hmac
import json
import re
import secrets
import uuid

from app.config import settings


def uid(prefix: str = "id") -> str:
    return f"{prefix}-{uuid.uuid4()}"


def hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return hmac.new(
        settings.platform_secret.encode(),
        ip.encode(),
        hashlib.sha256,
    ).hexdigest()[:16]


def sign_diploma(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"))
    return hmac.new(
        settings.platform_secret.encode(),
        raw.encode(),
        hashlib.sha256,
    ).hexdigest()


def generate_verification_code() -> str:
    return secrets.token_hex(16).upper()


def generate_diploma_number(universite: str | None, year: int) -> str:
    code = re.sub(r"[^A-Z0-9]", "", str(universite or "UNK").upper())[:6]
    seq = secrets.token_hex(3).upper()
    return f"SAC-{code}-{year}-{seq}"


def assert_campus_access(user: dict | None, universite: str | None) -> str:
    if not user:
        raise ValueError("AUTH_REQUIRED")
    if user.get("role") == "universite":
        code = user.get("universite") or user.get("codeUni") or user.get("sigle")
        if code and universite and code != universite:
            raise ValueError("FORBIDDEN_CAMPUS")
        return code or universite or ""
    if not universite or user.get("universite") != universite:
        raise ValueError("FORBIDDEN_CAMPUS")
    return universite
