"""Middleware Attack Shield — scoring pré-handler."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.services import attack_shield_service


class AttackShieldMiddleware(BaseHTTPMiddleware):
    SKIP_PREFIXES = ("/uploads", "/docs", "/redoc", "/openapi.json")

    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.attack_shield_enabled:
            return await call_next(request)

        path = request.url.path or ""
        if path.endswith("/health") or "/health" in path:
            return await call_next(request)
        if any(path.startswith(p) for p in self.SKIP_PREFIXES):
            return await call_next(request)
        if "/admin/tech-manager/shield" in path:
            return await call_next(request)

        if not path.startswith("/api/"):
            if attack_shield_service.is_honeypot_path(path):
                score, reasons, action = (
                    95,
                    ["honeypot_path"],
                    attack_shield_service.ACTION_HONEYPOT,
                )
                attack_shield_service.log_event(request, score, reasons, action)
                content, media_type, status = attack_shield_service.honeypot_response(path)
                return Response(
                    content=content,
                    status_code=status,
                    media_type=media_type,
                    headers={"X-Attack-Shield": "honeypot", "Cache-Control": "no-store"},
                )
            return await call_next(request)

        body_preview = ""
        if request.method in ("POST", "PUT", "PATCH"):
            ct = request.headers.get("content-type", "")
            if "multipart/form-data" not in ct:
                body = await request.body()
                if body:
                    body_preview = body.decode("utf-8", errors="ignore")[:300]

                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}

                request = Request(request.scope, receive)

        score, reasons, action = attack_shield_service.score_request(
            request, body_preview=body_preview
        )

        if action == attack_shield_service.ACTION_HONEYPOT:
            attack_shield_service.log_event(
                request, score, reasons, action, body_preview=body_preview
            )
            content, media_type, status = attack_shield_service.honeypot_response(path)
            return Response(
                content=content,
                status_code=status,
                media_type=media_type,
                headers={"X-Attack-Shield": "honeypot", "Cache-Control": "no-store"},
            )

        if action == attack_shield_service.ACTION_BLOCK:
            attack_shield_service.log_event(
                request, score, reasons, action, body_preview=body_preview
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "IP_BLOCKED",
                    "message": "Accès bloqué par le bouclier anti-attaque",
                    "score": score,
                },
                headers={"X-Attack-Shield": "block", "Retry-After": "3600"},
            )

        if action == attack_shield_service.ACTION_THROTTLE:
            attack_shield_service.log_event(
                request, score, reasons, action, body_preview=body_preview
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "THROTTLED",
                    "message": "Trafic suspect — ralenti par le bouclier",
                    "score": score,
                },
                headers={"X-Attack-Shield": "throttle", "Retry-After": "30"},
            )

        if score >= 20:
            attack_shield_service.log_event(
                request, score, reasons, action, body_preview=body_preview
            )

        return await call_next(request)
