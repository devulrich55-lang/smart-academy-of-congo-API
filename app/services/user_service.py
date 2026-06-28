import json
import uuid
from datetime import datetime, timezone

from passlib.context import CryptContext

from app.config import settings
from app.database import get_db, row_to_user
from app.services import email_service
from app.utils.campus_catalog import get_by_id, normalize_profile_campus, registered_campus, resolve_campus_id
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
LOGO_URL_MAX_LEN = 4_000_000


def _clean_logo_url(val: object) -> str | None:
    if not val or not isinstance(val, str):
        return None
    s = val.strip()
    if not s.startswith("data:image/") or len(s) > LOGO_URL_MAX_LEN:
        return None
    return s


def get_display_name_from_user(user: dict | None) -> str:
    return get_display_name(user) if user else "Utilisateur"


def _campus_label(campus_id: str | None) -> str:
    item = get_by_id(campus_id or "")
    if item:
        return f"{item['name']} ({item['sigle']})"
    return campus_id or "votre campus"


def _send_inscription_decision_email(student: dict, status: str, reason: str = "") -> None:
    if not email_service.smtp_configured():
        return
    to = student.get("email")
    if not to:
        return
    name = " ".join(p for p in [student.get("prenom"), student.get("nom")] if p).strip() or "Étudiant"
    campus = _campus_label(student.get("universite"))
    filiere = student.get("filiere") or "—"
    if status == "approved":
        title = "Inscription validée"
        message = (
            f"Bonjour {name}, votre inscription étudiant sur Smart Academy of Congo "
            f"a été validée ({campus}, filière {filiere}). "
            "Connectez-vous avec votre e-mail ou matricule et votre mot de passe."
        )
        action_url = f"{settings.frontend_url}/connexion.html"
    else:
        title = "Inscription refusée"
        message = (
            f"Bonjour {name}, votre inscription ({campus}, filière {filiere}) "
            "n'a pas été acceptée par votre section."
        )
        if reason:
            message += f" Motif : {reason}."
        message += " Contactez le chef de section ou l'administration de votre université."
        action_url = f"{settings.frontend_url}/attente-validation.html?status=rejected"
    email_service.send_platform_notification_email(to, title, message, action_url)


def _notify_section_pending_inscription(student: dict) -> None:
    if not email_service.smtp_configured() or student.get("role") != "etudiant":
        return
    payment = student.get("payment") if isinstance(student.get("payment"), dict) else {}
    if payment.get("method") == "section_delegate":
        return
    if payment.get("sectionApproval") in ("approved", "rejected"):
        return
    if payment.get("status") == "verified" and (
        payment.get("method") in ("section_validation", "superadmin_validation")
        or payment.get("verifiedBy")
    ):
        return

    section_id = student.get("sectionId")
    recipients: set[str] = set()
    db = get_db()
    if section_id:
        sec = db.execute(
            "SELECT email FROM faculty_sections WHERE id = ?", (section_id,)
        ).fetchone()
        if sec and sec["email"]:
            recipients.add(str(sec["email"]).strip().lower())
        for row in db.execute(
            "SELECT email FROM users WHERE role = 'section' AND section_id = ?",
            (section_id,),
        ).fetchall():
            if row["email"]:
                recipients.add(str(row["email"]).strip().lower())

    if not recipients:
        return

    name = " ".join(p for p in [student.get("prenom"), student.get("nom")] if p).strip()
    title = "Nouvelle inscription en attente"
    message = (
        f"{name or student.get('email')} ({student.get('email')}) "
        f"s'est inscrit et attend votre validation "
        f"(filière {student.get('filiere') or '—'}, "
        f"matricule {student.get('matricule') or '—'})."
    )
    action_url = f"{settings.frontend_url}/dashboard-section.html"
    for to in recipients:
        email_service.send_platform_notification_email(to, title, message, action_url)


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
    role = clean_role(profile.get("role"))

    if role in ("ministere", "superadmin"):
        if not phone:
            phone = None
        elif find_user_by_phone(phone):
            raise ValueError("PHONE_EXISTS")
        if not validate_person_name_text(profile.get("prenom")) or not validate_person_name_text(
            profile.get("nom")
        ):
            raise ValueError("INVALID_PROFILE")
        return phone

    if not phone:
        raise ValueError("INVALID_PHONE")
    if find_user_by_phone(phone):
        raise ValueError("PHONE_EXISTS")

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
        is_rector = (
            str(profile.get("sectionKind") or "").lower() == "recteur"
            or profile.get("isRector") is True
            or not profile.get("sectionId")
        )
        if not is_rector and not profile.get("sectionId"):
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


