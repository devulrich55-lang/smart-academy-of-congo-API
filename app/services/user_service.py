import json
import uuid
from datetime import datetime, timezone

from passlib.context import CryptContext

from app.config import settings
from app.database import get_db, row_to_user
from app.utils.sanitize import (
    clean_email,
    clean_phone,
    clean_role,
    clean_text,
    get_display_name,
    norm_person_key,
    validate_email_strict,
    validate_password,
    validate_person_name_text,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
BCRYPT_ROUNDS = 12


def get_display_name_from_user(user: dict | None) -> str:
    return get_display_name(user) if user else "Utilisateur"


def find_user_by_email(email: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
    ).fetchone()
    return row_to_user(row)


def find_user_by_phone(phone: str) -> dict | None:
    normalized = clean_phone(phone)
    if not normalized:
        return None
    rows = get_db().execute(
        "SELECT * FROM users WHERE telephone IS NOT NULL"
    ).fetchall()
    for row in rows:
        if clean_phone(row["telephone"]) == normalized:
            return row_to_user(row)
    return None


def _assert_unique_identity(profile: dict, email: str) -> str:
    existing = find_user_by_email(email)
    if existing:
        if existing["role"] == profile.get("role"):
            raise ValueError("EMAIL_EXISTS")
        raise ValueError("IDENTITY_CONFLICT")

    phone = clean_phone(profile.get("telephone"))
    if not phone:
        raise ValueError("INVALID_PHONE")
    if find_user_by_phone(phone):
        raise ValueError("PHONE_EXISTS")

    role = clean_role(profile.get("role"))
    if role == "universite":
        if not validate_person_name_text(profile.get("nomUniversite"), 3):
            raise ValueError("INVALID_PROFILE")
        if not validate_person_name_text(profile.get("responsable"), 3):
            raise ValueError("INVALID_PROFILE")
        uni_key = f"uni:{norm_person_key(profile.get('nomUniversite'), profile.get('responsable'))}"
        rows = get_db().execute(
            "SELECT * FROM users WHERE role = 'universite'"
        ).fetchall()
        for row in rows:
            k = f"uni:{norm_person_key(row['nom_universite'], row['responsable'])}"
            if k == uni_key:
                raise ValueError("IDENTITY_CONFLICT")
        return phone

    if role == "section":
        if not validate_person_name_text(profile.get("prenom")) and not validate_person_name_text(
            profile.get("nom"), 2
        ):
            raise ValueError("INVALID_PROFILE")
        if not profile.get("sectionId"):
            raise ValueError("INVALID_PROFILE")
        return phone

    if not validate_person_name_text(profile.get("prenom")) or not validate_person_name_text(
        profile.get("nom")
    ):
        raise ValueError("INVALID_PROFILE")

    key = norm_person_key(profile.get("prenom"), profile.get("nom"))
    for row in get_db().execute(
        "SELECT email, role, prenom, nom FROM users"
    ).fetchall():
        if row["role"] == "universite":
            continue
        if norm_person_key(row["prenom"], row["nom"]) == key and row["role"] != role:
            raise ValueError("MULTI_ROLE")
    return phone


def find_user_by_id(user_id: str) -> dict | None:
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_user(row)


def find_user_by_identifier(identifier: str) -> dict | None:
    db = get_db()
    ident = clean_email(identifier) or identifier.strip()
    row = db.execute(
        "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (ident,)
    ).fetchone()
    if not row:
        row = db.execute(
            "SELECT * FROM users WHERE matricule = ?", (ident,)
        ).fetchone()
    if not row:
        row = db.execute(
            "SELECT * FROM users WHERE num_employe = ?", (ident,)
        ).fetchone()
    if not row:
        row = db.execute(
            "SELECT * FROM users WHERE num_assist = ?", (ident,)
        ).fetchone()
    return row_to_user(row)


def create_user(profile: dict) -> dict:
    email = validate_email_strict(profile.get("email")) or clean_email(profile.get("email"))
    role = clean_role(profile.get("role"))
    if not email or not role:
        raise ValueError("INVALID_PROFILE")

    phone_normalized = _assert_unique_identity(profile, email)
    password_hash = pwd_context.hash(profile["password"])
    universite_locked = (
        clean_text(profile.get("universite") or profile.get("sigle"), 50)
        or clean_text(profile.get("codeUni"), 50)
        if role == "universite"
        else clean_text(profile.get("universite"), 50)
    )
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    get_db().execute(
        """INSERT INTO users (
          id, email, password_hash, role, prenom, nom, telephone, universite,
          filiere, niveau, matricule, date_naissance, departement, grade, service,
          fonction, num_employe, num_assist, nom_universite, sigle, ville, adresse,
          nb_etudiants, site_web, responsable, code_uni, cours_classes, payment,
          inscription_fee, classe, section_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            user_id,
            email,
            password_hash,
            role,
            clean_text(profile.get("prenom"), 100) or None,
            clean_text(profile.get("nom"), 150) or None,
            phone_normalized,
            universite_locked,
            clean_text(profile.get("filiere"), 200) or None,
            profile.get("niveau"),
            clean_text(profile.get("matricule"), 50) or None,
            profile.get("dateNaissance"),
            clean_text(profile.get("departement"), 200) or None,
            clean_text(profile.get("grade"), 50) or None,
            clean_text(profile.get("service"), 50) or None,
            clean_text(profile.get("fonction"), 50) or None,
            clean_text(profile.get("numEmploye"), 50) or None,
            clean_text(profile.get("numAssist"), 50) or None,
            clean_text(profile.get("nomUniversite"), 200) or None,
            clean_text(profile.get("sigle"), 30) or None,
            clean_text(profile.get("ville"), 100) or None,
            clean_text(profile.get("adresse"), 300) or None,
            clean_text(profile.get("nbEtudiants"), 20) or None,
            clean_text(profile.get("siteWeb"), 200) if profile.get("siteWeb") else None,
            clean_text(profile.get("responsable"), 150) or None,
            clean_text(profile.get("codeUni"), 50) or None,
            json.dumps(profile.get("coursClasses") or []),
            json.dumps(profile["payment"]) if profile.get("payment") else None,
            json.dumps(profile["inscriptionFee"])
            if profile.get("inscriptionFee")
            else None,
            clean_text(profile.get("classe"), 150) or None,
            clean_text(profile.get("sectionId"), 80) or None,
            now,
            now,
        ),
    )
    get_db().commit()
    return find_user_by_id(user_id)


def user_to_session(user: dict | None) -> dict | None:
    if not user:
        return None
    uni = (
        user.get("universite") or user.get("sigle") or user.get("codeUni")
        if user.get("role") == "universite"
        else user.get("universite")
    )
    is_uni = user.get("role") == "universite"
    return {
        "role": user["role"],
        "identifiant": user["email"],
        "userId": user["id"],
        "nom": user.get("nomUniversite") or user.get("email", "") if is_uni else user.get("nom", ""),
        "prenom": user.get("prenom"),
        "displayName": get_display_name(user),
        "universite": uni,
        "universiteLocked": uni,
        "filiere": user.get("filiere"),
        "niveau": user.get("niveau"),
        "coursClasses": user.get("coursClasses"),
        "departement": user.get("departement"),
        "service": user.get("service"),
        "codeUni": user.get("codeUni"),
        "sigle": user.get("sigle"),
        "matricule": user.get("matricule"),
        "classe": user.get("classe"),
        "sectionId": user.get("sectionId"),
        "sectionName": user.get("sectionName"),
        "nomination": user.get("nomination"),
        "grade": user.get("grade"),
        "fonction": user.get("fonction"),
        "campusTariffs": user.get("campusTariffs"),
    }


def _is_section_head_actor(actor: dict) -> bool:
    if actor.get("role") == "section":
        return bool(actor.get("sectionId"))
    return (
        actor.get("role") == "professeur"
        and actor.get("nomination") == "chef_section"
        and bool(actor.get("sectionId"))
    )


def _section_head_section_id(actor: dict) -> str | None:
    if _is_section_head_actor(actor):
        return actor.get("sectionId")
    return None


def _actor_campus(actor: dict) -> str | None:
    if actor.get("role") == "universite":
        return actor.get("universite") or actor.get("sigle") or actor.get("codeUni")
    return actor.get("universite")


def create_section_head_account(university_actor: dict, profile: dict) -> dict:
    if university_actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    campus = _actor_campus(university_actor)
    if not campus or not profile.get("sectionId"):
        raise ValueError("INVALID_PROFILE")
    if not profile.get("password") or not validate_password(profile.get("password")):
        raise ValueError("INVALID_PASSWORD")

    profile = {
        **profile,
        "role": "section",
        "universite": campus,
        "sectionId": clean_text(profile.get("sectionId"), 80),
    }
    return create_user(profile)


def create_student_for_section(actor: dict, profile: dict) -> dict:
    actor_role = actor.get("role")
    if actor_role not in ("section", "universite", "professeur"):
        raise ValueError("FORBIDDEN")
    if actor_role == "professeur" and not _is_section_head_actor(actor):
        raise ValueError("FORBIDDEN")
    if not profile.get("password") or not validate_password(profile.get("password")):
        raise ValueError("INVALID_PASSWORD")

    campus = _actor_campus(actor)
    now = datetime.now(timezone.utc).isoformat()

    if actor_role == "section" or (actor_role == "professeur" and _is_section_head_actor(actor)):
        section_id = _section_head_section_id(actor)
        if not section_id:
            raise ValueError("INVALID_PROFILE")
        profile = {
            **profile,
            "role": "etudiant",
            "universite": campus,
            "filiere": actor.get("filiere") or profile.get("filiere"),
            "sectionId": section_id,
        }
    else:
        profile = {
            **profile,
            "role": "etudiant",
            "universite": profile.get("universite") or campus,
            "sectionId": profile.get("sectionId"),
        }
        if campus and profile["universite"] != campus:
            raise ValueError("UNIVERSITY_MISMATCH")

    profile["payment"] = {
        "status": "verified",
        "method": "section_delegate",
        "verifiedAt": now,
        "verifiedBy": actor.get("email") or actor.get("id"),
        "createdByRole": actor_role,
    }
    return create_user(profile)


def list_students_for_section(actor: dict) -> list[dict]:
    db = get_db()
    actor_role = actor.get("role")
    campus = _actor_campus(actor)

    if actor_role == "section" or (actor_role == "professeur" and _is_section_head_actor(actor)):
        section_id = _section_head_section_id(actor)
        if not section_id:
            return []
        rows = db.execute(
            """SELECT * FROM users
               WHERE role = 'etudiant' AND section_id = ?
               ORDER BY created_at DESC""",
            (section_id,),
        ).fetchall()
    elif actor_role == "universite" and campus:
        rows = db.execute(
            """SELECT * FROM users
               WHERE role = 'etudiant' AND universite = ?
               ORDER BY created_at DESC""",
            (campus,),
        ).fetchall()
    else:
        return []

    return [row_to_user(r) for r in rows if r]


def list_campus_professors(actor: dict) -> list[dict]:
    campus = _actor_campus(actor)
    if actor.get("role") != "universite" or not campus:
        return []
    rows = get_db().execute(
        """SELECT * FROM users WHERE role = 'professeur' AND universite = ?
           ORDER BY nom, prenom""",
        (campus,),
    ).fetchall()
    return [row_to_user(r) for r in rows if r]


def nominate_professor(university_actor: dict, email: str, nomination: str, section_id: str) -> dict:
    if university_actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    campus = _actor_campus(university_actor)
    if not campus or nomination != "chef_section" or not section_id:
        raise ValueError("INVALID_PROFILE")

    professor = find_user_by_email(validate_email_strict(email) or email)
    if not professor or professor.get("role") != "professeur":
        raise ValueError("INVALID_PROFILE")
    if professor.get("universite") != campus:
        raise ValueError("UNIVERSITY_MISMATCH")

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """UPDATE users SET nomination = NULL, section_id = NULL, updated_at = ?
           WHERE role = 'professeur' AND universite = ? AND section_id = ? AND id != ?""",
        (now, campus, section_id, professor["id"]),
    )
    db.execute(
        """UPDATE users SET nomination = ?, section_id = ?, updated_at = ? WHERE id = ?""",
        (nomination, clean_text(section_id, 80), now, professor["id"]),
    )
    db.commit()
    return find_user_by_id(professor["id"])


def revoke_professor_nomination(university_actor: dict, email: str) -> dict:
    if university_actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    campus = _actor_campus(university_actor)
    professor = find_user_by_email(validate_email_strict(email) or email)
    if not professor or professor.get("role") != "professeur":
        raise ValueError("INVALID_PROFILE")
    if campus and professor.get("universite") != campus:
        raise ValueError("UNIVERSITY_MISMATCH")

    now = datetime.now(timezone.utc).isoformat()
    get_db().execute(
        """UPDATE users SET nomination = NULL, section_id = NULL, updated_at = ? WHERE id = ?""",
        (now, professor["id"]),
    )
    get_db().commit()
    return find_user_by_id(professor["id"])


def record_failed_login(user_id: str) -> None:
    db = get_db()
    row = db.execute(
        "SELECT failed_login_attempts FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    attempts = (row["failed_login_attempts"] or 0) + 1
    locked_until = None
    if attempts >= settings.max_login_attempts:
        locked_until = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp()
            + settings.lockout_minutes * 60,
            tz=timezone.utc,
        ).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE users SET failed_login_attempts = ?, locked_until = ?, updated_at = ? WHERE id = ?",
        (attempts, locked_until, now, user_id),
    )
    db.commit()


def clear_failed_logins(user_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    get_db().execute(
        "UPDATE users SET failed_login_attempts = 0, locked_until = NULL, updated_at = ? WHERE id = ?",
        (now, user_id),
    )
    get_db().commit()


def is_account_locked(user: dict | None) -> bool:
    if not user:
        return False
    row = get_db().execute(
        "SELECT locked_until FROM users WHERE id = ?", (user["id"],)
    ).fetchone()
    if not row or not row["locked_until"]:
        return False
    if datetime.fromisoformat(row["locked_until"].replace("Z", "+00:00")) > datetime.now(
        timezone.utc
    ):
        return True
    clear_failed_logins(user["id"])
    return False


def verify_password(user: dict, password: str) -> bool:
    row = get_db().execute(
        "SELECT password_hash FROM users WHERE id = ?", (user["id"],)
    ).fetchone()
    if not row:
        return False
    return pwd_context.verify(password, row["password_hash"])


def update_password(user_id: str, new_password: str) -> None:
    password_hash = pwd_context.hash(new_password)
    now = datetime.now(timezone.utc).isoformat()
    get_db().execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (password_hash, now, user_id),
    )
    get_db().commit()


def revoke_all_refresh_tokens(user_id: str) -> None:
    get_db().execute("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,))
    get_db().commit()
