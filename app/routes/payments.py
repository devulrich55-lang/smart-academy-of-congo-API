from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_current_user, require_roles
from app.services import payment_service

router = APIRouter(prefix="/payments", tags=["payments"])

ERROR_MAP = {
    "FORBIDDEN": (403, "Accès refusé"),
    "NOT_FOUND": (404, "Paiement introuvable"),
    "INVALID_BANK": (400, "Compte bancaire partenaire incomplet"),
    "INVALID_ACCOUNT_NUMBER": (400, "Numéro de compte invalide (minimum 8 chiffres)"),
    "INVALID_AMOUNT": (400, "Montant invalide"),
    "INVALID_METHOD": (400, "Mode de paiement invalide"),
    "INVALID_REFERENCE": (400, "Référence bancaire requise"),
    "INVALID_STATUS": (400, "Statut invalide"),
    "BANK_LOCKED": (409, "Compte verrouillé — demandez une modification via l'administration"),
    "BANK_NOT_CONFIGURED": (400, "Compte bancaire université non configuré"),
    "CHANGE_ALREADY_PENDING": (409, "Une demande de modification est déjà en cours"),
    "CHANGE_LIMIT_REACHED": (409, "Le compte ne peut plus être modifié"),
    "CHANGE_REASON_REQUIRED": (400, "Motif de modification requis (10 caractères minimum)"),
    "INVALID_APPROVAL_SLOT": (400, "Rôle d'approbation invalide"),
    "FORBIDDEN_APPROVAL_ROLE": (403, "Vous n'êtes pas autorisé à approuver ce volet"),
    "NO_PENDING_CHANGE": (404, "Aucune demande de modification en cours"),
    "REQUESTER_CANNOT_APPROVE": (403, "Le demandeur ne peut pas approuver sa propre demande"),
    "ALREADY_APPROVED": (409, "Vous avez déjà approuvé cette demande"),
    "SLOT_ALREADY_APPROVED": (409, "Ce volet est déjà approuvé"),
    "UNIVERSITY_NOT_FOUND": (404, "Université introuvable"),
    "ACCOUNT_SUFFIX_MISMATCH": (400, "Les derniers chiffres du compte ne correspondent pas"),
}


def _map_error(exc: ValueError) -> None:
    code = str(exc)
    if code in ERROR_MAP:
        status, message = ERROR_MAP[code]
        raise HTTPException(status_code=status, detail={"error": code, "message": message})
    raise exc


def _bank_response(universite: str, bank: dict | None, user: dict) -> dict:
    role = user.get("role")
    if role == "etudiant" or role not in ("universite", "admin"):
        bank = payment_service.mask_bank_for_student(bank)
    return {"ok": True, "universite": universite, "bank": bank}


@router.get("/campus-bank")
def campus_bank_route(universite: str, user: dict = Depends(get_current_user)):
    bank = payment_service.get_partner_bank(universite)
    return _bank_response(universite, bank, user)


@router.patch("/campus-bank")
def update_campus_bank_route(body: dict, user: dict = Depends(require_roles("universite"))):
    try:
        data = payment_service.update_partner_bank(user, body)
        return {"ok": True, **data}
    except ValueError as e:
        _map_error(e)


@router.post("/campus-bank/change-request")
def bank_change_request_route(body: dict, user: dict = Depends(require_roles("universite"))):
    try:
        data = payment_service.request_bank_change(user, body)
        return {"ok": True, **data}
    except ValueError as e:
        _map_error(e)


@router.post("/campus-bank/approve")
def bank_change_approve_route(body: dict, user: dict = Depends(get_current_user)):
    try:
        data = payment_service.approve_bank_change(user, body)
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
