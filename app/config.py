import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

required = ["JWT_ACCESS_SECRET", "JWT_REFRESH_SECRET"]
if os.getenv("NODE_ENV") == "production":
    for key in required:
        val = os.getenv(key, "")
        if not val or "CHANGEZ_MOI" in val.upper() or val.startswith("dev-only-"):
            raise RuntimeError(
                f"Variable {key} obligatoire en production (valeur unique 32+ caractères)"
            )
    origins = os.getenv("ALLOWED_ORIGINS", "").strip()
    if not origins:
        raise RuntimeError("ALLOWED_ORIGINS obligatoire en production (domaine frontend)")


def _parse_database_url(url: str) -> dict:
    normalized = url.replace("mysql+pymysql://", "mysql://").replace(
        "mysql+mysqlconnector://", "mysql://"
    )
    parsed = urlparse(normalized)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "/").lstrip("/"),
    }


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
    database_url: str = os.getenv("DATABASE_URL", "").strip()
    mysql_host: str = os.getenv("MYSQL_HOST", "localhost").strip()
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_user: str = os.getenv("MYSQL_USER", "root").strip()
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "").strip()
    mysql_database: str = os.getenv("MYSQL_DATABASE", "smart_academy").strip()
    db_path: Path = Path(
        os.getenv("DATABASE_PATH", str(ROOT / "data" / "sac.db"))
    ).resolve()
    upload_dir: Path = Path(
        os.getenv("UPLOAD_DIR", str(ROOT / "uploads"))
    ).resolve()
    allowed_origins: list[str] = [
        o.strip().rstrip("/")
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
    gmail_user: str = os.getenv("GMAIL_USER", "").strip()
    gmail_app_password: str = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    smtp_host: str = os.getenv("SMTP_HOST", "").strip()
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "").strip()
    smtp_pass: str = os.getenv("SMTP_PASS", "").strip()
    smtp_use_tls: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    smtp_use_ssl: bool = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
    email_from: str = os.getenv("EMAIL_FROM", "").strip()
    cross_origin_auth: bool = os.getenv("CROSS_ORIGIN_AUTH", "false").lower() == "true"
    api_page_default: int = int(os.getenv("API_PAGE_DEFAULT", "50"))
    api_page_max: int = int(os.getenv("API_PAGE_MAX", "200"))
    pons_api_secret: str = os.getenv("PONS_API_SECRET", "").strip()
    mobile_money_provider: str = os.getenv("MOBILE_MONEY_PROVIDER", "sandbox").strip().lower()
    flexpay_api_url: str = os.getenv("FLEXPAY_API_URL", "").strip().rstrip("/")
    flexpay_api_key: str = os.getenv("FLEXPAY_API_KEY", "").strip()
    flexpay_merchant_id: str = os.getenv("FLEXPAY_MERCHANT_ID", "").strip()
    mobile_money_webhook_secret: str = os.getenv("MOBILE_MONEY_WEBHOOK_SECRET", "").strip()
    mobile_money_sandbox_pin: str = os.getenv("MOBILE_MONEY_SANDBOX_PIN", "").strip()
    sac_orange_merchant_phone: str = os.getenv(
        "SAC_ORANGE_MERCHANT_PHONE", "+243851848859"
    ).strip()
    sac_mpesa_merchant_phone: str = os.getenv(
        "SAC_MPESA_MERCHANT_PHONE", "+243832479012"
    ).strip()
    api_public_url: str = os.getenv(
        "API_PUBLIC_URL",
        os.getenv("RENDER_EXTERNAL_URL", "http://localhost:8000"),
    ).strip().rstrip("/")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    orientation_use_llm: bool = os.getenv("ORIENTATION_USE_LLM", "true").lower() == "true"
    social_email_notifications: bool = (
        os.getenv("SOCIAL_EMAIL_NOTIFICATIONS", "true").lower() == "true"
    )

    @property
    def use_mysql(self) -> bool:
        if os.getenv("DATABASE_BACKEND", "").lower() == "sqlite":
            return False
        if self.database_url:
            return True
        if os.getenv("DATABASE_BACKEND", "").lower() == "mysql":
            return bool(self.database_url or self.mysql_password)
        return bool(self.mysql_password and self.mysql_database)

    @property
    def mysql_config(self) -> dict:
        if self.database_url:
            return _parse_database_url(self.database_url)
        return {
            "host": self.mysql_host,
            "port": self.mysql_port,
            "user": self.mysql_user,
            "password": self.mysql_password,
            "database": self.mysql_database,
        }

    @property
    def database_backend(self) -> str:
        return "mysql" if self.use_mysql else "sqlite"

    def ensure_storage_dirs(self) -> None:
        if not self.use_mysql:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    @property
    def uploads_on_render_disk(self) -> bool:
        return str(self.upload_dir).startswith("/data")

    @property
    def db_on_render_disk(self) -> bool:
        return str(self.db_path).startswith("/data")

    @property
    def render_free_tier(self) -> bool:
        return getattr(self, "storage_ephemeral", False) or os.getenv(
            "SAC_RENDER_FREE", ""
        ).lower() in ("1", "true", "yes")

    @property
    def persistence_on_render_disk(self) -> bool:
        """Comptes + uploads persistants sur le disque Render /data."""
        if getattr(self, "storage_ephemeral", False):
            return False
        if self.use_mysql:
            return self.uploads_on_render_disk
        return self.db_on_render_disk and self.uploads_on_render_disk


