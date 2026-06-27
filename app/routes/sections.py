from fastapi import APIRouter, Depends, HTTPException, Request

from app.deps import get_current_user, require_roles
from app.rate_limit import limiter
from app.services import audit_service, reclamation_service
from app.services.user_service import (
    _is_section_head_actor,
    create_section_head_account,
    create_student_for_section,
    find_user_by_email,
    link_student_to_section,
    list_students_for_section,
    list_pending_students_for_section,
    set_student_section_approval,
    user_to_session,
)
from app.utils.guards import strip_identity_fields
from app.utils.sanitize import validate_email_strict, validate_password

router = APIRouter(prefix="/sections", tags=["sections"])

ERROR_MAP = {
    "FORBIDDEN": (403, "Accès refusé"),
    "INVALID_PROFILE": (400, "Profil invalide ou informations manquantes"),
    "INVALID_PASSWORD": (400, "Mot de passe invalide (8+ caractères, lettre + chiffre, sans espace)"),
    "EMAIL_EXISTS": (409, "Cet e-mail est déjà inscrit"),
    "PHONE_EXISTS": (409, "Ce numéro de téléphone est déjà lié à un compte"),
    "IDENTITY_CONFLICT": (409, "Cette identité est déjà enregistrée"),
    "MULTI_ROLE": (403, "Un seul rôle par personne"),
    "INVALID_PHONE": (400, "Numéro de téléphone mobile congolais invalide"),
    "UNIVERSITY_MISMATCH": (403, "Université incorrecte"),
    "NOT_FOUND": (404, "Section introuvable"),
    "STUDENT_NOT_FOUND": (
        404,
        "Étudiant introuvable sur le serveur — il doit d'abord terminer son inscription en ligne.",
    ),
    "INVALID_INPUT": (400, "Nom de section et filière requis"),
}


def _map_error(exc: ValueError):
    code = str(exc)
    if code in ERROR_MAP:
        status, message = ERROR_MAP[code]
        raise HTTPException(status_code=status, detail={"error": code, "message": message})
    raise exc


@router.get("")
def list_sections_route(user: dict = Depends(get_current_user)):
    return {"sections": reclamation_service.list_sections_for_actor(user)}


@router.post("", status_code=201)
@limiter.limit("40/hour")
def upsert_section_route(
    request: Request, body: dict, user: dict = Depends(require_roles("universite"))
):
    try:
        section = reclamation_service.upsert_section(user, strip_identity_fields(body))
        audit_service.log_audit(
            request,
            "upsert_section",
            "section",
            resource_id=section.get("id"),
            universite=section.get("universite"),
        )
        return {"section": section}
    except ValueError as e:
        _map_error(e)


@router.patch("/{section_id}")
@limiter.limit("40/hour")
def update_section_route(
    section_id: str,
    request: Request,
    body: dict,
    user: dict = Depends(require_roles("universite")),
):
    try:
        section = reclamation_service.update_section(
            user, section_id, strip_identity_fields(body)
        )
        audit_service.log_audit(
            request,
            "update_section",
            "section",
            resource_id=section_id,
            universite=section.get("universite"),
        )
        return {"section": section}
    except ValueError as e:
        _map_error(e)


@router.post("/head-account", status_code=201)
@limiter.limit("20/hour")
def create_head_account_route(request: Request, body: dict, user: dict = Depends(require_roles("universite"))):
    email = validate_email_strict(body.get("email"))
    if not email or not validate_password(body.get("password")):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_INPUT",
                "message": "E-mail réel et mot de passe valide requis pour le chef de section",
            },
        )
    if not body.get("sectionId"):
        raise HTTPException(
            status_code=400,
            detail={"error": "MISSING_SECTION", "message": "Identifiant de section requis"},
        )
    try:
        profile = {
            "email": email,
            "password": body["password"],
            "telephone": body.get("telephone"),
            "prenom": body.get("prenom"),
            "nom": body.get("nom"),
            "filiere": body.get("filiere"),
            "sectionId": body.get("sectionId"),
        }
        created = create_section_head_account(user, profile)
        return {"ok": True, "user": user_to_session(created)}
    except ValueError as e:
        _map_error(e)


