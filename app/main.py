from contextlib import asynccontextmanager



from fastapi import FastAPI, HTTPException, Request

from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import JSONResponse

from fastapi.staticfiles import StaticFiles

from slowapi import _rate_limit_exceeded_handler

from slowapi.errors import RateLimitExceeded



from app.config import settings

from app.database import get_db

from app.middleware.security import (

    OriginGuardMiddleware,

    PayloadGuardMiddleware,

    SecurityHeadersMiddleware,

)

from app.rate_limit import limiter

from app.routes import auth, documents, nominations, platform, reclamations, sections, tariffs

from app.seed import seed_demo_sections_if_missing, seed_if_empty





@asynccontextmanager

async def lifespan(_app: FastAPI):

    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    get_db()

    seed_if_empty()

    seed_demo_sections_if_missing()

    yield





app = FastAPI(

    title="Smart Academy API",

    version="1.0.0",

    lifespan=lifespan,

)

app.state.limiter = limiter

app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)



app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(OriginGuardMiddleware)

app.add_middleware(PayloadGuardMiddleware)



app.add_middleware(

    CORSMiddleware,

    allow_origins=settings.allowed_origins,

    allow_credentials=True,

    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],

    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],

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
    try:
        get_db().execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "ok": db_ok,
        "service": "Smart Academy API",
        "version": "1.0.0",
        "runtime": "python",
        "database": "up" if db_ok else "down",
    }





app.include_router(auth.router, prefix="/api")

app.include_router(documents.router, prefix="/api")

app.include_router(tariffs.router, prefix="/api")

app.include_router(platform.router, prefix="/api")

app.include_router(sections.router, prefix="/api")

app.include_router(reclamations.router, prefix="/api")

app.include_router(nominations.router, prefix="/api")



settings.upload_dir.mkdir(parents=True, exist_ok=True)
settings.db_path.parent.mkdir(parents=True, exist_ok=True)

app.mount(

    "/uploads",

    StaticFiles(directory=str(settings.upload_dir)),

    name="uploads",

)



if not settings.is_prod and settings.frontend_root.exists():

    app.mount("/", StaticFiles(directory=str(settings.frontend_root), html=True), name="frontend")


