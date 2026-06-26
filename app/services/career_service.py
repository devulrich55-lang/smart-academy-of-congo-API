from datetime import datetime, timezone

from app.config import settings
from app.database import get_db
from app.services import email_service
from app.utils.platform_security import uid
from app.utils.sanitize import clean_text

CAMPUS_MANAGE_ROLES = frozenset({"universite"})
NATIONAL_MANAGE_ROLES = frozenset({"ministere"})
OFFER_TYPES = frozenset({"stage", "emploi", "alternance", "bourse", "autre"})
SCOPES = frozenset({"campus", "national"})
APP_STATUSES = frozenset({"pending", "viewed", "accepted", "rejected"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _campus(actor: dict) -> str:
    return clean_text(
        actor.get("universite") or actor.get("codeUni") or actor.get("sigle"), 80
    )


def _row_to_offer(row, applied: bool = False) -> dict:
    return {
        "id": row["id"],
        "scope": row["scope"],
        "type": row["offer_type"],
        "title": row["title"],
        "organization": row["organization"] or "",
        "location": row["location"] or "",
        "description": row["description"] or "",
        "requirements": row["requirements"] or "",
        "filiere": row["filiere"] or "",
        "niveau": row["niveau"] or "",
        "universite": row["universite"] or "",
        "universityName": row["university_name"] or "",
        "contactEmail": row["contact_email"] or "",
        "applyUrl": row["apply_url"] or "",
        "deadline": row["deadline"] or "",
        "published": bool(row["published"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "applied": applied,
    }


def _assert_can_manage(actor: dict, scope: str) -> str:
    role = actor.get("role")
    if scope == "national":
        if role not in NATIONAL_MANAGE_ROLES:
            raise ValueError("FORBIDDEN")
        return "national"
    if role not in CAMPUS_MANAGE_ROLES:
        raise ValueError("FORBIDDEN")
    campus = _campus(actor)
    if not campus:
        raise ValueError("INVALID_INPUT")
    return campus


def _student_campus(actor: dict) -> str:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")
    return _campus(actor)


def _applied_ids(email: str) -> set[str]:
    rows = get_db().execute(
        "SELECT career_id FROM career_applications WHERE LOWER(student_email) = ?",
        (email.lower(),),
    ).fetchall()
    return {r["career_id"] for r in rows}


def _match_student_offer(row, actor: dict) -> bool:
    filiere = clean_text(actor.get("filiere"), 120).lower()
    niveau = clean_text(actor.get("niveau"), 40).lower()
    row_fil = (row["filiere"] or "").lower()
    row_niv = (row["niveau"] or "").lower()
    if row_fil and filiere and row_fil not in filiere and filiere not in row_fil:
        return False
    if row_niv and niveau and row_niv not in niveau and niveau not in row_niv:
        return False
    return True


def list_for_student(actor: dict) -> list[dict]:
    campus = _student_campus(actor)
    email = (actor.get("email") or actor.get("identifiant") or "").lower()
    applied = _applied_ids(email)
    rows = get_db().execute(
        """SELECT * FROM career_offers
           WHERE published = 1
           AND (scope = 'national' OR universite = ?)
           ORDER BY created_at DESC""",
        (campus,),
    ).fetchall()
    out = []
    for row in rows:
        if not _match_student_offer(row, actor):
            continue
        out.append(_row_to_offer(row, applied=row["id"] in applied))
    return out


def list_public(scope: str | None = None, universite: str | None = None) -> list[dict]:
    if scope == "national":
        rows = get_db().execute(
            """SELECT * FROM career_offers
               WHERE published = 1 AND scope = 'national'
               ORDER BY created_at DESC"""
        ).fetchall()
    elif universite:
        rows = get_db().execute(
            """SELECT * FROM career_offers
               WHERE published = 1 AND (scope = 'national' OR universite = ?)
               ORDER BY created_at DESC""",
            (clean_text(universite, 80),),
        ).fetchall()
    else:
        rows = get_db().execute(
            """SELECT * FROM career_offers
               WHERE published = 1
               ORDER BY created_at DESC"""
        ).fetchall()
    return [_row_to_offer(r) for r in rows]


def list_manage(actor: dict) -> list[dict]:
    role = actor.get("role")
    if role == "ministere":
        rows = get_db().execute(
            """SELECT * FROM career_offers
               WHERE scope = 'national'
               ORDER BY created_at DESC"""
        ).fetchall()
        return [_row_to_offer(r) for r in rows]
    campus = _assert_can_manage(actor, "campus")
    rows = get_db().execute(
        """SELECT * FROM career_offers
           WHERE universite = ? AND scope = 'campus'
           ORDER BY created_at DESC""",
        (campus,),
    ).fetchall()
    return [_row_to_offer(r) for r in rows]


def create_offer(actor: dict, data: dict) -> dict:
    role = actor.get("role")
    scope = clean_text(data.get("scope"), 20) or ("national" if role == "ministere" else "campus")
    if scope not in SCOPES:
        scope = "campus"
    if scope == "national":
        _assert_can_manage(actor, "national")
        campus = clean_text(data.get("universite"), 80) or "national"
    else:
        campus = _assert_can_manage(actor, "campus")

    title = clean_text(data.get("title"), 200)
    if not title or len(title) < 3:
        raise ValueError("INVALID_INPUT")

    offer_type = clean_text(data.get("type"), 40) or "stage"
    if offer_type not in OFFER_TYPES:
        offer_type = "autre"

    now = _now()
    item_id = uid("job")
    get_db().execute(
        """INSERT INTO career_offers (
            id, scope, offer_type, title, organization, location, description,
            requirements, filiere, niveau, universite, university_name,
            contact_email, apply_url, deadline, published, author_id, author_role,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item_id,
            scope,
            offer_type,
            title,
            clean_text(data.get("organization"), 200),
            clean_text(data.get("location"), 120),
            clean_text(data.get("description"), 3000),
            clean_text(data.get("requirements"), 1500),
            clean_text(data.get("filiere"), 120),
            clean_text(data.get("niveau"), 40),
            campus,
            clean_text(data.get("universityName"), 200),
            clean_text(data.get("contactEmail"), 255),
            clean_text(data.get("applyUrl"), 500),
            clean_text(data.get("deadline"), 20),
            1 if data.get("published", True) else 0,
            actor.get("email") or actor.get("identifiant") or "",
            role or "",
            now,
            now,
        ),
    )
    get_db().commit()
    row = get_db().execute("SELECT * FROM career_offers WHERE id = ?", (item_id,)).fetchone()
    return _row_to_offer(row)


def _get_offer_for_manage(actor: dict, offer_id: str):
    row = get_db().execute(
        "SELECT * FROM career_offers WHERE id = ?", (clean_text(offer_id, 80),)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    role = actor.get("role")
    if row["scope"] == "national":
        if role != "ministere":
            raise ValueError("FORBIDDEN")
    else:
        campus = _assert_can_manage(actor, "campus")
        if row["universite"] != campus:
            raise ValueError("FORBIDDEN")
    return row


def update_offer(actor: dict, offer_id: str, data: dict) -> dict:
    row = _get_offer_for_manage(actor, offer_id)
    fields = []
    values = []
    mapping = {
        "title": ("title", 200),
        "organization": ("organization", 200),
        "location": ("location", 120),
        "description": ("description", 3000),
        "requirements": ("requirements", 1500),
        "filiere": ("filiere", 120),
        "niveau": ("niveau", 40),
        "contactEmail": ("contact_email", 255),
        "applyUrl": ("apply_url", 500),
        "deadline": ("deadline", 20),
        "universityName": ("university_name", 200),
    }
    for key, (col, max_len) in mapping.items():
        if key in data:
            fields.append(f"{col} = ?")
            values.append(clean_text(data.get(key), max_len))
    if "type" in data:
        t = clean_text(data.get("type"), 40)
        if t not in OFFER_TYPES:
            t = "autre"
        fields.append("offer_type = ?")
        values.append(t)
    if "published" in data:
        fields.append("published = ?")
        values.append(1 if data.get("published") else 0)
    if not fields:
        return _row_to_offer(row)
    fields.append("updated_at = ?")
    values.append(_now())
    values.append(row["id"])
    get_db().execute(
        f"UPDATE career_offers SET {', '.join(fields)} WHERE id = ?", tuple(values)
    )
    get_db().commit()
    updated = get_db().execute("SELECT * FROM career_offers WHERE id = ?", (row["id"],)).fetchone()
    return _row_to_offer(updated)


def delete_offer(actor: dict, offer_id: str) -> dict:
    row = _get_offer_for_manage(actor, offer_id)
    get_db().execute("DELETE FROM career_applications WHERE career_id = ?", (row["id"],))
    get_db().execute("DELETE FROM career_offers WHERE id = ?", (row["id"],))
    get_db().commit()
    return {"ok": True, "id": row["id"]}


def apply(actor: dict, offer_id: str, message: str = "") -> dict:
    campus = _student_campus(actor)
    email = (actor.get("email") or actor.get("identifiant") or "").lower()
    row = get_db().execute(
        "SELECT * FROM career_offers WHERE id = ? AND published = 1",
        (clean_text(offer_id, 80),),
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    if row["scope"] != "national" and row["universite"] != campus:
        raise ValueError("FORBIDDEN_CAMPUS")
    if not _match_student_offer(row, actor):
        raise ValueError("FORBIDDEN")

    existing = get_db().execute(
        """SELECT id FROM career_applications
           WHERE career_id = ? AND LOWER(student_email) = ?""",
        (row["id"], email),
    ).fetchone()
    if existing:
        app = get_db().execute(
            "SELECT * FROM career_applications WHERE id = ?", (existing["id"],)
        ).fetchone()
        return _app_to_dict(app, row)

    now = _now()
    app_id = uid("app")
    get_db().execute(
        """INSERT INTO career_applications (
            id, career_id, student_email, student_name, matricule, universite,
            message, status, applied_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (
            app_id,
            row["id"],
            email,
            " ".join(p for p in [actor.get("prenom"), actor.get("nom")] if p).strip(),
            clean_text(actor.get("matricule"), 50),
            campus,
            clean_text(message, 1500),
            now,
        ),
    )
    get_db().commit()
    app = get_db().execute(
        "SELECT * FROM career_applications WHERE id = ?", (app_id,)
    ).fetchone()
    contact = (row["contact_email"] or "").strip().lower()
    if contact and email_service.smtp_configured():
        student = " ".join(p for p in [actor.get("prenom"), actor.get("nom")] if p).strip() or email
        email_service.send_platform_notification_email(
            contact,
            "Nouvelle candidature — " + (row["title"] or "Offre"),
            f"{student} ({email}) a postulé pour « {row['title']} »."
            + (f"\n\nMessage : {clean_text(message, 500)}" if message else ""),
            f"{settings.frontend_url}/plateforme.html#stages",
        )
    return _app_to_dict(app, row)


def _app_to_dict(app, offer_row) -> dict:
    return {
        "id": app["id"],
        "careerId": app["career_id"],
        "studentEmail": app["student_email"],
        "studentName": app["student_name"] or "",
        "matricule": app["matricule"] or "",
        "message": app["message"] or "",
        "status": app["status"],
        "appliedAt": app["applied_at"],
        "offer": _row_to_offer(offer_row),
    }


def list_my_applications(actor: dict) -> list[dict]:
    campus = _student_campus(actor)
    email = (actor.get("email") or actor.get("identifiant") or "").lower()
    rows = get_db().execute(
        """SELECT a.*, o.scope, o.offer_type, o.title, o.organization, o.location,
                  o.description, o.requirements, o.filiere, o.niveau, o.universite,
                  o.university_name, o.contact_email, o.apply_url, o.deadline,
                  o.published, o.created_at, o.updated_at
           FROM career_applications a
           JOIN career_offers o ON o.id = a.career_id
           WHERE LOWER(a.student_email) = ? AND a.universite = ?
           ORDER BY a.applied_at DESC""",
        (email, campus),
    ).fetchall()
    out = []
    for r in rows:
        offer_row = {
            "id": r["career_id"],
            "scope": r["scope"],
            "offer_type": r["offer_type"],
            "title": r["title"],
            "organization": r["organization"],
            "location": r["location"],
            "description": r["description"],
            "requirements": r["requirements"],
            "filiere": r["filiere"],
            "niveau": r["niveau"],
            "universite": r["universite"],
            "university_name": r["university_name"],
            "contact_email": r["contact_email"],
            "apply_url": r["apply_url"],
            "deadline": r["deadline"],
            "published": r["published"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        out.append(
            {
                "id": r["id"],
                "careerId": r["career_id"],
                "status": r["status"],
                "appliedAt": r["applied_at"],
                "message": r["message"],
                "offer": _row_to_offer(offer_row, applied=True),
            }
        )
    return out


def list_applications_for_offer(actor: dict, offer_id: str) -> list[dict]:
    row = _get_offer_for_manage(actor, offer_id)
    apps = get_db().execute(
        """SELECT * FROM career_applications
           WHERE career_id = ?
           ORDER BY applied_at DESC""",
        (row["id"],),
    ).fetchall()
    return [_app_to_dict(a, row) for a in apps]


def update_application_status(actor: dict, app_id: str, status: str) -> dict:
    status = clean_text(status, 20)
    if status not in APP_STATUSES:
        raise ValueError("INVALID_STATUS")
    app = get_db().execute(
        "SELECT * FROM career_applications WHERE id = ?", (clean_text(app_id, 80),)
    ).fetchone()
    if not app:
        raise ValueError("NOT_FOUND")
    offer = get_db().execute(
        "SELECT * FROM career_offers WHERE id = ?", (app["career_id"],)
    ).fetchone()
    _get_offer_for_manage(actor, offer["id"])
    get_db().execute(
        "UPDATE career_applications SET status = ? WHERE id = ?",
        (status, app["id"]),
    )
    get_db().commit()
    updated = get_db().execute(
        "SELECT * FROM career_applications WHERE id = ?", (app["id"],)
    ).fetchone()
    return _app_to_dict(updated, offer)
