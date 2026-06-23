from fastapi import APIRouter, Depends, HTTPException

from app.deps import require_roles
from app.services import tariff_service

router = APIRouter(prefix="/tariffs", tags=["tariffs"])

ERROR_MAP = {
    "INVALID_TARIFF_AMOUNT": (400, "Montant invalide (entre 0,50 et 500 USD)"),
    "INVALID_TARIFFS": (400, "Tarifs invalides"),
    "INVALID_ACADEMIC_FEE_AMOUNT": (400, "Montant académique invalide (entre 1 et 50 000 USD)"),
    "INVALID_ACADEMIC_FEES": (400, "Frais académiques invalides"),
    "INVALID_EXCHANGE_RATE": (400, "Taux de change invalide (entre 500 et 50 000 CDF / USD)"),
    "FORBIDDEN": (403, "Accès réservé aux universités partenaires"),
}


@router.get("")
def get_tariffs(universite: str = "", role: str = ""):
    uni = universite.strip()
    if not uni:
        raise HTTPException(status_code=400, detail={"error": "MISSING_UNIVERSITE"})
    if role.strip():
        fee = tariff_service.get_campus_fee(uni, role.strip())
        return {"ok": True, "universite": uni, "role": role.strip(), "fee": fee}
    pack = tariff_service.get_campus_tariffs_for_university(uni)
    return {"ok": True, **pack}


@router.get("/platform")
def get_platform_tariffs_route():
    data = tariff_service.get_platform_tariffs()
    return {"ok": True, **data}


@router.patch("/platform")
def update_platform_tariffs_route(
    body: dict, user: dict = Depends(require_roles("superadmin"))
):
    try:
        data = tariff_service.update_platform_tariffs(user, body)
        return {"ok": True, **data}
    except ValueError as e:
        code = str(e)
        if code in ERROR_MAP:
            status, message = ERROR_MAP[code]
            raise HTTPException(
                status_code=status, detail={"error": code, "message": message}
            )
        raise


@router.patch("/campus")
def update_campus_tariffs(body: dict, user: dict = Depends(require_roles("universite"))):
    try:
        if body.get("academicFees"):
            pack = tariff_service.update_university_academic_fees(
                user["id"], body["academicFees"]
            )
            return {"ok": True, **pack, "membersUpdated": pack.get("membersUpdated", 0)}
        partial = tariff_service.validate_tariffs_payload(
            body.get("tariffs") or body
        )
        pack = tariff_service.update_university_campus_tariffs(user["id"], partial)
        return {"ok": True, **pack, "membersUpdated": pack.get("membersUpdated", 0)}
    except ValueError as e:
        code = str(e)
        if code in ERROR_MAP:
            status, message = ERROR_MAP[code]
            raise HTTPException(
                status_code=status, detail={"error": code, "message": message}
            )
        raise
