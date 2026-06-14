from fastapi import APIRouter, Depends, HTTPException, Request

from app.deps import require_roles
from app.rate_limit import limiter
from app.services.user_service import (
    list_campus_professors,
    nominate_professor,
    revoke_professor_nomination,
    user_to_session,
)
from app.utils.sanitize import validate_email_strict

router = APIRouter(prefix="/nominations", tags=["nominations"])


@router.get("/professors")
def list_professors_route(user: dict = Depends(require_roles("universite"))):
    professors = list_campus_professors(user)
    return {
        "professors": [
            {
                "email": p["email"],
                "prenom": p.get("prenom"),
                "nom": p.get("nom"),
                "grade": p.get("grade"),
                "departement": p.get("departement"),
                "numEmploye": p.get("numEmploye"),
                "nomination": p.get("nomination"),
                "sectionId": p.get("sectionId"),
                "payment": p.get("payment"),
                "createdAt": p.get("createdAt"),
            }
            for p in professors
        ]
    }


@router.post("/professor")
@limiter.limit("30/hour")
def nominate_professor_route(request: Request, body: dict, user: dict = Depends(require_roles("universite"))):
    email = validate_email_strict(body.get("email"))
    nomination = body.get("nomination")
    section_id = body.get("sectionId")
    if not email or not nomination or not section_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "MISSING_FIELDS", "message": "E-mail, nomination et section requis"},
        )
    try:
        updated = nominate_professor(user, email, nomination, section_id)
        return {"ok": True, "professor": user_to_session(updated)}
    except ValueError as e:
        code = str(e)
        messages = {
            "FORBIDDEN": (403, "Accès refusé"),
            "INVALID_PROFILE": (400, "Professeur ou section invalide"),
            "UNIVERSITY_MISMATCH": (403, "Professeur hors campus"),
        }
        if code in messages:
            status, message = messages[code]
            raise HTTPException(status_code=status, detail={"error": code, "message": message})
        raise


@router.delete("/professor")
@limiter.limit("30/hour")
def revoke_nomination_route(request: Request, body: dict, user: dict = Depends(require_roles("universite"))):
    email = validate_email_strict(body.get("email"))
    if not email:
        raise HTTPException(status_code=400, detail={"error": "MISSING_EMAIL"})
    try:
        updated = revoke_professor_nomination(user, email)
        return {"ok": True, "professor": user_to_session(updated)}
    except ValueError as e:
        code = str(e)
        if code == "FORBIDDEN":
            raise HTTPException(status_code=403, detail={"error": code, "message": "Accès refusé"})
        raise HTTPException(status_code=400, detail={"error": code, "message": "Professeur introuvable"})
