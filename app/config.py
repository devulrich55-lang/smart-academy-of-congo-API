import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

required = ["JWT_ACCESS_SECRET", "JWT_REFRESH_SECRET"]
if os.getenv("NODE_ENV") == "production":
    for key in required:
        val = os.getenv(key, "")
        if not val or "CHANGEZ_MOI" in val.upper() or val.startswith("dev-only-"):
            raise RuntimeError(f"Variable {key} obligatoire en production (valeur unique 32+ caractères)")
    origins = os.getenv("ALLOWED_ORIGINS", "").strip()
    if not origins:
        raise RuntimeError("ALLOWED_ORIGINS obligatoire en production (domaine frontend)")


class Settings:
    env: str = os.getenv("NODE_ENV", "development")
    port: int = int(os.getenv("PORT", "8000"))
    is_prod: bool = env == "production"
    jwt_access_secret: str = os.getenv(
        "JWT_ACCESS_SECRET", "dev-only-access-secret-min-32-chars-long!!"
    )
    jwt_refresh_secret: str = os.getenv(
        "JWT_REFRESH_SECRET", "dev-only-refresh-secret-min-32-chars-long!"
    )
    jwt_access_expires: str = "15m"
    jwt_refresh_expires: str = "7d"
    db_path: Path = Path(
        os.getenv("DATABASE_PATH", str(ROOT / "data" / "sac.db"))
    ).resolve()
    upload_dir: Path = Path(
        os.getenv("UPLOAD_DIR", str(ROOT / "uploads"))
    ).resolve()
    allowed_origins: list[str] = [
        o.strip()
        for o in os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:8000,http://127.0.0.1:8000,"
            "http://localhost:5500,http://127.0.0.1:5500,"
            "http://localhost:5173,http://127.0.0.1:5173,"
            "http://localhost:5000,http://127.0.0.1:5000",
        ).split(",")
        if o.strip()
    ]
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    max_login_attempts: int = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
    lockout_minutes: int = int(os.getenv("LOCKOUT_MINUTES", "15"))
    platform_secret: str = os.getenv(
        "SAC_PLATFORM_SECRET", jwt_access_secret + ":platform"
    )
    frontend_root: Path = ROOT.parent
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:5500").rstrip("/")
    reset_token_hours: int = int(os.getenv("RESET_TOKEN_HOURS", "1"))
    smtp_host: str = os.getenv("SMTP_HOST", "").strip()
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "").strip()
    smtp_pass: str = os.getenv("SMTP_PASS", "").strip()
    email_from: str = os.getenv("EMAIL_FROM", "noreply@smartacademy.cd").strip()
    cross_origin_auth: bool = os.getenv("CROSS_ORIGIN_AUTH", "false").lower() == "true"


settings = Settings()
if settings.frontend_url and settings.frontend_url not in settings.allowed_origins:
    settings.allowed_origins.append(settings.frontend_url)
