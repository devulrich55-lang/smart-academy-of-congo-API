from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.config import settings
from app.deps import get_current_user, require_roles
from app.rate_limit import limiter
from app.services import audit_service, reclamation_service
from app.utils.guards import strip_identity_fields
from app.utils.pagination import clamp_page

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
def list_my_reclamations(
    user: dict = Depends(get_current_user),
    limit: int | None = Query(None, ge=1),
    offset: int | None = Query(None, ge=0),
):
    page_limit, page_offset = clamp_page(
        limit,
        offset,
        default=settings.api_page_default,
        maximum=settings.api_page_max,
    )
    recs = reclamation_service.list_reclamations_for_actor(
        user, page_limit, page_offset
    )
    return {
        "reclamations": recs,
        "pagination": {
            "limit": page_limit,
            "offset": page_offset,
            "hasMore": len(recs) == page_limit,
        },
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
