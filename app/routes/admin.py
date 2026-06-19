from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.deps import require_roles
from app.rate_limit import limiter
from app.services import audit_service
from app.services.user_service import (
    campus_accounts_summary,
    delete_campus_account,
    list_campus_accounts,
)

router = APIRouter(prefix="/admin", tags=["admin"])

ERROR_MAP = {
    "FORBIDDEN": (403, "Accès refusé"),
    "NOT_FOUND": (404, "Compte introuvable"),
    "INVALID_INPUT": (400, "E-mail invalide"),
    "CANNOT_DELETE_SELF": (400, "Vous ne pouvez pas supprimer votre propre compte"),
    "FORBIDDEN_TARGET": (403, "Impossible de supprimer un compte université"),
    "UNIVERSITY_MISMATCH": (403, "Ce compte n'appartient pas à votre campus"),
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
