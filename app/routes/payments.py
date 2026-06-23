from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_current_user, require_roles
from app.services import payment_service

router = APIRouter(prefix="/payments", tags=["payments"])

ERROR_MAP = {
    "FORBIDDEN": (403, "Accès refusé"),
    "NOT_FOUND": (404, "Paiement introuvable"),
    "INVALID_BANK": (400, "Compte bancaire partenaire incomplet"),
    "INVALID_AMOUNT": (400, "Montant invalide"),
    "INVALID_METHOD": (400, "Mode de paiement invalide"),
    "INVALID_REFERENCE": (400, "Référence bancaire requise"),
    "INVALID_STATUS": (400, "Statut invalide"),
}


def _map_error(exc: ValueError) -> None:
    code = str(exc)
    if code in ERROR_MAP:
        status, message = ERROR_MAP[code]
        raise HTTPException(status_code=status, detail={"error": code, "message": message})
    raise exc


@router.get("/campus-bank")
def campus_bank_route(universite: str, user: dict = Depends(get_current_user)):
    del user
    bank = payment_service.get_partner_bank(universite)
    return {"ok": True, "universite": universite, "bank": bank}


@router.patch("/campus-bank")
def update_campus_bank_route(body: dict, user: dict = Depends(require_roles("universite"))):
    try:
        data = payment_service.update_partner_bank(user, body)
        return {"ok": True, **data}
    except ValueError as e:
        _map_error(e)


@router.get("/me")
def my_payments_route(user: dict = Depends(require_roles("etudiant"))):
    try:
        return {"payments": payment_service.list_payments_for_student(user)}
    except ValueError as e:
        _map_error(e)


@router.post("/academic", status_code=201)
def create_payment_route(body: dict, user: dict = Depends(require_roles("etudiant"))):
    try:
        payment = payment_service.create_academic_payment(user, body)
        return {"ok": True, "payment": payment}
    except ValueError as e:
        _map_error(e)


@router.get("/campus")
def campus_payments_route(user: dict = Depends(require_roles("universite"))):
    try:
        return {"payments": payment_service.list_payments_for_campus(user)}
    except ValueError as e:
        _map_error(e)


@router.patch("/{payment_id}")
def update_payment_route(
    payment_id: str, body: dict, user: dict = Depends(require_roles("universite"))
):
    try:
        payment = payment_service.update_payment_status(user, payment_id, body)
        return {"ok": True, "payment": payment}
    except ValueError as e:
        _map_error(e)
