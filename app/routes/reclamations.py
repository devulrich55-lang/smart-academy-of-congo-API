from fastapi import APIRouter, Depends, HTTPException, Request

from app.deps import get_current_user, require_roles
from app.rate_limit import limiter
from app.services import audit_service, reclamation_service
from app.utils.guards import strip_identity_fields

router = APIRouter(prefix="/reclamations", tags=["reclamations"])

ERROR_MAP = {
    "FORBIDDEN": (403, "Accès refusé"),
    "NOT_FOUND": (404, "Réclamation introuvable"),
    "INVALID_INPUT": (400, "Données invalides"),
    "NO_SECTION": (
        400,
        "Aucune section de votre faculté n'est enregistrée. Contactez l'administration.",
    ),
    "ATTACHMENT_TOO_LARGE": (400, "Pièce jointe trop volumineuse (max ~800 Ko)"),
    "INVALID_ATTACHMENT": (400, "Pièce jointe invalide"),
}


def _handle_error(exc: ValueError) -> None:
    code = str(exc)
    if code in ERROR_MAP:
        status, message = ERROR_MAP[code]
        raise HTTPException(status_code=status, detail={"error": code, "message": message})
    raise exc


@router.get("/me")
def list_my_reclamations(user: dict = Depends(get_current_user)):
    return {
        "reclamations": reclamation_service.list_reclamations_for_actor(user)
    }


@router.post("", status_code=201)
@limiter.limit("30/hour")
def create_reclamation_route(
    request: Request,
    body: dict,
    user: dict = Depends(require_roles("etudiant")),
):
    try:
        rec = reclamation_service.create_reclamation(
            user, strip_identity_fields(body)
        )
        audit_service.log_audit(
            request,
            "create_reclamation",
            "reclamation",
            resource_id=rec.get("id"),
            universite=user.get("universite"),
        )
        return {"reclamation": rec}
    except ValueError as e:
        _handle_error(e)


@router.patch("/{rec_id}")
@limiter.limit("60/hour")
def update_reclamation_route(
    rec_id: str,
    request: Request,
    body: dict,
    user: dict = Depends(get_current_user),
):
    try:
        rec = reclamation_service.update_reclamation(
            user, rec_id, strip_identity_fields(body)
        )
        audit_service.log_audit(
            request,
            "update_reclamation",
            "reclamation",
            resource_id=rec_id,
            universite=rec.get("universite"),
        )
        return {"reclamation": rec}
    except ValueError as e:
        _handle_error(e)
