import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

# Motifs suspects dans le corps JSON (injection / élévation de privilèges)
_SUSPICIOUS_PATTERNS = re.compile(
    r"(<script|javascript:|onerror=|onload=|__proto__|constructor\[|"
    r"DROP\s+TABLE|UNION\s+SELECT|;\s*--)",
    re.IGNORECASE,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-XSS-Protection"] = "0"
        if settings.is_prod:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        path = request.url.path
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Bloque les requêtes d'écriture depuis une origine non autorisée."""

    WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in self.WRITE_METHODS:
            origin = request.headers.get("origin")
            if origin and origin not in settings.allowed_origins:
                return JSONResponse(
                    status_code=403,
                    content={"error": "CORS_BLOCKED", "message": "Origine non autorisée"},
                )
        return await call_next(request)


class PayloadGuardMiddleware(BaseHTTPMiddleware):
    """Limite la taille et détecte les payloads malveillants."""

    MAX_BODY = 512 * 1024  # 512 Ko (hors multipart géré ailleurs)

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in ("POST", "PUT", "PATCH"):
            ct = request.headers.get("content-type", "")
            if "multipart/form-data" not in ct:
                body = await request.body()
                if len(body) > self.MAX_BODY:
                    return JSONResponse(
                        status_code=413,
                        content={"error": "PAYLOAD_TOO_LARGE"},
                    )
                if body and _SUSPICIOUS_PATTERNS.search(body.decode("utf-8", errors="ignore")):
                    try:
                        from app.services import monitor_sata_service

                        monitor_sata_service.log_sql_injection_attempt(
                            request, body.decode("utf-8", errors="ignore")[:200]
                        )
                    except Exception:
                        pass
                    return JSONResponse(
                        status_code=400,
                        content={"error": "INVALID_PAYLOAD", "message": "Contenu rejeté"},
                    )
                # Réinjecter le corps pour les routes
                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}

                request = Request(request.scope, receive)
        return await call_next(request)
