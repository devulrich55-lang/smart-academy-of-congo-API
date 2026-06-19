import json
import uuid
from datetime import datetime, timezone

from passlib.context import CryptContext

from app.config import settings
from app.database import get_db, row_to_user
from app.utils.sanitize import (
    clean_email,
    clean_institutional_role,
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

    if role in ("ministere", "superadmin"):
        if not validate_person_name_text(profile.get("prenom")) or not validate_person_name_text(
            profile.get("nom")
        ):
            raise ValueError("INVALID_PROFILE")
        return phone

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
        if row["role"] in ("universite", "ministere", "superadmin"):
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
    is_institutional = user.get("role") in ("ministere", "superadmin")
    display_nom = user.get("nom", "")
    if is_uni:
        display_nom = user.get("nomUniversite") or user.get("email", "")
    elif is_institutional:
        display_nom = user.get("nom", "") or user.get("email", "")
    return {
        "role": user["role"],
        "identifiant": user["email"],
        "userId": user["id"],
        "nom": display_nom,
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


def list_students_for_professor(actor: dict) -> list[dict]:
    if actor.get("role") != "professeur":
        raise ValueError("FORBIDDEN")
    campus = _actor_campus(actor)
    if not campus:
        return []
    rows = get_db().execute(
        """SELECT * FROM users
           WHERE role = 'etudiant' AND universite = ?
           ORDER BY nom, prenom""",
        (campus,),
    ).fetchall()
    return [row_to_user(r) for r in rows if r]


def _account_public_row(user: dict) -> dict:
    payment = user.get("payment") if isinstance(user.get("payment"), dict) else None
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "prenom": user.get("prenom"),
        "nom": user.get("nom"),
        "telephone": user.get("telephone"),
        "universite": user.get("universite"),
        "filiere": user.get("filiere"),
        "niveau": user.get("niveau"),
        "classe": user.get("classe"),
        "matricule": user.get("matricule"),
        "sectionId": user.get("sectionId"),
        "paymentStatus": payment.get("status") if payment else None,
        "createdAt": user.get("createdAt"),
    }


def list_campus_accounts(actor: dict, role: str | None = None) -> list[dict]:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    campus = _actor_campus(actor)
    if not campus:
        return []

    q = "SELECT * FROM users WHERE universite = ? AND role != 'universite'"
    params: list = [campus]
    allowed_roles = ("etudiant", "professeur", "assistant", "section")
    if role and role in allowed_roles:
        q += " AND role = ?"
        params.append(role)
    q += " ORDER BY role, nom, prenom, email"

    rows = get_db().execute(q, params).fetchall()
    return [_account_public_row(row_to_user(r)) for r in rows if r]


def campus_accounts_summary(actor: dict) -> dict:
    accounts = list_campus_accounts(actor)
    by_role = {"etudiant": 0, "professeur": 0, "assistant": 0, "section": 0}
    for account in accounts:
        rr = account.get("role")
        if rr in by_role:
            by_role[rr] += 1
    return {"total": len(accounts), "byRole": by_role}


def delete_campus_account(actor: dict, email: str) -> dict:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    campus = _actor_campus(actor)
    target_email = validate_email_strict(email) or clean_email(email)
    if not target_email:
        raise ValueError("INVALID_INPUT")

    actor_email = (actor.get("email") or "").strip().lower()
    if target_email == actor_email:
        raise ValueError("CANNOT_DELETE_SELF")

    target = find_user_by_email(target_email)
    if not target:
        raise ValueError("NOT_FOUND")
    if target.get("role") == "universite":
        raise ValueError("FORBIDDEN_TARGET")
    if campus and target.get("universite") != campus:
        raise ValueError("UNIVERSITY_MISMATCH")

    user_id = target["id"]
    db = get_db()

    for stmt, params in (
        ("DELETE FROM online_presence WHERE user_email = ? COLLATE NOCASE", (target_email,)),
        ("DELETE FROM grades WHERE student_email = ? COLLATE NOCASE", (target_email,)),
        ("DELETE FROM grades WHERE professor_email = ? COLLATE NOCASE", (target_email,)),
        ("DELETE FROM reclamations WHERE student_email = ? COLLATE NOCASE", (target_email,)),
        ("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,)),
        ("DELETE FROM password_reset_tokens WHERE user_id = ?", (user_id,)),
        ("DELETE FROM users WHERE id = ?", (user_id,)),
    ):
        try:
            db.execute(stmt, params)
        except Exception:
            pass

    db.commit()
    return {"ok": True, "email": target_email}


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


CAMPUS_ROLES = ("etudiant", "professeur", "assistant", "section")
INSTITUTIONAL_ROLES = ("superadmin", "ministere", "universite")
ROLE_LABELS = {
    "superadmin": "Super Admin",
    "ministere": "Ministère",
    "universite": "Admin Université",
}


def _actor_campus(actor: dict) -> str | None:
    return actor.get("universite") or actor.get("codeUni") or actor.get("sigle")


def _campus_account_row(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "prenom": user.get("prenom"),
        "nom": user.get("nom"),
        "matricule": user.get("matricule"),
        "filiere": user.get("filiere"),
        "niveau": user.get("niveau"),
        "classe": user.get("classe"),
        "createdAt": user.get("createdAt"),
    }


def list_campus_accounts(actor: dict, role: str | None = None) -> list[dict]:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    campus = _actor_campus(actor)
    if not campus:
        raise ValueError("FORBIDDEN")
    params: list[str] = [campus]
    query = (
        "SELECT * FROM users WHERE universite = ? "
        "AND role IN ('etudiant','professeur','assistant','section')"
    )
    if role and role in CAMPUS_ROLES:
        query += " AND role = ?"
        params.append(role)
    query += " ORDER BY role, nom, prenom"
    rows = get_db().execute(query, tuple(params)).fetchall()
    return [_campus_account_row(row_to_user(r)) for r in rows]


def campus_accounts_summary(actor: dict) -> dict:
    accounts = list_campus_accounts(actor)
    by_role = {r: 0 for r in CAMPUS_ROLES}
    for account in accounts:
        if account["role"] in by_role:
            by_role[account["role"]] += 1
    return {"total": len(accounts), "byRole": by_role, "campus": _actor_campus(actor)}


def delete_campus_account(actor: dict, email: str) -> dict:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    campus = _actor_campus(actor)
    email_clean = validate_email_strict(email) or clean_email(email)
    if not email_clean:
        raise ValueError("INVALID_INPUT")
    if email_clean.lower() == str(actor.get("email", "")).lower():
        raise ValueError("CANNOT_DELETE_SELF")
    target = find_user_by_email(email_clean)
    if not target:
        raise ValueError("NOT_FOUND")
    if target.get("role") == "universite":
        raise ValueError("FORBIDDEN_TARGET")
    if target.get("role") not in CAMPUS_ROLES:
        raise ValueError("FORBIDDEN_TARGET")
    if campus and target.get("universite") != campus:
        raise ValueError("UNIVERSITY_MISMATCH")
    revoke_all_refresh_tokens(target["id"])
    get_db().execute("DELETE FROM users WHERE id = ?", (target["id"],))
    get_db().commit()
    return {"ok": True, "email": email_clean}


def _institutional_row(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "roleLabel": ROLE_LABELS.get(user["role"], user["role"]),
        "displayName": get_display_name(user),
        "prenom": user.get("prenom"),
        "nom": user.get("nom"),
        "responsable": user.get("responsable"),
        "universite": user.get("universite") or user.get("codeUni"),
        "sigle": user.get("sigle"),
        "nomUniversite": user.get("nomUniversite"),
        "ville": user.get("ville"),
        "telephone": user.get("telephone"),
        "createdAt": user.get("createdAt"),
    }


def list_institutional_admins(actor: dict) -> list[dict]:
    actor_role = actor.get("role")
    if actor_role not in ("superadmin", "ministere"):
        raise ValueError("FORBIDDEN")
    db = get_db()
    if actor_role == "ministere":
        rows = db.execute(
            "SELECT * FROM users WHERE role IN ('ministere','universite') ORDER BY role, email"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM users WHERE role IN ('superadmin','ministere','universite') "
            "ORDER BY role, email"
        ).fetchall()
    return [_institutional_row(row_to_user(r)) for r in rows]


def institutional_admins_summary(actor: dict) -> dict:
    admins = list_institutional_admins(actor)
    by_role = {r: 0 for r in INSTITUTIONAL_ROLES}
    for admin in admins:
        if admin["role"] in by_role:
            by_role[admin["role"]] += 1
    return {"total": len(admins), "byRole": by_role}


def create_institutional_admin(actor: dict, profile: dict) -> dict:
    if actor.get("role") != "superadmin":
        raise ValueError("FORBIDDEN")
    role = clean_institutional_role(str(profile.get("role") or "").strip().lower())
    if not role:
        raise ValueError("INVALID_PROFILE")
    if not validate_password(profile.get("password")):
        raise ValueError("INVALID_PASSWORD")
    email = validate_email_strict(profile.get("email"))
    if not email:
        raise ValueError("INVALID_PROFILE")

    payload: dict = {
        "email": email,
        "password": profile["password"],
        "role": role,
        "telephone": profile.get("telephone"),
        "prenom": profile.get("prenom"),
        "nom": profile.get("nom"),
    }
    if role == "universite":
        payload.update(
            {
                "nomUniversite": profile.get("nomUniversite"),
                "responsable": profile.get("responsable"),
                "codeUni": profile.get("codeUni"),
                "sigle": profile.get("sigle"),
                "ville": profile.get("ville"),
                "universite": profile.get("universite") or profile.get("sigle"),
            }
        )
    return create_user(payload)


def delete_institutional_admin(actor: dict, email: str) -> dict:
    if actor.get("role") != "superadmin":
        raise ValueError("FORBIDDEN")
    email_clean = validate_email_strict(email) or clean_email(email)
    if not email_clean:
        raise ValueError("INVALID_INPUT")
    if email_clean.lower() == str(actor.get("email", "")).lower():
        raise ValueError("CANNOT_DELETE_SELF")
    target = find_user_by_email(email_clean)
    if not target:
        raise ValueError("NOT_FOUND")
    if target.get("role") not in INSTITUTIONAL_ROLES:
        raise ValueError("FORBIDDEN_TARGET")
    revoke_all_refresh_tokens(target["id"])
    get_db().execute("DELETE FROM users WHERE id = ?", (target["id"],))
    get_db().commit()
    return {"ok": True, "email": email_clean, "role": target.get("role")}


PLATFORM_ROLES = (
    "superadmin",
    "ministere",
    "universite",
    "etudiant",
    "professeur",
    "assistant",
    "section",
)


def _platform_registry_row(user: dict) -> dict:
    payment = user.get("payment") if isinstance(user.get("payment"), dict) else None
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "prenom": user.get("prenom"),
        "nom": user.get("nom"),
        "displayName": get_display_name(user),
        "telephone": user.get("telephone"),
        "universite": user.get("universite") or user.get("sigle") or user.get("codeUni"),
        "nomUniversite": user.get("nomUniversite"),
        "sigle": user.get("sigle"),
        "filiere": user.get("filiere"),
        "niveau": user.get("niveau"),
        "classe": user.get("classe"),
        "matricule": user.get("matricule"),
        "sectionId": user.get("sectionId"),
        "paymentStatus": payment.get("status") if payment else None,
        "createdAt": user.get("createdAt"),
        "source": "api",
    }


def list_platform_accounts(
    actor: dict,
    role: str | None = None,
    q: str | None = None,
    universite: str | None = None,
    limit: int = 500,
) -> list[dict]:
    if actor.get("role") != "superadmin":
        raise ValueError("FORBIDDEN")

    query = (
        "SELECT * FROM users WHERE role IN "
        "('superadmin','ministere','universite','etudiant','professeur','assistant','section')"
    )
    params: list = []
    if role and role in PLATFORM_ROLES:
        query += " AND role = ?"
        params.append(role)
    if universite:
        u = clean_text(universite, 100)
        query += (
            " AND (LOWER(universite) = LOWER(?) OR LOWER(sigle) = LOWER(?) "
            "OR LOWER(nom_universite) LIKE ?)"
        )
        params.extend([u, u, f"%{u.lower()}%"])
    query += " ORDER BY datetime(created_at) DESC"

    rows = get_db().execute(query, tuple(params)).fetchall()
    accounts = [_platform_registry_row(row_to_user(r)) for r in rows if r]

    if q:
        needle = q.strip().lower()
        accounts = [
            a
            for a in accounts
            if needle
            in " ".join(
                str(a.get(k) or "")
                for k in (
                    "displayName",
                    "email",
                    "telephone",
                    "universite",
                    "nomUniversite",
                    "filiere",
                    "classe",
                    "role",
                )
            ).lower()
        ]

    cap = max(1, min(int(limit or 500), 5000))
    return accounts[:cap]


def platform_accounts_summary(actor: dict) -> dict:
    accounts = list_platform_accounts(actor, limit=5000)
    by_role = {r: 0 for r in PLATFORM_ROLES}
    for account in accounts:
        rr = account.get("role")
        if rr in by_role:
            by_role[rr] += 1
    return {"total": len(accounts), "byRole": by_role}


def _purge_user_records(target_email: str, user_id: str) -> None:
    db = get_db()
    for stmt, params in (
        ("DELETE FROM online_presence WHERE user_email = ? COLLATE NOCASE", (target_email,)),
        ("DELETE FROM grades WHERE student_email = ? COLLATE NOCASE", (target_email,)),
        ("DELETE FROM grades WHERE professor_email = ? COLLATE NOCASE", (target_email,)),
        ("DELETE FROM reclamations WHERE student_email = ? COLLATE NOCASE", (target_email,)),
        ("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,)),
        ("DELETE FROM password_reset_tokens WHERE user_id = ?", (user_id,)),
        ("DELETE FROM users WHERE id = ?", (user_id,)),
    ):
        try:
            db.execute(stmt, params)
        except Exception:
            pass
    db.commit()


def delete_platform_account(actor: dict, email: str) -> dict:
    if actor.get("role") != "superadmin":
        raise ValueError("FORBIDDEN")
    target_email = validate_email_strict(email) or clean_email(email)
    if not target_email:
        raise ValueError("INVALID_INPUT")
    if target_email.lower() == str(actor.get("email", "")).lower():
        raise ValueError("CANNOT_DELETE_SELF")

    target = find_user_by_email(target_email)
    if not target:
        raise ValueError("NOT_FOUND")
    if target.get("role") not in PLATFORM_ROLES:
        raise ValueError("FORBIDDEN_TARGET")

    _purge_user_records(target_email, target["id"])
    return {"ok": True, "email": target_email, "role": target.get("role")}
