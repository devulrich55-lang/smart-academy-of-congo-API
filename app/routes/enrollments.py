from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_current_user, require_roles
from app.services import enrollment_renewal_service
from app.services.user_service import find_user_by_id

router = APIRouter(prefix="/enrollments", tags=["enrollments"])

ERROR_MAP = {
    "NOT_APPLICABLE": (400, "Renouvellement non requis pour ce compte"),
    "NOT_ONBOARDED": (403, "Inscription initiale non validée"),
    "ALREADY_RENEWED": (409, "Inscription déjà renouvelée pour cette année"),
    "FORBIDDEN": (403, "Accès refusé"),
    "NOT_FOUND": (404, "Renouvellement introuvable"),
}


def _map_error(exc: ValueError) -> None:
    code = str(exc)
    if code in ERROR_MAP:
        status, message = ERROR_MAP[code]
        raise HTTPException(status_code=status, detail={"error": code, "message": message})
    raise exc


@router.get("/renewal/status")
def renewal_status_route(user: dict = Depends(get_current_user)):
    full = find_user_by_id(user["id"]) or user
    return {"ok": True, "renewal": enrollment_renewal_service.get_renewal_status(full)}


@router.post("/renewal", status_code=201)
def submit_renewal_route(body: dict, user: dict = Depends(get_current_user)):
    try:
        full = find_user_by_id(user["id"]) or user
        payment = body.get("payment") if isinstance(body, dict) else None
        if not payment:
            raise HTTPException(
                status_code=400,
                detail={"error": "MISSING_PAYMENT", "message": "Détails de paiement requis"},
            )
        return enrollment_renewal_service.submit_renewal_payment(full, payment)
    except ValueError as e:
        _map_error(e)


@router.post("/renewal/{enrollment_id}/verify")
def verify_renewal_route(enrollment_id: str, user: dict = Depends(get_current_user)):
    try:
        return enrollment_renewal_service.verify_renewal_payment(user, enrollment_id)
    except ValueError as e:
        _map_error(e)


@router.get("/renewal/pending")
def pending_renewals_route(user: dict = Depends(require_roles("assistant", "universite", "section", "superadmin"))):
    try:
        return {
            "ok": True,
            "renewals": enrollment_renewal_service.list_pending_renewals(user),
        }
    except ValueError as e:
        _map_error(e)
