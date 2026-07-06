from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.deps import require_roles
from app.rate_limit import limiter
from app.services import audit_service
from app.services.platform_service import platform_presence_global_summary
from app.services.user_service import (
    campus_accounts_summary,
    create_institutional_admin,
    delete_campus_account,
    delete_institutional_admin,
    institutional_admins_summary,
    list_campus_accounts,
    list_institutional_admins,
    list_platform_accounts,
    list_students_for_platform_approval,
    platform_accounts_summary,
    delete_platform_account,
    seed_faculty_sections_for_campus,
    set_student_section_approval,
    user_to_session,
)
from app.services.audit_service import activities_summary, delete_activities, list_activities

router = APIRouter(prefix="/admin", tags=["admin"])

ERROR_MAP = {
    "FORBIDDEN": (403, "Accès refusé"),
    "NOT_FOUND": (404, "Compte introuvable"),
    "INVALID_INPUT": (400, "E-mail invalide"),
    "INVALID_PROFILE": (400, "Profil invalide"),
    "INVALID_COUNTRY": (400, "Pays partenaire invalide ou manquant"),
    "INVALID_EMAIL": (400, "E-mail institutionnel invalide — évitez les adresses jetables ou génériques bloquées."),
    "MINISTRY_COUNTRY_EXISTS": (
        409,
        "Un compte Ministère existe déjà pour ce pays mais est introuvable — contactez le support.",
    ),
    "UNIVERSITY_CAMPUS_EXISTS": (
        409,
        "Un administrateur existe déjà pour cet établissement. Supprimez-le dans la liste avant d'en créer un nouveau.",
    ),
    "INVALID_PASSWORD": (400, "Mot de passe invalide (8+ caractères, lettre + chiffre)"),
    "CANNOT_DELETE_SELF": (400, "Vous ne pouvez pas supprimer votre propre compte"),
    "FORBIDDEN_TARGET": (403, "Suppression non autorisée pour ce compte"),
    "UNIVERSITY_MISMATCH": (403, "Ce compte n'appartient pas à votre campus"),
    "EMAIL_EXISTS": (409, "Cet e-mail est déjà utilisé"),
    "INVALID_PHONE": (400, "Numéro de téléphone mobile invalide (ex. 085 184 8859)"),
    "PHONE_EXISTS": (409, "Ce numéro est déjà lié à un compte"),
    "IDENTITY_CONFLICT": (409, "Cette identité est déjà enregistrée"),
    "MULTI_ROLE": (409, "Cette identité est déjà liée à un autre type de compte"),
    "SUPERADMIN_LIMIT": (
        403,
        "Limite atteinte : maximum 2 comptes Super Admin autorisés sur la plateforme.",
    ),
    "DB_ROLE_CONSTRAINT": (
        500,
        "Rôle non autorisé en base — redéployez l'API (API-1) pour activer Développeur / Tech Manager.",
    ),
    "CREATE_FAILED": (500, "Création du compte impossible — réessayez ou consultez les logs API."),
}


def _map_error(exc: ValueError) -> None:
    code = str(exc)
    if code in ERROR_MAP:
        status, message = ERROR_MAP[code]
        raise HTTPException(status_code=status, detail={"error": code, "message": message})
    raise exc


@router.get("/accounts/summary")
def accounts_summary_route(user: dict = Depends(require_roles("universite"))):
    try:
        return campus_accounts_summary(user)
    except ValueError as e:
        _map_error(e)


@router.get("/accounts")
def accounts_list_route(
    user: dict = Depends(require_roles("universite")),
    role: str | None = Query(None),
):
    try:
        return {"accounts": list_campus_accounts(user, role)}
    except ValueError as e:
        _map_error(e)


@router.delete("/accounts/{email}")
@limiter.limit("30/hour")
def delete_account_route(
    email: str,
    request: Request,
    user: dict = Depends(require_roles("universite")),
):
    try:
        result = delete_campus_account(user, email)
        audit_service.log_audit(
            request,
            "delete_account",
            "user",
            meta={"email": result.get("email", "")[:80]},
            universite=user.get("universite"),
        )
        return result
    except ValueError as e:
        _map_error(e)


@router.get("/institutional/summary")
def institutional_summary_route(
    user: dict = Depends(require_roles("superadmin", "ministere")),
):
    try:
        return institutional_admins_summary(user)
    except ValueError as e:
        _map_error(e)


@router.get("/institutional")
def institutional_list_route(
    user: dict = Depends(require_roles("superadmin", "ministere")),
):
    try:
        return {"admins": list_institutional_admins(user)}
    except ValueError as e:
        _map_error(e)


@router.post("/institutional", status_code=201)
@limiter.limit("20/hour")
def create_institutional_route(
    body: dict,
    request: Request,
    user: dict = Depends(require_roles("superadmin")),
):
    try:
        created = create_institutional_admin(user, body)
        audit_service.log_audit(
            request,
            "create_institutional_admin",
            "user",
            meta={"email": created.get("email", "")[:80], "role": created.get("role")},
        )
        return {"ok": True, "admin": created}
    except ValueError as e:
        _map_error(e)