def _require_student_delegate(user: dict = Depends(get_current_user)) -> dict:
    role = user.get("role")
    if role in ("section", "universite"):
        return user
    if role == "professeur" and _is_section_head_actor(user):
        return user
    raise HTTPException(status_code=403, detail={"error": "FORBIDDEN", "message": "Accès refusé"})


@router.post("/students", status_code=201)
@limiter.limit("30/hour")
def create_student_route(
    request: Request, body: dict, user: dict = Depends(_require_student_delegate)
):
    email = validate_email_strict(body.get("email"))
    if not email:
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_INPUT", "message": "E-mail réel requis"},
        )

    existing = find_user_by_email(email)
    if existing and existing.get("role") == "etudiant":
        try:
            profile = {
                "email": email,
                "filiere": body.get("filiere"),
                "universite": body.get("universite"),
                "sectionId": body.get("sectionId"),
                "niveau": body.get("niveau"),
                "classe": body.get("classe"),
            }
            linked = link_student_to_section(user, email, profile)
            audit_service.log_audit(
                request,
                "link_student_section",
                "user",
                resource_id=linked.get("id"),
                universite=user.get("universite"),
            )
            return {"ok": True, "user": user_to_session(linked), "linked": True}
        except ValueError as e:
            _map_error(e)

    if not validate_password(body.get("password")):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_INPUT",
                "message": "E-mail réel et mot de passe valide requis",
            },
        )
    if not body.get("telephone") or not body.get("prenom") or not body.get("nom"):
        raise HTTPException(
            status_code=400,
            detail={"error": "MISSING_FIELDS", "message": "Prénom, nom et téléphone requis"},
        )
    try:
        profile = {
            "email": email,
            "password": body["password"],
            "telephone": body.get("telephone"),
            "prenom": body.get("prenom"),
            "nom": body.get("nom"),
            "matricule": body.get("matricule"),
            "niveau": body.get("niveau"),
            "classe": body.get("classe"),
            "dateNaissance": body.get("dateNaissance"),
            "filiere": body.get("filiere"),
            "sectionId": body.get("sectionId"),
            "universite": body.get("universite"),
        }
        created = create_student_for_section(user, profile)
        return {"ok": True, "user": user_to_session(created)}
    except ValueError as e:
        _map_error(e)


@router.get("/students/pending")
def list_pending_students_route(user: dict = Depends(_require_student_delegate)):
    students = list_pending_students_for_section(user)
    return {
        "students": [user_to_session(s) for s in students if s],
    }


@router.get("/students")
def list_students_route(user: dict = Depends(_require_student_delegate)):
    students = list_students_for_section(user)
    return {
        "students": [user_to_session(s) for s in students if s],
    }


@router.patch("/students/{student_email}/link")
@limiter.limit("60/hour")
def link_student_route(
    student_email: str,
    request: Request,
    body: dict,
    user: dict = Depends(_require_student_delegate),
):
    try:
        updated = link_student_to_section(user, student_email, body or {})
        audit_service.log_audit(
            request,
            "link_student_section",
            "user",
            resource_id=updated.get("id"),
            universite=user.get("universite"),
        )
        return {"ok": True, "user": user_to_session(updated)}
    except ValueError as e:
        _map_error(e)


@router.patch("/students/{student_email}/approval")
@limiter.limit("60/hour")
def approve_student_route(
    student_email: str,
    request: Request,
    body: dict,
    user: dict = Depends(_require_student_delegate),
):
    status = str(body.get("status") or "approved").strip()
    if status == "confirmed":
        status = "approved"
    reason = str(body.get("reason") or "").strip()
    try:
        updated = set_student_section_approval(user, student_email, status, reason)
        audit_service.log_audit(
            request,
            "approve_student_section",
            "user",
            resource_id=updated.get("id"),
            universite=user.get("universite"),
        )
        return {"ok": True, "user": user_to_session(updated)}
    except ValueError as e:
        code = str(e)
        if code == "INVALID_STATUS":
            raise HTTPException(
                status_code=400,
                detail={"error": code, "message": "Statut de validation invalide"},
            )
        _map_error(e)
