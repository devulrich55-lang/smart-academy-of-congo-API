from fastapi import Cookie, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.user_service import find_user_by_id, user_to_session
from app.utils.tokens import verify_access_token

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    sac_access: str | None = Cookie(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    token = sac_access
    if not token and credentials:
        token = credentials.credentials
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"error": "AUTH_REQUIRED", "message": "Connexion requise"},
        )
    try:
        decoded = verify_access_token(token)
        user = find_user_by_id(decoded["sub"])
        if not user:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "USER_NOT_FOUND",
                    "message": "Session expirée ou compte introuvable — reconnectez-vous.",
                },
            )
        request.state.user = user
        request.state.session = user_to_session(user)
        return user
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail={"error": "TOKEN_EXPIRED", "message": "Session expirée"},
        )


def require_roles(*roles: str):
    def checker(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(
                status_code=403,
                detail={"error": "FORBIDDEN", "message": "Accès refusé"},
            )
        return user

    return checker
