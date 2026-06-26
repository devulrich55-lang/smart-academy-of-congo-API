from fastapi import APIRouter, Depends, HTTPException, Request

from app.deps import get_current_user, get_optional_user, require_roles
from app.rate_limit import limiter
from app.services import mobile_money_service, payment_service

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
    "INVALID_PROVIDER": (400, "Opérateur mobile invalide"),
    "INVALID_PHONE": (400, "Numéro mobile congolais invalide"),
    "INVALID_PIN": (400, "Code PIN invalide"),
    "MOBILE_TX_REQUIRED": (400, "Transaction Mobile Money requise"),
    "PAYMENT_NOT_COMPLETED": (400, "Paiement mobile non confirmé"),
    "INVALID_PURPOSE": (400, "Type de paiement invalide"),
    "PIN_NOT_REQUIRED": (400, "Confirmation PIN non requise pour ce mode"),
    "PROVIDER_OFFLINE": (503, "Service Mobile Money temporairement indisponible"),
    "PROVIDER_DECLINED": (402, "Paiement refusé par l'opérateur"),
    "INVALID_WEBHOOK": (400, "Webhook invalide"),
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


@router.post("/mobile/initiate", status_code=201)
@limiter.limit("15/minute")
def mobile_initiate_route(
    request: Request,
    body: dict,
    user: dict | None = Depends(get_optional_user),
):
    try:
        tx = mobile_money_service.initiate(body, user)
        return {"ok": True, "transaction": tx}
    except ValueError as e:
        _map_error(e)


@router.get("/mobile/{tx_id}")
def mobile_status_route(
    tx_id: str,
    user: dict | None = Depends(get_optional_user),
):
    try:
        return {"ok": True, "transaction": mobile_money_service.get_status(tx_id, user)}
    except ValueError as e:
        _map_error(e)


@router.post("/mobile/{tx_id}/confirm")
@limiter.limit("20/minute")
def mobile_confirm_route(
    request: Request,
    tx_id: str,
    body: dict,
    user: dict | None = Depends(get_optional_user),
):
    try:
        tx = mobile_money_service.confirm_pin(tx_id, body.get("pin") or "", user)
        return {"ok": True, "transaction": tx}
    except ValueError as e:
        _map_error(e)


@router.post("/mobile/webhook")
async def mobile_webhook_route(request: Request):
    raw = await request.body()
    signature = request.headers.get("X-Signature") or request.headers.get("X-Hub-Signature") or ""
    if not mobile_money_service.verify_webhook_signature(raw, signature):
        raise HTTPException(status_code=401, detail={"error": "INVALID_SIGNATURE"})
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "INVALID_WEBHOOK"})
    try:
        tx = mobile_money_service.handle_webhook(payload if isinstance(payload, dict) else {})
        return {"ok": True, "transaction": tx}
    except ValueError as e:
        _map_error(e)