@router.post("/institutional/faculty-sections", status_code=201)
@limiter.limit("40/hour")
def seed_faculty_sections_route(
    body: dict,
    request: Request,
    user: dict = Depends(require_roles("superadmin")),
):
    del user
    universite = str(body.get("universite") or body.get("sigle") or "").strip()
    rows = body.get("facultySections") or []
    if not universite or not rows:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_INPUT",
                "message": "Université et liste de sections requises",
            },
        )
    try:
        sections = seed_faculty_sections_for_campus(universite, rows)
        audit_service.log_audit(
            request,
            "seed_faculty_sections",
            "section",
            universite=universite,
            meta={"count": len(sections)},
        )
        return {"ok": True, "sections": sections}
    except ValueError as e:
        _map_error(e)


@router.delete("/institutional/{email}")
@limiter.limit("20/hour")
def delete_institutional_route(
    email: str,
    request: Request,
    user: dict = Depends(require_roles("superadmin")),
):
    try:
        result = delete_institutional_admin(user, email)
        audit_service.log_audit(
            request,
            "delete_institutional_admin",
            "user",
            meta={"email": result.get("email", "")[:80], "role": result.get("role")},
        )
        return result
    except ValueError as e:
        _map_error(e)


@router.get("/platform/accounts/summary")
def platform_accounts_summary_route(
    user: dict = Depends(require_roles("superadmin")),
):
    try:
        return platform_accounts_summary(user)
    except ValueError as e:
        _map_error(e)


@router.get("/platform/accounts")
def platform_accounts_list_route(
    user: dict = Depends(require_roles("superadmin")),
    role: str | None = Query(None),
    q: str | None = Query(None),
    universite: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    try:
        accounts = list_platform_accounts(user, role=role, q=q, universite=universite, limit=limit)
        return {"accounts": accounts, "total": len(accounts)}
    except ValueError as e:
        _map_error(e)


@router.delete("/platform/accounts/{email}")
@limiter.limit("30/hour")
def delete_platform_account_route(
    email: str,
    request: Request,
    user: dict = Depends(require_roles("superadmin")),
):
    try:
        result = delete_platform_account(user, email)
        audit_service.log_audit(
            request,
            "delete_platform_account",
            "user",
            meta={
                "email": result.get("email", "")[:80],
                "role": result.get("role"),
            },
        )
        return result
    except ValueError as e:
        _map_error(e)


@router.get("/students/pending")
def list_platform_pending_students_route(
    user: dict = Depends(require_roles("superadmin")),
    status: str | None = Query("pending"),
    universite: str | None = Query(None),
):
    try:
        students = list_students_for_platform_approval(user, status=status or "pending")
        if universite:
            from app.utils.campus_catalog import same_campus

            uni = str(universite).strip()
            students = [s for s in students if same_campus(s.get("universite"), uni)]
        return {
            "students": [user_to_session(s) for s in students if s],
            "total": len(students),
        }
    except ValueError as e:
        _map_error(e)


@router.patch("/students/{email}/approval")
@limiter.limit("120/hour")
def platform_student_approval_route(
    email: str,
    request: Request,
    body: dict,
    user: dict = Depends(require_roles("superadmin")),
):
    status = str(body.get("status") or "approved").strip()
    if status == "confirmed":
        status = "approved"
    reason = str(body.get("reason") or "").strip()
    try:
        updated = set_student_section_approval(user, email, status, reason)
        audit_service.log_audit(
            request,
            "approve_student_platform",
            "user",
            resource_id=updated.get("id"),
            meta={
                "email": updated.get("email", "")[:80],
                "status": status,
                "by": "superadmin",
            },
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


@router.get("/activities/summary")
def activities_summary_route(
    user: dict = Depends(require_roles("superadmin", "ministere", "universite")),
):
    try:
        return activities_summary(user)
    except ValueError as e:
        _map_error(e)


@router.get("/presence/summary")
def presence_summary_route(user: dict = Depends(require_roles("superadmin"))):
    try:
        return platform_presence_global_summary(user)
    except ValueError as e:
        _map_error(e)


@router.get("/activities")
def activities_list_route(
    user: dict = Depends(require_roles("superadmin", "ministere", "universite")),
    limit: int = Query(80, ge=1, le=200),
):
    try:
        return {"activities": list_activities(user, limit)}
    except ValueError as e:
        _map_error(e)


@router.delete("/activities")
@limiter.limit("30/hour")
def delete_activities_route(
    body: dict,
    request: Request,
    user: dict = Depends(require_roles("superadmin", "ministere", "universite")),
):
    try:
        delete_all = bool(body.get("deleteAll"))
        ids = body.get("ids") if isinstance(body.get("ids"), list) else []
        return delete_activities(user, ids=ids, delete_all=delete_all)
    except ValueError as e:
        _map_error(e)
