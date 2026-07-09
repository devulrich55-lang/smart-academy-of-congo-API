"""EvoDigitalBooks — routes API plateforme."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_current_user, get_optional_user, require_roles
from app.services import edb_service

router = APIRouter(prefix="/platform/edb", tags=["evodigitalbooks"])

ERROR_MAP = {
    "EMAIL_EXISTS": (409, "Cet e-mail est déjà utilisé"),
    "INVALID_EMAIL": (400, "E-mail invalide"),
    "INVALID_PASSWORD": (400, "Mot de passe invalide (8+ caractères, lettre + chiffre)"),
    "INVALID_PHONE": (400, "Numéro Mobile Money invalide"),
    "INVALID_PROFILE": (400, "Profil auteur invalide"),
    "AUTHOR_NOT_FOUND": (404, "Auteur introuvable"),
    "INVALID_STATUS": (400, "Statut invalide"),
    "INVALID_PAYLOAD": (400, "Données d'achat incomplètes"),
    "device_limit": (403, "Limite de 3 appareils atteinte"),
    "IDENTITY_CONFLICT": (409, "Cet e-mail est lié à un autre type de compte"),
}


def _map_error(exc: ValueError) -> None:
    code = str(exc)
    if code in ERROR_MAP:
        status, message = ERROR_MAP[code]
        raise HTTPException(status_code=status, detail={"error": code, "message": message})
    if code == "DEVICE_LIMIT":
        raise HTTPException(
            status_code=403,
            detail={"error": "DEVICE_LIMIT", "message": "Limite de 3 appareils atteinte", "max": 3},
        )
    raise exc


@router.post("/authors/register")
def register_edb_author_route(body: dict):
    try:
        author = edb_service.register_author(
            email=str(body.get("email") or ""),
            password=str(body.get("password") or ""),
            pen_name=str(body.get("penName") or body.get("pen_name") or ""),
            mobile_money=str(body.get("mobileMoney") or body.get("mobile_money") or ""),
            bio=str(body.get("bio") or ""),
        )
        return {"ok": True, "author": author}
    except ValueError as exc:
        _map_error(exc)


@router.get("/authors/pending")
def list_pending_edb_authors_route(user: dict = Depends(require_roles("superadmin"))):
    del user
    return {"ok": True, "authors": edb_service.list_pending_authors()}


@router.patch("/authors/{email}/status")
def set_edb_author_status_route(
    email: str,
    body: dict,
    user: dict = Depends(require_roles("superadmin")),
):
    try:
        status = str(body.get("status") or "").strip().lower()
        reviewer = user.get("email") or user.get("identifiant") or ""
        author = edb_service.set_author_status(email, status, reviewer)
        return {"ok": True, "author": author}
    except ValueError as exc:
        _map_error(exc)


@router.post("/purchases")
def record_edb_purchase_route(
    body: dict,
    user: dict | None = Depends(get_optional_user),
):
    try:
        payload = dict(body or {})
        if user and not payload.get("email"):
            payload["email"] = user.get("email")
        result = edb_service.record_purchase(payload)
        return {"ok": True, **result}
    except ValueError as exc:
        code = str(exc)
        if code == "device_limit":
            raise HTTPException(
                status_code=403,
                detail={"error": "DEVICE_LIMIT", "message": "Limite de 3 appareils atteinte", "max": 3},
            )
        _map_error(exc)


@router.get("/purchases/me")
def my_edb_purchases_route(user: dict = Depends(get_current_user)):
    email = user.get("email") or ""
    return {"ok": True, "bookIds": edb_service.list_purchased_book_ids(email)}
