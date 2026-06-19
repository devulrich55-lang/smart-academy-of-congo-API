from contextlib import asynccontextmanager



from fastapi import FastAPI, HTTPException, Request

from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from fastapi.staticfiles import StaticFiles

from slowapi import _rate_limit_exceeded_handler

from slowapi.errors import RateLimitExceeded



from app.config import settings

from app.database import get_db
from app.database_maintenance import run_maintenance
from app.services.email_service import smtp_configured

from app.middleware.security import (

    OriginGuardMiddleware,

    PayloadGuardMiddleware,

    SecurityHeadersMiddleware,

)

from app.rate_limit import limiter

from app.routes import admin, auth, documents, nominations, platform, reclamations, sections, tariffs

from app.seed import seed_demo_sections_if_missing, seed_if_empty





@asynccontextmanager

async def lifespan(_app: FastAPI):
    settings.ensure_storage_dirs()
    db = get_db()
    user_count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    doc_count = db.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"]
    print(
        f"[SAC] DB={settings.database_backend} db={settings.db_path} uploads={settings.upload_dir} "
        f"persistent={settings.persistence_on_render_disk} ephemeral={settings.storage_ephemeral} "
        f"users={user_count} docs={doc_count}"
    )
    seed_if_empty()
    seed_demo_sections_if_missing()
    try:
        maint = run_maintenance()
        if maint["refreshTokensPurged"] or maint["resetTokensPurged"]:
            print(f"[SAC] Maintenance: {maint}")
    except Exception as exc:
        print(f"[SAC] Maintenance skipped: {exc}")
    yield





app = FastAPI(
    title="Smart Academy API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.is_prod else "/docs",
    redoc_url=None if settings.is_prod else "/redoc",
    openapi_url=None if settings.is_prod else "/openapi.json",
)

app.state.limiter = limiter

app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)



app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(OriginGuardMiddleware)

app.add_middleware(PayloadGuardMiddleware)



app.add_middleware(

    CORSMiddleware,

    allow_origins=settings.allowed_origins,

    allow_credentials=True,

    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],

    allow_headers=["*"],

)





@app.exception_handler(HTTPException)

async def http_exception_handler(_request: Request, exc: HTTPException):

    if isinstance(exc.detail, dict):

        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})





@app.get("/api/health")

@limiter.limit("30/minute")

def health(request: Request):
    db_ok = False
    user_count = 0
    doc_count = 0
    request_origin = (request.headers.get("origin") or "").strip().rstrip("/")
    origin_allowed = (
        not request_origin or request_origin in settings.allowed_origins
    )
    try:
        db = get_db()
        db.execute("SELECT 1").fetchone()
        db_ok = True
        user_count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        doc_count = db.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"]
    except Exception:
        db_ok = False
    if settings.is_prod:
        return {
            "ok": db_ok,
            "service": "Smart Academy API",
            "version": "1.0.0",
            "database": "up" if db_ok else "down",
            "storage": {
                "persistentOnRenderDisk": settings.persistence_on_render_disk,
                "emailConfigured": smtp_configured(),
            },
            "cors": {
                "requestOrigin": request_origin or None,
                "originAllowed": origin_allowed,
                "configuredOrigins": len(settings.allowed_origins),
            },
        }

    return {
        "ok": db_ok,
        "service": "Smart Academy API",
        "version": "1.0.0",
        "runtime": "python",
        "database": "up" if db_ok else "down",
        "storage": {
            "backend": settings.database_backend,
            "mode": (
                "mysql"
                if settings.use_mysql
                else ("sqlite-ephemeral" if settings.storage_ephemeral else "sqlite-persistent")
            ),
            "mysqlHost": settings.mysql_config.get("host") if settings.use_mysql else None,
            "mysqlDatabase": settings.mysql_config.get("database") if settings.use_mysql else None,
            "databasePath": str(settings.db_path) if not settings.use_mysql else None,
            "uploadDir": str(settings.upload_dir),
            "dbOnRenderDisk": settings.db_on_render_disk,
            "uploadsOnRenderDisk": settings.uploads_on_render_disk,
            "persistentOnRenderDisk": settings.persistence_on_render_disk,
            "emailConfigured": smtp_configured(),
            "userCount": user_count,
            "documentCount": doc_count,
        },
    }





app.include_router(auth.router, prefix="/api")

app.include_router(documents.router, prefix="/api")

app.include_router(tariffs.router, prefix="/api")

app.include_router(platform.router, prefix="/api")

app.include_router(sections.router, prefix="/api")

app.include_router(reclamations.router, prefix="/api")

app.include_router(nominations.router, prefix="/api")

app.include_router(admin.router, prefix="/api")



settings.ensure_storage_dirs()

app.mount(

    "/uploads",

    StaticFiles(directory=str(settings.upload_dir)),

    name="uploads",

)



if not settings.is_prod and settings.frontend_root.exists():

    app.mount("/", StaticFiles(directory=str(settings.frontend_root), html=True), name="frontend")


