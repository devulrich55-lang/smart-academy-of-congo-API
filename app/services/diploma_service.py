from datetime import datetime, timezone

from app.database import get_db
from app.utils.platform_security import (
    generate_diploma_number,
    generate_verification_code,
    sign_diploma,
    uid,
)
from app.utils.sanitize import clean_text

ISSUER_ROLE = "universite"
DIPLOMA_TYPES = frozenset(
    {
        "licence",
        "master",
        "doctorat",
        "dut",
        "graduat",
        "certificat",
        "attestation",
        "autre",
    }
)
STATUSES = frozenset({"actif", "revoque"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_public(row) -> dict:
    return {
        "id": row["id"],
        "studentName": row["student_name"],
        "matricule": row["matricule"] or "",
        "universite": row["universite"],
        "universityName": row["university_name"] or "",
        "filiere": row["filiere"] or "",
        "niveau": row["niveau"] or "",
        "diplomaType": row["diploma_type"],
        "graduationYear": int(row["graduation_year"]),
        "diplomaNumber": row["diploma_number"],
        "issuedAt": row["issued_at"],
        "status": row["status"],
    }


def _row_to_manage(row) -> dict:
    out = _row_to_public(row)
    out.update(
        {
            "studentEmail": row["student_email"],
            "verificationCode": row["verification_code"],
            "hashSignature": row["hash_signature"],
            "issuedBy": row["issued_by"],
            "notes": row["notes"] or "",
            "revokedAt": row["revoked_at"],
            "revokedBy": row["revoked_by"],
        }
    )
    return out


def _assert_university(actor: dict) -> str:
    if actor.get("role") != ISSUER_ROLE:
        raise ValueError("FORBIDDEN")
    code = actor.get("universite") or actor.get("codeUni") or actor.get("sigle")
    if not code:
        raise ValueError("INVALID_INPUT")
    return clean_text(code, 80)


def _find_student_on_campus(email: str, universite: str) -> dict | None:
    row = get_db().execute(
        """SELECT email, prenom, nom, matricule, universite, filiere, niveau, nom_universite
           FROM users
           WHERE LOWER(email) = LOWER(?) AND role = 'etudiant' AND universite = ?
           LIMIT 1""",
        (email, universite),
    ).fetchone()
    if not row:
        return None
    name = " ".join(p for p in [row["prenom"], row["nom"]] if p).strip()
    return {
        "email": row["email"],
        "studentName": name or row["email"],
        "matricule": row["matricule"] or "",
        "filiere": row["filiere"] or "",
        "niveau": row["niveau"] or "",
        "universityName": row["nom_universite"] or "",
    }


def list_campus_diplomas(actor: dict) -> list[dict]:
    universite = _assert_university(actor)
    rows = get_db().execute(
        """SELECT * FROM diplomas
           WHERE universite = ?
           ORDER BY issued_at DESC""",
        (universite,),
    ).fetchall()
    return [_row_to_manage(r) for r in rows]


def list_student_diplomas(actor: dict) -> list[dict]:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")
    email = actor.get("email") or actor.get("identifiant")
    if not email:
        raise ValueError("AUTH_REQUIRED")
    rows = get_db().execute(
        """SELECT * FROM diplomas
           WHERE LOWER(student_email) = LOWER(?) AND status = 'actif'
           ORDER BY issued_at DESC""",
        (email,),
    ).fetchall()
    return [_row_to_public(r) for r in rows]


def issue_diploma(actor: dict, data: dict) -> dict:
    universite = _assert_university(actor)
    student_email = clean_text(data.get("studentEmail"), 255).lower()
    if not student_email or "@" not in student_email:
        raise ValueError("INVALID_INPUT")

    student = _find_student_on_campus(student_email, universite)
    if not student:
        raise ValueError("STUDENT_NOT_FOUND")

    diploma_type = clean_text(data.get("diplomaType"), 40) or "licence"
    if diploma_type not in DIPLOMA_TYPES:
        diploma_type = "autre"

    try:
        graduation_year = int(data.get("graduationYear") or datetime.now(timezone.utc).year)
    except (TypeError, ValueError):
        graduation_year = datetime.now(timezone.utc).year

    student_name = clean_text(data.get("studentName"), 200) or student["studentName"]
    matricule = clean_text(data.get("matricule"), 50) or student["matricule"]
    filiere = clean_text(data.get("filiere"), 120) or student["filiere"]
    niveau = clean_text(data.get("niveau"), 40) or student["niveau"]
    university_name = clean_text(data.get("universityName"), 200) or student["universityName"]
    notes = clean_text(data.get("notes"), 400)

    diploma_number = generate_diploma_number(universite, graduation_year)
    verification_code = generate_verification_code()
    sign_payload = {
        "diplomaNumber": diploma_number,
        "verificationCode": verification_code,
        "studentEmail": student_email,
        "universite": universite,
        "graduationYear": graduation_year,
        "diplomaType": diploma_type,
    }
    hash_signature = sign_diploma(sign_payload)
    now = _now()
    item_id = uid("dip")

    get_db().execute(
        """INSERT INTO diplomas (
            id, student_email, student_name, matricule, universite, university_name,
            filiere, niveau, diploma_type, graduation_year, diploma_number,
            verification_code, hash_signature, status, issued_by, issued_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'actif', ?, ?, ?)""",
        (
            item_id,
            student_email,
            student_name,
            matricule,
            universite,
            university_name,
            filiere,
            niveau,
            diploma_type,
            graduation_year,
            diploma_number,
            verification_code,
            hash_signature,
            actor.get("email") or actor.get("identifiant") or "",
            now,
            notes,
        ),
    )
    get_db().commit()

    row = get_db().execute("SELECT * FROM diplomas WHERE id = ?", (item_id,)).fetchone()
    return _row_to_manage(row)


def revoke_diploma(actor: dict, diploma_id: str) -> dict:
    universite = _assert_university(actor)
    row = get_db().execute(
        "SELECT * FROM diplomas WHERE id = ?", (clean_text(diploma_id, 80),)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    if row["universite"] != universite:
        raise ValueError("FORBIDDEN")
    if row["status"] == "revoque":
        return _row_to_manage(row)

    now = _now()
    get_db().execute(
        """UPDATE diplomas SET status = 'revoque', revoked_at = ?, revoked_by = ?
           WHERE id = ?""",
        (now, actor.get("email") or actor.get("identifiant") or "", row["id"]),
    )
    get_db().commit()
    updated = get_db().execute("SELECT * FROM diplomas WHERE id = ?", (row["id"],)).fetchone()
    return _row_to_manage(updated)


def verify_diploma_public(verification_code: str, diploma_number: str) -> dict:
    code = clean_text(verification_code, 80).upper()
    number = clean_text(diploma_number, 80).upper()
    if not code or not number:
        return {"valid": False, "message": "Code et numéro de diplôme requis."}

    row = get_db().execute(
        """SELECT * FROM diplomas
           WHERE UPPER(diploma_number) = ? AND UPPER(verification_code) = ?
           LIMIT 1""",
        (number, code),
    ).fetchone()
    if not row:
        return {"valid": False, "message": "Aucun diplôme correspondant."}

    sign_payload = {
        "diplomaNumber": row["diploma_number"],
        "verificationCode": row["verification_code"],
        "studentEmail": row["student_email"],
        "universite": row["universite"],
        "graduationYear": int(row["graduation_year"]),
        "diplomaType": row["diploma_type"],
    }
    expected = sign_diploma(sign_payload)
    if expected != row["hash_signature"]:
        return {"valid": False, "message": "Signature cryptographique invalide."}

    if row["status"] != "actif":
        return {
            "valid": False,
            "message": "Diplôme révoqué ou non actif.",
            "diploma": _row_to_public(row),
        }

    return {"valid": True, "diploma": _row_to_public(row)}