settings = Settings()
settings.storage_ephemeral = False
# Gmail : raccourci — GMAIL_USER + GMAIL_APP_PASSWORD suffisent
if settings.gmail_user and settings.gmail_app_password:
    if not settings.smtp_host:
        settings.smtp_host = "smtp.gmail.com"
    if not settings.smtp_user:
        settings.smtp_user = settings.gmail_user
    if not settings.smtp_pass:
        settings.smtp_pass = settings.gmail_app_password.replace(" ", "")
    if not settings.email_from:
        settings.email_from = settings.gmail_user
    if os.getenv("SMTP_PORT") is None and not settings.smtp_use_ssl:
        settings.smtp_port = 587

if not settings.email_from:
    settings.email_from = "noreply@smartacademy.cd"

if settings.frontend_url and settings.frontend_url.rstrip("/") not in settings.allowed_origins:
    settings.allowed_origins.append(settings.frontend_url.rstrip("/"))

# Render : autoriser les frontends SAC connus si ALLOWED_ORIGINS incomplet
if os.getenv("RENDER", "").lower() == "true":
    for _origin in (
        "https://smart-academy-of-congo-dbfm.onrender.com",
        "https://smart-academy-of-congoat.onrender.com",
        "https://smart-academy-of-congo.onrender.com",
        "https://smart-academy-of-congo-frontend.onrender.com",
    ):
        if _origin not in settings.allowed_origins:
            settings.allowed_origins.append(_origin)


def _render_data_writable() -> bool:
    """Vérifie si le disque /data est monté et accessible en écriture."""
    try:
        data_root = Path("/data")
        data_root.mkdir(parents=True, exist_ok=True)
        probe = data_root / ".sac_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


_on_render = os.getenv("RENDER", "").lower() == "true"
if _on_render and not settings.use_mysql:
    wants_persistent = settings.db_on_render_disk and settings.uploads_on_render_disk
    if wants_persistent and _render_data_writable():
        settings.storage_ephemeral = False
    else:
        settings.db_path = (ROOT / "data" / "sac.db").resolve()
        settings.upload_dir = (ROOT / "uploads").resolve()
        settings.storage_ephemeral = True


def _validate_production_config() -> None:
    if not settings.is_prod:
        return

    on_render = os.getenv("RENDER", "").lower() == "true"

    if settings.use_mysql:
        cfg = settings.mysql_config
        if not cfg.get("host") or not cfg.get("database"):
            raise RuntimeError("Configuration MySQL incomplète (host ou database manquant).")
        if not settings.database_url and not settings.mysql_password:
            raise RuntimeError("MYSQL_PASSWORD ou DATABASE_URL requis en production.")
        if on_render and not settings.uploads_on_render_disk:
            raise RuntimeError(
                "Sur Render, les fichiers uploadés doivent être sur le disque persistant : "
                f"UPLOAD_DIR={settings.upload_dir}. Montez /data et utilisez UPLOAD_DIR=/data/uploads."
            )
    # SQLite sur Render : pas de crash — repli automatique si /data absent (voir ci-dessus)


_validate_production_config()
