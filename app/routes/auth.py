from fastapi import APIRouter, Cookie, Depends, Request, Response

from app.config import settings
from app.deps import get_current_user
from app.rate_limit import limiter
from app.services import auth_service
from app.services.password_reset_service import request_password_reset, reset_password
from app.services.user_service import create_user, find_user_by_id, user_to_session
from app.utils.guards import strip_identity_fields
from app.utils.sanitize import validate_email_strict, validate_password

router = APIRouter(prefix="/auth", tags=["auth"])

ERROR_MAP = {
    "INVALID_CREDENTIALS": (401, "Identifiant ou mot de passe incorrect"),
    "ROLE_MISMATCH": (403, "Rôle incorrect pour ce compte"),
    "ACCOUNT_LOCKED": (423, "Compte temporairement verrouillé. Réessayez dans 15 minutes."),
    "EMAIL_EXISTS": (409, "Cet e-mail est déjà inscrit"),
    "PHONE_EXISTS": (409, "Ce numéro de téléphone est déjà lié à un compte"),
    "IDENTITY_CONFLICT": (409, "Cette identité est déjà enregistrée. Une seule inscription par personne"),
    "MULTI_ROLE": (403, "Un seul rôle par personne (pas de double compte étudiant / professeur / assistant)"),
    "INVALID_PHONE": (400, "Numéro de téléphone mobile congolais invalide (ex. 085 184 8859)"),
    "UNIVERSITY_MISMATCH": (403, "Université incorrecte : utilisez celle choisie à l'inscription"),
    "CODE_UNI_MISMATCH": (403, "Code établissement incorrect"),
    "INVALID_PROFILE": (400, "Profil invalide ou informations non fiables"),
    "INVALID_REFRESH": (401, "Session expirée, reconnectez-vous"),
    "INVALID_RESET_TOKEN": (400, "Lien de réinitialisation invalide ou expiré"),
    "INVALID_PASSWORD": (400, "Mot de passe invalide (8+ caractères, lettre + chiffre, sans espace)"),
}


def _set_auth_cookies(response: Response, access_token: str, refresh_raw: str) -> None:
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax" if not settings.is_prod else "strict",
        "path": "/",
    }
    response.set_cookie("sac_access", access_token, max_age=15 * 60, **common)
    response.set_cookie("sac_refresh", refresh_raw, max_age=7 * 24 * 60 * 60, **common)


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie("sac_access", path="/")
    response.delete_cookie("sac_refresh", path="/")


def _map_error(exc: ValueError):
    from fastapi import HTTPException

    code = str(exc)
    if code in ERROR_MAP:
        status, message = ERROR_MAP[code]
        raise HTTPException(status_code=status, detail={"error": code, "message": message})
    raise exc


@router.post("/login")
@limiter.limit("8/minute")
def login_route(request: Request, body: dict, response: Response):
    identifier = body.get("identifier")
    password = body.get("password")
    if not identifier or not password:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail={"error": "MISSING_FIELDS"})
    try:
        result = auth_service.login(
            str(identifier).strip(),
            password,
            body.get("role"),
            {"universite": body.get("universite"), "codeUni": body.get("codeUni")},
        )
        _set_auth_cookies(response, result["accessToken"], result["refreshRaw"])
        return {"ok": True, "session": result["session"]}
    except ValueError as e:
        _map_error(e)


@router.post("/register", status_code=201)
@limiter.limit("5/hour")
def register_route(request: Request, body: dict, response: Response):
    email = validate_email_strict(body.get("email"))
    if not email or not validate_password(body.get("password")):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_INPUT",
                "message": "E-mail réel requis et mot de passe (8+ caractères, lettre + chiffre, sans espace)",
            },
        )
    if not body.get("telephone"):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_PHONE", "message": "Numéro de téléphone mobile requis"},
        )
    try:
        profile = strip_identity_fields(body)
        profile["email"] = email
        profile["password"] = body["password"]
        profile["role"] = body.get("role")
        profile["universite"] = body.get("universite")
        profile["codeUni"] = body.get("codeUni")
        user = create_user(profile)
        tokens = auth_service.issue_tokens(user)
        _set_auth_cookies(response, tokens["accessToken"], tokens["refreshRaw"])
        return {"ok": True, "session": tokens["session"]}
    except ValueError as e:
        _map_error(e)


@router.post("/refresh")
@limiter.limit("20/minute")
def refresh_route(
    request: Request, response: Response, sac_refresh: str | None = Cookie(default=None)
):
    try:
        result = auth_service.refresh_session(sac_refresh)
        _set_auth_cookies(response, result["accessToken"], result["refreshRaw"])
        return {"ok": True, "session": result["session"]}
    except ValueError as e:
        _clear_auth_cookies(response)
        _map_error(e)


@router.post("/logout")
def logout_route(response: Response, sac_refresh: str | None = Cookie(default=None)):
    auth_service.logout(sac_refresh)
    _clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me")
def me_route(user: dict = Depends(get_current_user)):
    return {"session": user_to_session(find_user_by_id(user["id"]))}


@router.post("/forgot-password")
@limiter.limit("3/hour")
def forgot_password_route(request: Request, body: dict):
    email = body.get("email")
    if not email:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail={"error": "MISSING_EMAIL", "message": "Adresse e-mail requise"},
        )
    request_password_reset(str(email).strip())
    return {
        "ok": True,
        "message": "Si un compte existe avec cet e-mail, un lien de réinitialisation a été envoyé.",
    }


@router.post("/reset-password")
@limiter.limit("10/hour")
def reset_password_route(request: Request, body: dict):
    token = body.get("token")
    password = body.get("password")
    if not token or not password:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail={"error": "MISSING_FIELDS", "message": "Token et nouveau mot de passe requis"},
        )
    try:
        reset_password(str(token).strip(), password)
        return {"ok": True, "message": "Mot de passe mis à jour. Vous pouvez vous connecter."}
    except ValueError as e:
        _map_error(e)