def migrate_user_campus_if_needed(user: dict) -> dict:
    """Réaligne les anciens comptes (ex. UNIKIN → unkin) sur le catalogue SAC."""
    if not user:
        return user
    canonical = registered_campus(user)
    if not canonical or user.get("universite") == canonical:
        return user
    now = datetime.now(timezone.utc).isoformat()
    get_db().execute(
        "UPDATE users SET universite = ?, updated_at = ? WHERE id = ?",
        (canonical, now, user["id"]),
    )
    get_db().commit()
    refreshed = find_user_by_id(user["id"])
    return refreshed or {**user, "universite": canonical}


def create_user(profile: dict) -> dict:
    email = validate_email_strict(profile.get("email")) or clean_email(profile.get("email"))
    role = clean_role(profile.get("role"))
    if not email or not role:
        raise ValueError("INVALID_PROFILE")

    profile = normalize_profile_campus(dict(profile))
    phone_normalized = _assert_unique_identity(profile, email)
    password_hash = pwd_context.hash(profile["password"])
    raw_campus = (
        profile.get("universite")
        or profile.get("universiteLocked")
        or (profile.get("sigle") if role == "universite" else None)
        or (profile.get("codeUni") if role == "universite" else None)
    )
    universite_locked = resolve_campus_id(raw_campus) or clean_text(raw_campus, 50)
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    section_id = clean_text(profile.get("sectionId"), 80) or None
    if role in ("etudiant", "professeur", "assistant"):
        if not profile.get("payment"):
            profile["payment"] = {
                "status": "pending_verification",
                "sectionApproval": "pending",
            }
        elif isinstance(profile.get("payment"), dict):
            pay = dict(profile["payment"])
            if pay.get("sectionApproval") not in ("approved", "rejected", "pending"):
                pay["sectionApproval"] = "pending"
            if not pay.get("status"):
                pay["status"] = "pending_verification"
            profile["payment"] = pay
    if role == "etudiant":
        from app.services.reclamation_service import find_section_for_student

        if not section_id:
            sec = find_section_for_student(
                universite_locked,
                profile.get("filiere"),
                profile.get("sectionId"),
            )
            if sec:
                section_id = sec["id"]

    get_db().execute(
        """INSERT INTO users (
          id, email, password_hash, role, prenom, nom, telephone, universite,
          filiere, niveau, matricule, date_naissance, departement, grade, service,
          fonction, num_employe, num_assist, nom_universite, sigle, ville, adresse,
          nb_etudiants, site_web, responsable, code_uni, cours_classes, payment,
          inscription_fee, classe, section_id, logo_url, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            section_id,
            _clean_logo_url(profile.get("logoUrl")),
            now,
            now,
        ),
    )
    get_db().commit()
    created = find_user_by_id(user_id)
    if created and role == "etudiant":
        from app.services.reclamation_service import find_section_for_student

        sec = find_section_for_student(
            created.get("universite") or "",
            created.get("filiere"),
            created.get("sectionId"),
        )
        if sec and created.get("sectionId") != sec["id"]:
            now_link = datetime.now(timezone.utc).isoformat()
            get_db().execute(
                "UPDATE users SET section_id = ?, updated_at = ? WHERE id = ?",
                (sec["id"], now_link, created["id"]),
            )
            get_db().commit()
            created = find_user_by_id(user_id) or created
        _notify_section_pending_inscription(created)
    return created


def _resolve_section_head_faculty(actor: dict) -> tuple[str | None, object | None]:
    from app.services.reclamation_service import find_section_for_student
    from app.utils.campus_catalog import same_campus

    section_id = actor.get("sectionId")
    if not section_id:
        return None, None
    db = get_db()
    row = db.execute(
        "SELECT * FROM faculty_sections WHERE id = ?", (section_id,)
    ).fetchone()
    if row:
        return section_id, row
    campus = _actor_campus(actor)
    for candidate in db.execute(
        "SELECT * FROM faculty_sections WHERE active = 1"
    ).fetchall():
        if same_campus(campus, candidate["universite"]) and _filiere_matches(
            actor.get("filiere"), candidate["filiere"] or candidate["name"]
        ):
            return candidate["id"], candidate
    sec = find_section_for_student(campus or "", actor.get("filiere"), section_id)
    if sec:
        row = db.execute(
            "SELECT * FROM faculty_sections WHERE id = ?", (sec["id"],)
        ).fetchone()
        if row:
            return sec["id"], row
    return section_id, None


def _align_section_head_section_id(user: dict) -> dict:
    if user.get("role") != "section" or _is_rector_actor(user):
        return user
    canonical, _row = _resolve_section_head_faculty(user)
    if not canonical or canonical == user.get("sectionId"):
        return user
    now = datetime.now(timezone.utc).isoformat()
    get_db().execute(
        "UPDATE users SET section_id = ?, updated_at = ? WHERE id = ?",
        (canonical, now, user["id"]),
    )
    get_db().commit()
    return find_user_by_id(user["id"]) or {**user, "sectionId": canonical}


def user_to_session(user: dict | None) -> dict | None:
    if not user:
        return None
    if user.get("role") == "section":
        user = _align_section_head_section_id(user)
    uni = registered_campus(user)
    is_uni = user.get("role") == "universite"
    is_institutional = user.get("role") in ("ministere", "superadmin")
    display_nom = user.get("nom", "")
    if is_uni:
        display_nom = user.get("nomUniversite") or user.get("email", "")
    elif is_institutional:
        display_nom = user.get("nom", "") or user.get("email", "")
    payment = user.get("payment") if isinstance(user.get("payment"), dict) else None
    section_approval = user.get("sectionApproval")
    if not section_approval and user.get("role") in ("etudiant", "professeur", "assistant"):
        if payment and payment.get("sectionApproval") in ("approved", "rejected", "pending"):
            section_approval = payment.get("sectionApproval")
        elif payment and payment.get("status") == "verified" and payment.get("method") in (
            "section_delegate",
            "section_validation",
            "superadmin_validation",
        ):
            section_approval = "approved"
        elif payment and payment.get("status") == "verified" and payment.get("verifiedBy"):
            section_approval = "approved"
        elif payment:
            section_approval = "pending"
        elif user.get("createdAt"):
            section_approval = "pending"
    return {
        "role": user["role"],
        "email": user["email"],
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
        "sectionApproval": section_approval,
        "sectionApprovalRequestedAt": user.get("createdAt"),
        "nomination": user.get("nomination"),
        "grade": user.get("grade"),
        "fonction": user.get("fonction"),
        "nomUniversite": user.get("nomUniversite"),
        "logoUrl": user.get("logoUrl"),
        "campusTariffs": user.get("campusTariffs"),
        "campusAcademicFees": user.get("campusAcademicFees"),
        "universityFees": user.get("universityFees"),
        "inscriptionFee": user.get("inscriptionFee"),
        "payment": payment,
        "paymentStatus": payment.get("status") if payment else None,
        "createdAt": user.get("createdAt"),
    }


def _is_rector_actor(actor: dict) -> bool:
    if actor.get("role") != "section":
        return False
    if not actor.get("sectionId"):
        return True
    label = " ".join(
        str(actor.get(k) or "")
        for k in ("sectionName", "nom", "prenom", "fonction", "nomination")
    ).lower()
    return "recteur" in label


def _is_section_head_actor(actor: dict) -> bool:
    if actor.get("role") == "section":
        return bool(actor.get("sectionId")) and not _is_rector_actor(actor)
    return (
        actor.get("role") == "professeur"
        and actor.get("nomination") == "chef_section"
        and bool(actor.get("sectionId"))
    )


def _section_head_section_id(actor: dict) -> str | None:
    if not _is_section_head_actor(actor):
        return None
    canonical, _row = _resolve_section_head_faculty(actor)
    return canonical


def _filiere_matches(a: str | None, b: str | None) -> bool:
    from app.services.reclamation_service import _norm

    fa, fb = _norm(a), _norm(b)
    if not fa or not fb:
        return False
    if fa == fb or fa in fb or fb in fa:
        return True
    fa_tokens = [w for w in fa.split() if len(w) >= 5]
    fb_tokens = set(fb.split())
    return bool(fa_tokens and any(t in fb_tokens for t in fa_tokens))


def _student_manageable_by_actor(student: dict, actor: dict) -> bool:
    from app.services.reclamation_service import find_section_for_student
    from app.utils.campus_catalog import same_campus

    if student.get("role") != "etudiant":
        return False
    actor_role = actor.get("role")
    campus = _actor_campus(actor)
    if not campus or not same_campus(student.get("universite"), campus):
        return False
    if actor_role in ("universite", "superadmin") or _is_rector_actor(actor):
        return True
    if actor_role == "section" or (actor_role == "professeur" and _is_section_head_actor(actor)):
        section_id, section_row = _resolve_section_head_faculty(actor)
        if not section_id:
            return False
        if student.get("sectionId") == section_id:
            return True
        resolved = find_section_for_student(
            student.get("universite") or "",
            student.get("filiere"),
            student.get("sectionId"),
        )
        if resolved and resolved["id"] == section_id:
            return True
        if section_row and _filiere_matches(
            student.get("filiere"),
            section_row["filiere"] or section_row["name"],
        ):
            return True
        if resolved and section_row and _filiere_matches(
            resolved.get("filiere") or resolved.get("name"),
            section_row["filiere"] or section_row["name"],
        ):
            return True
    return False


def _target_section_id_for_actor(actor: dict, student: dict) -> str | None:
    from app.services.reclamation_service import find_section_for_student

    head_section_id = _section_head_section_id(actor)
    if head_section_id:
        return head_section_id
    resolved = find_section_for_student(
        student.get("universite") or "",
        student.get("filiere"),
        student.get("sectionId"),
    )
    if resolved:
        return resolved["id"]
    return student.get("sectionId")


def _actor_campus(actor: dict) -> str | None:
    return registered_campus(actor) or clean_text(actor.get("universite"), 50)


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


def link_student_to_section(actor: dict, email: str, profile: dict | None = None) -> dict:
    target_email = validate_email_strict(email) or clean_email(email)
    if not target_email:
        raise ValueError("INVALID_INPUT")
    student = find_user_by_email(target_email)
    if not student or student.get("role") != "etudiant":
        raise ValueError("STUDENT_NOT_FOUND")
    payload = dict(profile or {})
    payload["email"] = target_email
    return _link_existing_student_to_section(actor, student, payload)


def _link_existing_student_to_section(actor: dict, student: dict, profile: dict) -> dict:
    if not _student_manageable_by_actor(student, actor):
        raise ValueError("FORBIDDEN")
    section_id = _target_section_id_for_actor(actor, student)
    now = datetime.now(timezone.utc).isoformat()
    payment = student.get("payment") if isinstance(student.get("payment"), dict) else {}
    if not payment:
        payment = {
            "status": "pending_verification",
            "sectionApproval": "pending",
        }
    get_db().execute(
        """UPDATE users SET section_id = ?, filiere = COALESCE(?, filiere),
           payment = ?, updated_at = ? WHERE id = ?""",
        (
            section_id or student.get("sectionId"),
            clean_text(profile.get("filiere"), 200) or None,
            json.dumps(payment),
            now,
            student["id"],
        ),
    )
    get_db().commit()
    return find_user_by_id(student["id"]) or student


def create_student_for_section(actor: dict, profile: dict) -> dict:
    actor_role = actor.get("role")
    if actor_role not in ("section", "universite", "professeur"):
        raise ValueError("FORBIDDEN")
    if actor_role == "professeur" and not _is_section_head_actor(actor):
        raise ValueError("FORBIDDEN")

    email = validate_email_strict(profile.get("email")) or clean_email(profile.get("email"))
    if email:
        existing = find_user_by_email(email)
        if existing and existing.get("role") == "etudiant":
            return _link_existing_student_to_section(actor, existing, profile)

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


def list_pending_students_for_section(actor: dict) -> list[dict]:
    db = get_db()
    section_id, section_row = _resolve_section_head_faculty(actor)
    rows = db.execute(
        """SELECT * FROM users WHERE role = 'etudiant'
           ORDER BY created_at DESC LIMIT 2000"""
    ).fetchall()
    out: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    dirty = False
    for row in rows:
        user = row_to_user(row)
        if not user:
            continue
        if section_id and section_row and not row["section_id"]:
            if _filiere_matches(
                user.get("filiere"),
                section_row["filiere"] or section_row["name"],
            ):
                db.execute(
                    "UPDATE users SET section_id = ?, updated_at = ? WHERE id = ?",
                    (section_id, now, row["id"]),
                )
                dirty = True
                user = row_to_user(
                    db.execute(
                        "SELECT * FROM users WHERE id = ?", (row["id"],)
                    ).fetchone()
                )
        if not _student_manageable_by_actor(user, actor):
            continue
        status = student_section_approval_status(user)
        if status != "pending":
            continue
        user["sectionApproval"] = status
        out.append(user)
    if dirty:
        db.commit()
    return out


def list_students_for_section(actor: dict) -> list[dict]:
    from app.services.reclamation_service import find_section_for_student
    from app.utils.campus_catalog import same_campus

    db = get_db()
    actor_role = actor.get("role")
    campus = _actor_campus(actor)

    if _is_rector_actor(actor) and campus:
        rows = db.execute(
            """SELECT * FROM users WHERE role = 'etudiant' ORDER BY created_at DESC"""
        ).fetchall()
        return [
            row_to_user(r)
            for r in rows
            if r and same_campus(row_to_user(r).get("universite"), campus)
        ]

    if actor_role == "section" or (actor_role == "professeur" and _is_section_head_actor(actor)):
        section_id = _section_head_section_id(actor)
        if not section_id:
            return []
        section_row = db.execute(
            "SELECT * FROM faculty_sections WHERE id = ?", (section_id,)
        ).fetchone()
        if not section_row:
            return []
        section_campus = section_row["universite"]
        rows = db.execute(
            """SELECT * FROM users WHERE role = 'etudiant' ORDER BY created_at DESC"""
        ).fetchall()
        matched: list[dict] = []
        now = datetime.now(timezone.utc).isoformat()
        dirty = False
        for row in rows:
            user = row_to_user(row)
            if not same_campus(user.get("universite"), section_campus):
                continue
            belongs = row["section_id"] == section_id
            if not belongs:
                resolved = find_section_for_student(
                    user.get("universite") or "",
                    user.get("filiere"),
                    row["section_id"],
                )
                belongs = bool(resolved and resolved["id"] == section_id)
            if not belongs:
                belongs = _filiere_matches(
                    user.get("filiere"),
                    section_row["filiere"] or section_row["name"],
                )
                if belongs and not row["section_id"]:
                    db.execute(
                        "UPDATE users SET section_id = ?, updated_at = ? WHERE id = ?",
                        (section_id, now, row["id"]),
                    )
                    dirty = True
                    user = row_to_user(
                        db.execute(
                            "SELECT * FROM users WHERE id = ?", (row["id"],)
                        ).fetchone()
                    )
            if belongs:
                matched.append(user)
        if dirty:
            db.commit()
        return matched
    elif actor_role == "universite" and campus:
        rows = db.execute(
            """SELECT * FROM users WHERE role = 'etudiant' ORDER BY created_at DESC"""
        ).fetchall()
        return [
            row_to_user(r)
            for r in rows
            if r and same_campus(row_to_user(r).get("universite"), campus)
        ]
    else:
        return []


def _resolve_section_id_for_student(student: dict, actor: dict) -> str | None:
    from app.services.reclamation_service import find_section_for_student
    from app.utils.campus_catalog import same_campus

    actor_role = actor.get("role")
    campus = _actor_campus(actor)
    if actor_role == "superadmin":
        sec = find_section_for_student(
            student.get("universite") or "",
            student.get("filiere"),
            student.get("sectionId"),
        )
        return sec["id"] if sec else student.get("sectionId")
    if actor_role == "universite":
        if not same_campus(student.get("universite"), campus):
            raise ValueError("FORBIDDEN")
        sec = find_section_for_student(
            student.get("universite") or "",
            student.get("filiere"),
            student.get("sectionId"),
        )
        return sec["id"] if sec else student.get("sectionId")

    if not _student_manageable_by_actor(student, actor):
        raise ValueError("FORBIDDEN")
    return _target_section_id_for_actor(actor, student)


def student_section_approval_status(user: dict | None) -> str:
    if not user or user.get("role") != "etudiant":
        return "approved"
    payment = user.get("payment") if isinstance(user.get("payment"), dict) else {}
    raw = user.get("sectionApproval") or payment.get("sectionApproval")
    if raw in ("approved", "rejected", "pending"):
        return raw
    if payment.get("status") == "verified" and (
        payment.get("method") in ("section_delegate", "section_validation", "superadmin_validation")
        or payment.get("verifiedBy")
    ):
        return "approved"
    if payment.get("status") == "rejected" or payment.get("sectionApproval") == "rejected":
        return "rejected"
    return "pending"


def list_students_for_platform_approval(actor: dict, status: str | None = "pending") -> list[dict]:
    if actor.get("role") != "superadmin":
        raise ValueError("FORBIDDEN")
    rows = get_db().execute(
        """SELECT * FROM users WHERE role = 'etudiant'
           ORDER BY datetime(created_at) DESC LIMIT 3000"""
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        user = row_to_user(row)
        st = student_section_approval_status(user)
        if status and status != "all" and st != status:
            continue
        user["sectionApproval"] = st
        out.append(user)
    return out


def set_student_section_approval(
    actor: dict, email: str, status: str, reason: str = ""
) -> dict:
    actor_role = actor.get("role")
    if actor_role not in ("section", "universite", "professeur", "superadmin"):
        raise ValueError("FORBIDDEN")
    if actor_role == "professeur" and not _is_section_head_actor(actor):
        raise ValueError("FORBIDDEN")
    if status not in ("approved", "rejected"):
        raise ValueError("INVALID_STATUS")

    target_email = validate_email_strict(email) or clean_email(email)
    if not target_email:
        raise ValueError("INVALID_INPUT")
    student = find_user_by_email(target_email)
    if not student or student.get("role") != "etudiant":
        raise ValueError("STUDENT_NOT_FOUND")

    section_id = _resolve_section_id_for_student(student, actor)

    payment = student.get("payment") if isinstance(student.get("payment"), dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    if status == "approved":
        default_method = "superadmin_validation" if actor_role == "superadmin" else "section_validation"
        payment = {
            **payment,
            "status": "verified",
            "method": payment.get("method") or default_method,
            "verifiedAt": now,
            "verifiedBy": actor.get("email") or actor.get("id"),
            "verifiedByRole": actor_role,
            "sectionApproval": "approved",
            "sectionApprovedAt": now,
        }
    else:
        payment = {
            **payment,
            "status": "rejected",
            "sectionApproval": "rejected",
            "rejectionReason": clean_text(reason, 300) or "",
            "rejectedAt": now,
            "rejectedBy": actor.get("email") or actor.get("id"),
        }

    get_db().execute(
        """UPDATE users SET section_id = ?, payment = ?, updated_at = ? WHERE id = ?""",
        (
            section_id or student.get("sectionId"),
            json.dumps(payment),
            now,
            student["id"],
        ),
    )
    get_db().commit()
    updated = find_user_by_id(student["id"])
    if updated:
        updated["sectionApproval"] = status
        _send_inscription_decision_email(
            updated,
            status,
            clean_text(reason, 300) if status == "rejected" else "",
        )
    return updated


def staff_section_approval_status(user: dict | None) -> str:
    if not user or user.get("role") not in ("professeur", "assistant"):
        return "approved"
    payment = user.get("payment") if isinstance(user.get("payment"), dict) else {}
    raw = payment.get("sectionApproval")
    if raw in ("approved", "rejected", "pending"):
        return raw
    if payment.get("status") == "verified" and (
        payment.get("method") in ("section_delegate", "section_validation", "superadmin_validation")
        or payment.get("verifiedBy")
    ):
        return "approved"
    if payment.get("status") == "rejected" or payment.get("sectionApproval") == "rejected":
        return "rejected"
    return "pending"


def _staff_manageable_by_actor(staff: dict, actor: dict) -> bool:
    from app.utils.campus_catalog import same_campus

    if staff.get("role") not in ("professeur", "assistant"):
        return False
    actor_role = actor.get("role")
    campus = _actor_campus(actor)
    if not campus or not same_campus(staff.get("universite"), campus):
        return False
    if actor_role in ("universite", "superadmin") or _is_rector_actor(actor):
        return True
    if actor_role == "section" or (actor_role == "professeur" and _is_section_head_actor(actor)):
        section_id, section_row = _resolve_section_head_faculty(actor)
        if section_id and staff.get("sectionId") == section_id:
            return True
        if section_row:
            sec_label = section_row["filiere"] or section_row["name"]
            for label in (
                staff.get("filiere"),
                staff.get("departement"),
                staff.get("service"),
            ):
                if label and _filiere_matches(label, sec_label):
                    return True
    return False


def set_staff_section_approval(
    actor: dict, email: str, status: str, reason: str = ""
) -> dict:
    actor_role = actor.get("role")
    if actor_role not in ("section", "universite", "professeur", "superadmin"):
        raise ValueError("FORBIDDEN")
    if actor_role == "professeur" and not _is_section_head_actor(actor):
        raise ValueError("FORBIDDEN")
    if status not in ("approved", "rejected"):
        raise ValueError("INVALID_STATUS")

    target_email = validate_email_strict(email) or clean_email(email)
    if not target_email:
        raise ValueError("INVALID_INPUT")
    staff = find_user_by_email(target_email)
    if not staff or staff.get("role") not in ("professeur", "assistant"):
        raise ValueError("STAFF_NOT_FOUND")

    if not _staff_manageable_by_actor(staff, actor):
        raise ValueError("FORBIDDEN")

    section_id = staff.get("sectionId")
    if actor_role == "section" or (actor_role == "professeur" and _is_section_head_actor(actor)):
        head_section_id = _section_head_section_id(actor)
        if head_section_id:
            section_id = head_section_id

    payment = staff.get("payment") if isinstance(staff.get("payment"), dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    if status == "approved":
        default_method = "superadmin_validation" if actor_role == "superadmin" else "section_validation"
        payment = {
            **payment,
            "status": "verified",
            "method": payment.get("method") or default_method,
            "verifiedAt": now,
            "verifiedBy": actor.get("email") or actor.get("id"),
            "verifiedByRole": actor_role,
            "sectionApproval": "approved",
            "sectionApprovedAt": now,
        }
    else:
        payment = {
            **payment,
            "status": "rejected",
            "sectionApproval": "rejected",
            "rejectionReason": clean_text(reason, 300) or "",
            "rejectedAt": now,
            "rejectedBy": actor.get("email") or actor.get("id"),
        }

    get_db().execute(
        """UPDATE users SET section_id = COALESCE(?, section_id), payment = ?, updated_at = ? WHERE id = ?""",
        (
            section_id,
            json.dumps(payment),
            now,
            staff["id"],
        ),
    )
    get_db().commit()
    updated = find_user_by_id(staff["id"])
    if updated:
        updated["sectionApproval"] = status
        _send_inscription_decision_email(
            updated,
            status,
            clean_text(reason, 300) if status == "rejected" else "",
        )
    return updated


def list_pending_staff_for_section(actor: dict) -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM users WHERE role IN ('professeur','assistant')
           ORDER BY created_at DESC LIMIT 2000"""
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        user = row_to_user(row)
        if not user:
            continue
        if not _staff_manageable_by_actor(user, actor):
            continue
        st = staff_section_approval_status(user)
        if st != "pending":
            continue
        user["sectionApproval"] = st
        out.append(user)
    return out


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
MAX_SUPERADMIN_ACCOUNTS = 2
ROLE_LABELS = {
    "superadmin": "Super Admin",
    "ministere": "Ministère",
    "universite": "Admin Université",
}


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
        "logoUrl": user.get("logoUrl"),
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


def get_campus_branding(campus_code: str) -> dict | None:
    code = clean_text(campus_code, 100)
    if not code:
        return None
    canonical = resolve_campus_id(code) or code
    db = get_db()
    rows = db.execute(
        """SELECT * FROM users WHERE role = 'universite' AND (
          universite = ? COLLATE NOCASE OR sigle = ? COLLATE NOCASE OR code_uni = ? COLLATE NOCASE
        ) LIMIT 1""",
        (canonical, canonical, canonical),
    ).fetchall()
    if not rows:
        like = f"%{canonical.lower()}%"
        rows = db.execute(
            """SELECT * FROM users WHERE role = 'universite' AND (
              LOWER(universite) = LOWER(?) OR LOWER(sigle) = LOWER(?)
              OR LOWER(code_uni) = LOWER(?) OR LOWER(nom_universite) LIKE ?
            ) LIMIT 1""",
            (canonical, canonical, canonical, like),
        ).fetchall()
    if not rows:
        return None
    user = row_to_user(rows[0])
    if not user:
        return None
    campus = registered_campus(user) or user.get("universite")
    return {
        "universite": campus,
        "sigle": user.get("sigle"),
        "codeUni": user.get("codeUni"),
        "nomUniversite": user.get("nomUniversite"),
        "logoUrl": user.get("logoUrl"),
    }


def count_superadmin_accounts() -> int:
    row = get_db().execute(
        "SELECT COUNT(*) AS c FROM users WHERE role = 'superadmin'"
    ).fetchone()
    return int(row["c"] or 0)


def superadmin_slots_remaining() -> int:
    return max(0, MAX_SUPERADMIN_ACCOUNTS - count_superadmin_accounts())


def institutional_admins_summary(actor: dict) -> dict:
    admins = list_institutional_admins(actor)
    by_role = {r: 0 for r in INSTITUTIONAL_ROLES}
    for admin in admins:
        if admin["role"] in by_role:
            by_role[admin["role"]] += 1
    super_count = count_superadmin_accounts()
    return {
        "total": len(admins),
        "byRole": by_role,
        "superadminLimit": MAX_SUPERADMIN_ACCOUNTS,
        "superadminCount": super_count,
        "superadminRemaining": max(0, MAX_SUPERADMIN_ACCOUNTS - super_count),
    }


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

    if role == "superadmin" and count_superadmin_accounts() >= MAX_SUPERADMIN_ACCOUNTS:
        raise ValueError("SUPERADMIN_LIMIT")

    payload: dict = {
        "email": email,
        "password": profile["password"],
        "role": role,
        "telephone": profile.get("telephone"),
        "prenom": profile.get("prenom"),
        "nom": profile.get("nom"),
    }
    if role == "ministere":
        fonction = clean_text(profile.get("fonction"), 50)
        if fonction:
            payload["fonction"] = fonction
    if role == "universite":
        campus = normalize_profile_campus(
            {
                "role": "universite",
                "universite": profile.get("universite") or profile.get("sigle"),
                "nomUniversite": profile.get("nomUniversite"),
                "sigle": profile.get("sigle"),
                "codeUni": profile.get("codeUni"),
            }
        )
        payload.update(
            {
                "nomUniversite": campus.get("nomUniversite") or profile.get("nomUniversite"),
                "responsable": profile.get("responsable"),
                "codeUni": campus.get("codeUni") or profile.get("codeUni"),
                "sigle": campus.get("sigle") or profile.get("sigle"),
                "ville": profile.get("ville"),
                "universite": campus.get("universite"),
                "logoUrl": profile.get("logoUrl"),
            }
        )
    created = create_user(payload)
    if created and role == "universite":
        faculty_sections = profile.get("facultySections") or []
        if faculty_sections:
            from app.services.reclamation_service import seed_faculty_sections_for_university

            seed_faculty_sections_for_university(
                created.get("id") or email,
                payload.get("universite") or "",
                faculty_sections,
            )
    return _institutional_row(created) if created else {}


def seed_faculty_sections_for_campus(campus: str, rows: list) -> list[dict]:
    from app.services.reclamation_service import seed_faculty_sections_for_university
    from app.utils.campus_catalog import resolve_campus_id, same_campus

    campus_id = resolve_campus_id(campus) or campus
    if not campus_id or not rows:
        return []
    db = get_db()
    uni_rows = db.execute(
        "SELECT id, email, universite FROM users WHERE role = 'universite'"
    ).fetchall()
    university_id = campus_id
    for row in uni_rows:
        if same_campus(campus_id, row["universite"]):
            university_id = row["id"] or row["email"] or campus_id
            break
    return seed_faculty_sections_for_university(university_id, campus_id, rows)


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
