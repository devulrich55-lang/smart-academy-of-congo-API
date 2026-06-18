import json
import re
from datetime import datetime, timedelta, timezone

from app.database import get_db, is_duplicate_key_error
from app.utils.platform_security import (
    assert_campus_access,
    generate_diploma_number,
    generate_verification_code,
    sign_diploma,
    uid,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _active_presence_cutoff(seconds: int = 90) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def compute_grade_avg(cc: float, exam: float) -> float:
    """Moyenne /20 : CC (/40) × 40 % + Examen (/60) × 60 %."""
    c = max(0.0, min(40.0, float(cc or 0)))
    e = max(0.0, min(60.0, float(exam or 0)))
    return round(c * 0.4 + e * 0.6 + 1e-9, 1)


def compute_grade_status(avg: float) -> str:
    return "Validé" if avg >= 10 else "Rattrapage"


def _row_to_grade(r) -> dict:
    return {
        "id": r["id"],
        "studentEmail": r["student_email"],
        "studentMatricule": r["student_matricule"],
        "professorEmail": r["professor_email"],
        "universite": r["universite"],
        "filiere": r["filiere"],
        "niveau": r["niveau"],
        "semester": r["semester"],
        "courseCode": r["course_code"],
        "courseName": r["course_name"],
        "classe": r["classe"],
        "credits": r["credits"],
        "cc": r["cc"],
        "exam": r["exam"],
        "avg": r["avg"],
        "status": r["status"],
        "updatedAt": r["updated_at"],
    }


def list_grades_for_student(
    email: str, universite: str, limit: int = 50, offset: int = 0
) -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM grades WHERE student_email = ? COLLATE NOCASE
           AND universite = ? ORDER BY semester DESC, course_name
           LIMIT ? OFFSET ?""",
        (email, universite, limit, offset),
    ).fetchall()
    return [_row_to_grade(r) for r in rows]


def list_grades_for_professor(
    prof_email: str, universite: str, limit: int = 50, offset: int = 0
) -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM grades WHERE professor_email = ? COLLATE NOCASE
           AND universite = ? ORDER BY updated_at DESC
           LIMIT ? OFFSET ?""",
        (prof_email, universite, limit, offset),
    ).fetchall()
    return [_row_to_grade(r) for r in rows]


def list_grades_for_campus(
    universite: str, limit: int = 50, offset: int = 0
) -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM grades WHERE universite = ?
           ORDER BY semester DESC, student_email, course_name
           LIMIT ? OFFSET ?""",
        (universite, limit, offset),
    ).fetchall()
    return [_row_to_grade(r) for r in rows]


def assert_transcript_access(actor: dict, student_email: str) -> None:
    """Vérifie que l'acteur peut consulter le relevé de l'étudiant."""
    email = (student_email or "").strip().lower()
    if not email:
        raise ValueError("INVALID_INPUT")

    role = actor.get("role")
    actor_email = (actor.get("email") or "").lower()

    if role == "etudiant":
        if actor_email != email:
            raise ValueError("FORBIDDEN")
        return

    db = get_db()
    sample = db.execute(
        """SELECT universite FROM grades WHERE student_email = ? COLLATE NOCASE LIMIT 1""",
        (email,),
    ).fetchone()

    if not sample:
        raise ValueError("NOT_FOUND")

    assert_campus_access(actor, sample["universite"])

    if role == "professeur":
        prof_row = db.execute(
            """SELECT 1 FROM grades WHERE student_email = ? COLLATE NOCASE
               AND professor_email = ? COLLATE NOCASE LIMIT 1""",
            (email, actor["email"]),
        ).fetchone()
        if not prof_row:
            raise ValueError("FORBIDDEN")


def get_transcript(actor: dict, student_email: str, semester: str) -> dict:
    assert_transcript_access(actor, student_email)
    email = student_email.strip().lower()
    db = get_db()
    rows = db.execute(
        """SELECT * FROM grades WHERE student_email = ? COLLATE NOCASE
           AND semester = ? ORDER BY course_name""",
        (email, semester),
    ).fetchall()

    if actor.get("role") == "professeur":
        rows = [
            r
            for r in rows
            if (r["professor_email"] or "").lower() == actor["email"].lower()
        ]

    grades = [_row_to_grade(r) for r in rows]
    uni = grades[0]["universite"] if grades else actor.get("universite")

    student_row = db.execute(
        """SELECT student_matricule, filiere, niveau FROM grades
           WHERE student_email = ? COLLATE NOCASE LIMIT 1""",
        (email,),
    ).fetchone()

    return {
        "studentEmail": email,
        "semester": semester,
        "universite": uni,
        "studentMatricule": student_row["student_matricule"] if student_row else None,
        "filiere": student_row["filiere"] if student_row else None,
        "niveau": student_row["niveau"] if student_row else None,
        "grades": grades,
    }


def upsert_grade(user: dict, data: dict) -> dict:
    assert_campus_access(user, data.get("universite"))
    if user.get("role") not in ("professeur", "universite"):
        raise ValueError("FORBIDDEN")
    db = get_db()
    ts = _now()
    cc = float(data["cc"])
    exam = float(data["exam"])
    avg = compute_grade_avg(cc, exam)
    status = compute_grade_status(avg)
    prof_email = user["email"]
    student_email = data["studentEmail"]
    course_code = data["courseCode"]
    semester = data["semester"]

    existing = db.execute(
        """SELECT id FROM grades WHERE student_email = ? COLLATE NOCASE
           AND course_code = ? AND semester = ? AND professor_email = ? COLLATE NOCASE""",
        (student_email, course_code, semester, prof_email),
    ).fetchone()
    grd_id = data.get("id") or (existing["id"] if existing else uid("grd"))

    if existing:
        db.execute(
            """UPDATE grades SET cc=?, exam=?, avg=?, status=?, updated_at=?,
               student_matricule=?, course_name=?, classe=?, filiere=?, niveau=?, credits=?
               WHERE id=? AND professor_email=? COLLATE NOCASE""",
            (
                cc,
                exam,
                avg,
                status,
                ts,
                data.get("studentMatricule"),
                data["courseName"],
                data.get("classe"),
                data.get("filiere"),
                data.get("niveau"),
                data.get("credits", 3),
                grd_id,
                prof_email,
            ),
        )
    else:
        db.execute(
            """INSERT INTO grades (id, student_email, student_matricule, professor_email,
               universite, filiere, niveau, semester, course_code, course_name, classe,
               credits, cc, exam, avg, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                grd_id,
                student_email,
                data.get("studentMatricule"),
                prof_email,
                data["universite"],
                data.get("filiere"),
                data.get("niveau"),
                semester,
                course_code,
                data["courseName"],
                data.get("classe"),
                data.get("credits", 3),
                cc,
                exam,
                avg,
                status,
                ts,
                ts,
            ),
        )
    db.commit()
    return {**data, "id": grd_id, "avg": avg, "status": status, "updatedAt": ts}


def upsert_grade_from_ai_validation(row: dict, final_grade: float, validated_by: str) -> dict:
    """Enregistre ou met à jour la cote CC après validation IA (note travail → CC /40)."""
    cc = round(max(0.0, min(20.0, float(final_grade))) * 2.5, 1)
    exam = 0.0
    avg = compute_grade_avg(cc, exam)
    status = compute_grade_status(avg)
    db = get_db()
    ts = _now()
    prof_email = row.get("professor_email") or validated_by
    student_email = row["student_email"]
    course_code = row["course_code"]
    semester = row["semester"]

    existing = db.execute(
        """SELECT id FROM grades WHERE student_email = ? COLLATE NOCASE
           AND course_code = ? AND semester = ?""",
        (student_email, course_code, semester),
    ).fetchone()
    grd_id = existing["id"] if existing else uid("grd")

    payload = {
        "studentEmail": student_email,
        "studentMatricule": row.get("student_matricule"),
        "universite": row["universite"],
        "filiere": row.get("filiere"),
        "niveau": row.get("niveau"),
        "semester": semester,
        "courseCode": course_code,
        "courseName": row["course_name"],
        "classe": row.get("classe"),
        "credits": 3,
        "cc": cc,
        "exam": exam,
    }

    if existing:
        db.execute(
            """UPDATE grades SET cc=?, exam=?, avg=?, status=?, updated_at=?,
               professor_email=?, student_matricule=?, course_name=?, classe=?
               WHERE id=?""",
            (
                cc,
                exam,
                avg,
                status,
                ts,
                prof_email,
                row.get("student_matricule"),
                row["course_name"],
                row.get("classe"),
                grd_id,
            ),
        )
    else:
        db.execute(
            """INSERT INTO grades (id, student_email, student_matricule, professor_email,
               universite, filiere, niveau, semester, course_code, course_name, classe,
               credits, cc, exam, avg, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                grd_id,
                student_email,
                row.get("student_matricule"),
                prof_email,
                row["universite"],
                row.get("filiere"),
                row.get("niveau"),
                semester,
                course_code,
                row["course_name"],
                row.get("classe"),
                3,
                cc,
                exam,
                avg,
                status,
                ts,
                ts,
            ),
        )
    db.commit()
    return {**payload, "id": grd_id, "avg": avg, "status": status, "updatedAt": ts}


def _row_to_library(r) -> dict:
    return {
        "id": r["id"],
        "universite": r["universite"],
        "title": r["title"],
        "author": r["author"],
        "category": r["category"],
        "description": r["description"],
        "fileUrl": r["file_url"],
        "coverUrl": r["cover_url"],
        "year": r["year"],
        "language": r["language"],
        "accessRoles": json.loads(r["access_roles"] or "[]"),
        "published": bool(r["published"]),
        "createdBy": r["created_by"],
        "createdAt": r["created_at"],
    }


def list_library(universite: str, role: str) -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM library_items WHERE universite = ? AND published = 1 ORDER BY title",
        (universite,),
    ).fetchall()
    out = []
    for r in rows:
        try:
            roles = json.loads(r["access_roles"] or "[]")
            if role not in roles:
                continue
        except json.JSONDecodeError:
            pass
        out.append(_row_to_library(r))
    return out


def create_library_item(user: dict, data: dict) -> dict:
    uni = assert_campus_access(user, data.get("universite"))
    if user.get("role") not in ("universite", "professeur", "assistant"):
        raise ValueError("FORBIDDEN")
    item_id = uid("lib")
    ts = _now()
    get_db().execute(
        """INSERT INTO library_items (id, universite, title, author, category, description,
           file_url, cover_url, year, language, access_roles, published, created_by, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            item_id,
            uni,
            data["title"],
            data.get("author", ""),
            data.get("category", "ouvrage"),
            data.get("description", ""),
            data.get("fileUrl", ""),
            data.get("coverUrl", ""),
            data.get("year"),
            data.get("language", "fr"),
            json.dumps(data.get("accessRoles") or ["etudiant", "professeur", "assistant"]),
            1 if data.get("published", True) else 0,
            user["email"],
            ts,
            ts,
        ),
    )
    get_db().commit()
    return {"id": item_id, **data, "universite": uni, "createdAt": ts}


def _row_to_career(r) -> dict:
    return {
        "id": r["id"],
        "universite": r["universite"],
        "scope": r["scope"],
        "type": r["type"],
        "title": r["title"],
        "organization": r["organization"],
        "location": r["location"],
        "description": r["description"],
        "requirements": r["requirements"],
        "deadline": r["deadline"],
        "contactEmail": r["contact_email"],
        "createdAt": r["created_at"],
    }


def list_careers(universite: str, scope_filter: str | None = None) -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM career_posts WHERE published = 1
           AND (universite = ? OR scope = 'national') ORDER BY created_at DESC""",
        (universite,),
    ).fetchall()
    if scope_filter == "national":
        return [_row_to_career(r) for r in rows if r["scope"] == "national"]
    return [_row_to_career(r) for r in rows]


def create_career_post(user: dict, data: dict) -> dict:
    uni = assert_campus_access(user, data.get("universite") or user.get("universite"))
    if user.get("role") not in ("universite", "professeur", "assistant"):
        raise ValueError("FORBIDDEN")
    job_id = uid("job")
    ts = _now()
    get_db().execute(
        """INSERT INTO career_posts (id, universite, scope, type, title, organization,
           location, description, requirements, deadline, contact_email, published, created_by, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            job_id,
            uni,
            data.get("scope", "campus"),
            data["type"],
            data["title"],
            data["organization"],
            data.get("location", ""),
            data["description"],
            data.get("requirements", ""),
            data.get("deadline"),
            data.get("contactEmail"),
            1,
            user["email"],
            ts,
            ts,
        ),
    )
    get_db().commit()
    return {"id": job_id, **data, "universite": uni, "createdAt": ts}


def _row_to_course(r) -> dict:
    return {
        "id": r["id"],
        "universite": r["universite"],
        "professorEmail": r["professor_email"],
        "title": r["title"],
        "description": r["description"],
        "filiere": r["filiere"],
        "niveau": r["niveau"],
        "modules": json.loads(r["modules"] or "[]"),
        "published": bool(r["published"]),
        "createdAt": r["created_at"],
    }


def list_courses(universite: str, filiere: str | None, niveau: str | None) -> list[dict]:
    q = "SELECT * FROM online_courses WHERE universite = ? AND published = 1"
    params: list = [universite]
    if filiere:
        q += " AND (filiere IS NULL OR filiere = ?)"
        params.append(filiere)
    if niveau:
        q += " AND (niveau IS NULL OR niveau = ?)"
        params.append(niveau)
    rows = get_db().execute(q, params).fetchall()
    return [_row_to_course(r) for r in rows]


def enroll_course(student_email: str, course_id: str) -> dict:
    db = get_db()
    course = db.execute(
        "SELECT id, universite FROM online_courses WHERE id = ?", (course_id,)
    ).fetchone()
    if not course:
        raise ValueError("NOT_FOUND")
    enr_id = uid("enr")
    ts = _now()
    try:
        db.execute(
            """INSERT INTO course_enrollments (id, course_id, student_email, progress, enrolled_at)
               VALUES (?,?,?,?,?)""",
            (enr_id, course_id, student_email, 0, ts),
        )
        db.commit()
        return {"id": enr_id, "courseId": course_id, "studentEmail": student_email, "progress": 0, "enrolledAt": ts}
    except Exception as e:
        if is_duplicate_key_error(e):
            return {"courseId": course_id, "studentEmail": student_email, "progress": 0}
        raise


def create_course(user: dict, data: dict) -> dict:
    assert_campus_access(user, data.get("universite"))
    if user.get("role") not in ("professeur", "universite"):
        raise ValueError("FORBIDDEN")
    crs_id = uid("crs")
    ts = _now()
    get_db().execute(
        """INSERT INTO online_courses (id, universite, professor_email, title, description,
           filiere, niveau, modules, published, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            crs_id,
            data["universite"],
            user["email"],
            data["title"],
            data.get("description", ""),
            data.get("filiere"),
            data.get("niveau"),
            json.dumps(data.get("modules") or []),
            1 if data.get("published") else 0,
            ts,
            ts,
        ),
    )
    get_db().commit()
    return {**data, "id": crs_id, "professorEmail": user["email"], "createdAt": ts}


def _row_to_social(r) -> dict:
    return {
        "id": r["id"],
        "universite": r["universite"],
        "authorEmail": r["author_email"],
        "authorName": r["author_name"],
        "authorRole": r["author_role"],
        "content": r["content"],
        "mediaUrl": r["media_url"],
        "audience": r["audience"],
        "filiere": r["filiere"],
        "likes": json.loads(r["likes"] or "[]"),
        "createdAt": r["created_at"],
    }


def list_social_posts(universite: str, filiere: str | None, limit: int = 50) -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM social_posts WHERE universite = ? ORDER BY created_at DESC LIMIT ?",
        (universite, limit),
    ).fetchall()
    return [
        _row_to_social(r)
        for r in rows
        if r["audience"] != "filiere" or not filiere or r["filiere"] == filiere
    ]


def create_social_post(user: dict, data: dict) -> dict:
    uni = assert_campus_access(user, data.get("universite") or user.get("universite"))
    if user.get("role") not in ("etudiant", "professeur", "assistant"):
        raise ValueError("FORBIDDEN")
    soc_id = uid("soc")
    ts = _now()
    name = " ".join(filter(None, [user.get("prenom"), user.get("nom")])) or user["email"]
    get_db().execute(
        """INSERT INTO social_posts (id, universite, author_email, author_name, author_role,
           content, media_url, audience, filiere, likes, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            soc_id,
            uni,
            user["email"],
            name,
            user["role"],
            str(data.get("content", ""))[:2000],
            data.get("mediaUrl"),
            data.get("audience", "campus"),
            data.get("filiere") or user.get("filiere"),
            "[]",
            ts,
            ts,
        ),
    )
    get_db().commit()
    row = get_db().execute(
        "SELECT * FROM social_posts WHERE id = ?", (soc_id,)
    ).fetchone()
    return _row_to_social(row)


def toggle_social_like(post_id: str, user_email: str) -> dict:
    db = get_db()
    row = db.execute("SELECT * FROM social_posts WHERE id = ?", (post_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    try:
        likes = json.loads(row["likes"] or "[]")
    except json.JSONDecodeError:
        likes = []
    if user_email in likes:
        likes.remove(user_email)
    else:
        likes.append(user_email)
    db.execute(
        "UPDATE social_posts SET likes = ?, updated_at = ? WHERE id = ?",
        (json.dumps(likes), _now(), post_id),
    )
    db.commit()
    return {"postId": post_id, "likes": likes}


def issue_diploma(user: dict, data: dict) -> dict:
    if user.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    campus = (
        data.get("universite")
        or user.get("universite")
        or user.get("nomUniversite")
        or user.get("codeUni")
    )
    uni = assert_campus_access(user, campus)
    dip_id = uid("dip")
    ts = _now()
    year = data.get("graduationYear") or datetime.now(timezone.utc).year
    diploma_number = data.get("diplomaNumber") or generate_diploma_number(uni, year)
    verification_code = generate_verification_code()
    payload = {
        "diplomaNumber": diploma_number,
        "studentEmail": data["studentEmail"],
        "matricule": data["matricule"],
        "universite": uni,
        "graduationYear": year,
        "filiere": data["filiere"],
    }
    hash_signature = sign_diploma(payload)
    get_db().execute(
        """INSERT INTO diplomas (id, universite, student_email, student_name, matricule, filiere,
           niveau, diploma_type, graduation_year, diploma_number, verification_code, hash_signature,
           status, issued_by, issued_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            dip_id,
            uni,
            data["studentEmail"],
            data["studentName"],
            data["matricule"],
            data["filiere"],
            data["niveau"],
            data.get("diplomaType", "Licence"),
            year,
            diploma_number,
            verification_code,
            hash_signature,
            "actif",
            user["email"],
            ts,
            ts,
        ),
    )
    get_db().commit()
    return {
        "id": dip_id,
        "diplomaNumber": diploma_number,
        "verificationCode": verification_code,
        "hashSignature": hash_signature[:16] + "…",
        "status": "actif",
        "issuedAt": ts,
    }


def verify_diploma(code: str, number: str) -> dict:
    row = get_db().execute(
        """SELECT * FROM diplomas WHERE verification_code = ? COLLATE NOCASE
           AND diploma_number = ? COLLATE NOCASE""",
        (code.strip(), number.strip()),
    ).fetchone()
    if not row:
        return {"valid": False, "message": "Aucun diplôme correspondant."}
    if row["status"] != "actif":
        return {
            "valid": False,
            "message": f"Diplôme {row['status']}. Contactez l'établissement.",
            "status": row["status"],
        }
    check = sign_diploma(
        {
            "diplomaNumber": row["diploma_number"],
            "studentEmail": row["student_email"],
            "matricule": row["matricule"],
            "universite": row["universite"],
            "graduationYear": row["graduation_year"],
            "filiere": row["filiere"],
        }
    )
    if check != row["hash_signature"]:
        return {"valid": False, "message": "Signature cryptographique invalide."}
    return {
        "valid": True,
        "diploma": {
            "studentName": row["student_name"],
            "matricule": row["matricule"],
            "universite": row["universite"],
            "filiere": row["filiere"],
            "niveau": row["niveau"],
            "diplomaType": row["diploma_type"],
            "graduationYear": row["graduation_year"],
            "diplomaNumber": row["diploma_number"],
            "issuedAt": row["issued_at"],
            "status": row["status"],
        },
    }


def list_diplomas_for_student(email: str) -> list[dict]:
    rows = get_db().execute(
        """SELECT id, diploma_number, verification_code, status, issued_at, universite,
           diploma_type, graduation_year FROM diplomas WHERE student_email = ? COLLATE NOCASE""",
        (email,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "diplomaNumber": r["diploma_number"],
            "verificationCode": r["verification_code"],
            "status": r["status"],
            "issuedAt": r["issued_at"],
            "universite": r["universite"],
            "diplomaType": r["diploma_type"],
            "graduationYear": r["graduation_year"],
        }
        for r in rows
    ]


ORIENTATION_DB = {
    "informatique": {
        "filieres": ["Informatique", "Génie logiciel", "Réseaux"],
        "stages": ["Développeur junior", "Admin réseau", "Support IT"],
        "skills": ["Programmation", "Bases de données", "Anglais technique"],
    },
    "medecine": {
        "filieres": ["Médecine", "Sciences infirmières", "Santé publique"],
        "stages": ["Hôpital", "ONG santé", "Laboratoire"],
        "skills": ["Biologie", "Éthique", "Communication patient"],
    },
    "droit": {
        "filieres": ["Droit", "Sciences politiques"],
        "stages": ["Cabinet juridique", "ONG", "Administration publique"],
        "skills": ["Argumentation", "Droit congolais", "Rédaction"],
    },
    "commerce": {
        "filieres": ["Gestion", "Comptabilité", "Marketing"],
        "stages": ["Banque", "Audit", "PME"],
        "skills": ["Excel", "Comptabilité", "Négociation"],
    },
}


def get_orientation_advice(profile: dict) -> dict:
    filiere = (profile.get("filiere") or "").lower()
    key = "commerce"
    if re.search(r"info|logiciel|réseau|data", filiere, re.I):
        key = "informatique"
    elif re.search(r"médec|santé|infirm", filiere, re.I):
        key = "medecine"
    elif re.search(r"droit|jurid|polit", filiere, re.I):
        key = "droit"
    pack = ORIENTATION_DB[key]
    niveau = profile.get("niveau") or "L1"
    next_level = {
        "L1": "L2",
        "L2": "L3",
        "L3": "Master",
    }.get(niveau, "Doctorat")
    return {
        "domain": key,
        "recommendedFilieres": pack["filieres"],
        "suggestedInternships": pack["stages"],
        "skillsToDevelop": pack["skills"],
        "academicPath": f"Vous êtes en {niveau}. Prochaine étape conseillée : {next_level}.",
        "message": f"Orientation personnalisée pour {profile.get('filiere') or 'votre filière'} à {profile.get('universite') or 'votre université'}. Consultez aussi le service orientation de votre campus.",
        "disclaimer": "Conseil indicatif — ne remplace pas un conseiller académique officiel.",
    }


def _sanitize_room_name(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "sac-live").lower()).strip("-")
    return slug[:48] or "sac-live"


def _row_to_live_session(row) -> dict:
    return {
        "id": row["id"],
        "universite": row["universite"],
        "professorEmail": row["professor_email"],
        "professorName": row["professor_name"],
        "courseCode": row["course_code"],
        "title": row["title"],
        "description": row["description"],
        "roomName": row["room_name"],
        "status": row["status"],
        "filiere": row["filiere"],
        "niveau": row["niveau"],
        "scheduledAt": row["scheduled_at"],
        "startedAt": row["started_at"],
        "endedAt": row["ended_at"],
        "recordingUrl": row["recording_url"],
        "joinUrl": f"https://meet.jit.si/{row['room_name']}",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _notify_live(
    email: str,
    role: str,
    ntype: str,
    title: str,
    message: str,
    session_id: str,
    universite: str,
):
    get_db().execute(
        """INSERT INTO correction_notifications
           (id, recipient_email, recipient_role, type, title, message, submission_id, universite, read, created_at)
           VALUES (?,?,?,?,?,?,?,?,0,?)""",
        (uid("ntf"), email, role, ntype, title, message, session_id, universite, _now()),
    )


def _students_for_live(universite: str, filiere: str | None, niveau: str | None) -> list[str]:
    q = "SELECT email FROM users WHERE role='etudiant' AND universite=?"
    params: list = [universite]
    if filiere:
        q += " AND (filiere IS NULL OR filiere = ? OR filiere LIKE ?)"
        params.extend([filiere, f"%{filiere[:20]}%"])
    if niveau:
        q += " AND (niveau IS NULL OR niveau = ?)"
        params.append(niveau)
    rows = get_db().execute(q, params).fetchall()
    return [r["email"] for r in rows]


def create_live_session(user: dict, data: dict) -> dict:
    if user.get("role") not in ("professeur", "universite"):
        raise ValueError("FORBIDDEN")
    uni = data.get("universite") or user.get("universite")
    assert_campus_access(user, uni)
    session_id = uid("live")
    room = f"sac-{uni}-{_sanitize_room_name(data.get('title', session_id))}-{session_id[-6:]}"
    now = _now()
    prof_name = f"{user.get('prenom', '')} {user.get('nom', '')}".strip() or user["email"]
    get_db().execute(
        """INSERT INTO live_sessions (
          id, universite, professor_email, professor_name, course_code, title, description,
          room_name, status, filiere, niveau, scheduled_at, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id,
            uni,
            user["email"],
            prof_name,
            data.get("courseCode"),
            data["title"],
            data.get("description", ""),
            room,
            "scheduled",
            data.get("filiere"),
            data.get("niveau"),
            data.get("scheduledAt") or now,
            now,
            now,
        ),
    )
    students = _students_for_live(uni, data.get("filiere"), data.get("niveau"))
    for email in students:
        _notify_live(
            email,
            "etudiant",
            "live_session_scheduled",
            "Nouveau cours en direct programmé",
            f"« {data['title']} » — votre professeur a programmé une session. Rejoignez quand elle démarre.",
            session_id,
            uni,
        )
    get_db().commit()
    return get_live_session(session_id)


def list_live_sessions(user: dict) -> list[dict]:
    uni = user.get("universite")
    role = user.get("role")
    if role in ("professeur", "universite"):
        rows = get_db().execute(
            """SELECT * FROM live_sessions WHERE universite=? AND professor_email=? COLLATE NOCASE
               ORDER BY created_at DESC LIMIT 50""",
            (uni, user["email"]),
        ).fetchall()
        if role == "universite" and not rows:
            rows = get_db().execute(
                "SELECT * FROM live_sessions WHERE universite=? ORDER BY created_at DESC LIMIT 50",
                (uni,),
            ).fetchall()
    else:
        q = "SELECT * FROM live_sessions WHERE universite=? AND status IN ('scheduled','live','ended')"
        params: list = [uni]
        filiere = user.get("filiere")
        niveau = user.get("niveau")
        if filiere:
            q += " AND (filiere IS NULL OR filiere = '' OR filiere = ?)"
            params.append(filiere)
        if niveau:
            q += " AND (niveau IS NULL OR niveau = '' OR niveau = ?)"
            params.append(niveau)
        q += " ORDER BY CASE status WHEN 'live' THEN 0 WHEN 'scheduled' THEN 1 ELSE 2 END, created_at DESC LIMIT 50"
        rows = get_db().execute(q, params).fetchall()
    return [_row_to_live_session(r) for r in rows]


def get_live_session(session_id: str) -> dict | None:
    row = get_db().execute("SELECT * FROM live_sessions WHERE id=?", (session_id,)).fetchone()
    return _row_to_live_session(row) if row else None


def start_live_session(user: dict, session_id: str) -> dict:
    if user.get("role") not in ("professeur", "universite"):
        raise ValueError("FORBIDDEN")
    row = get_db().execute("SELECT * FROM live_sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    assert_campus_access(user, row["universite"])
    if (row["professor_email"] or "").lower() != (user.get("email") or "").lower():
        if user.get("role") != "universite":
            raise ValueError("FORBIDDEN")
    now = _now()
    get_db().execute(
        "UPDATE live_sessions SET status='live', started_at=?, updated_at=? WHERE id=?",
        (now, now, session_id),
    )
    students = _students_for_live(row["universite"], row["filiere"], row["niveau"])
    for email in students:
        _notify_live(
            email,
            "etudiant",
            "live_session_start",
            "Cours en direct — démarré",
            f"« {row['title']} » est en direct. Rejoignez la salle maintenant.",
            session_id,
            row["universite"],
        )
    get_db().commit()
    return get_live_session(session_id)


def end_live_session(user: dict, session_id: str, recording_url: str | None = None) -> dict:
    if user.get("role") not in ("professeur", "universite"):
        raise ValueError("FORBIDDEN")
    row = get_db().execute("SELECT * FROM live_sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    assert_campus_access(user, row["universite"])
    if (row["professor_email"] or "").lower() != (user.get("email") or "").lower():
        if user.get("role") != "universite":
            raise ValueError("FORBIDDEN")
    now = _now()
    get_db().execute(
        """UPDATE live_sessions SET status='ended', ended_at=?, recording_url=?, updated_at=?
           WHERE id=?""",
        (now, recording_url or row["recording_url"], now, session_id),
    )
    students = _students_for_live(row["universite"], row["filiere"], row["niveau"])
    for email in students:
        msg = f"« {row['title']} » est terminé."
        if recording_url:
            msg += " L'enregistrement est disponible."
        _notify_live(
            email,
            "etudiant",
            "live_session_ended",
            "Cours en direct — terminé",
            msg,
            session_id,
            row["universite"],
        )
    get_db().commit()
    return get_live_session(session_id)


def join_live_session(user: dict, session_id: str) -> dict:
    row = get_db().execute("SELECT * FROM live_sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    assert_campus_access(user, row["universite"])
    if row["status"] not in ("scheduled", "live"):
        raise ValueError("SESSION_ENDED")
    attendee_id = uid("att")
    name = f"{user.get('prenom', '')} {user.get('nom', '')}".strip() or user["email"]
    now = _now()
    try:
        get_db().execute(
            """INSERT INTO live_attendees (id, session_id, student_email, student_name, joined_at)
               VALUES (?,?,?,?,?)""",
            (attendee_id, session_id, user["email"], name, now),
        )
    except Exception as e:
        if not is_duplicate_key_error(e):
            raise
    get_db().commit()
    session = get_live_session(session_id)
    session["displayName"] = name
    return session


def update_live_recording(user: dict, session_id: str, recording_url: str) -> dict:
    if user.get("role") not in ("professeur", "universite"):
        raise ValueError("FORBIDDEN")
    row = get_db().execute("SELECT * FROM live_sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    assert_campus_access(user, row["universite"])
    get_db().execute(
        "UPDATE live_sessions SET recording_url=?, updated_at=? WHERE id=?",
        (recording_url, _now(), session_id),
    )
    get_db().commit()
    return get_live_session(session_id)


def upsert_presence(user: dict, data: dict | None = None) -> dict:
    payload = data or {}
    now = _now()
    db = get_db()

    classe = payload.get("classe") or user.get("classe")
    filiere = payload.get("filiere") or user.get("filiere")
    section_id = payload.get("sectionId") or user.get("sectionId")
    role = user.get("role") or "etudiant"
    email = (user.get("email") or "").strip().lower()
    universite = user.get("universite")

    if not email or not universite:
        raise ValueError("INVALID_INPUT")

    existing = db.execute(
        "SELECT user_email FROM online_presence WHERE user_email=? COLLATE NOCASE",
        (email,),
    ).fetchone()
    if existing:
        db.execute(
            """UPDATE online_presence
               SET role=?, universite=?, filiere=?, section_id=?, classe=?, updated_at=?
               WHERE user_email=? COLLATE NOCASE""",
            (role, universite, filiere, section_id, classe, now, email),
        )
    else:
        db.execute(
            """INSERT INTO online_presence
               (id, user_email, role, universite, filiere, section_id, classe, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (uid("prs"), email, role, universite, filiere, section_id, classe, now),
        )
    db.commit()
    return {"ok": True, "updatedAt": now}


def section_presence_summary(user: dict) -> dict:
    if user.get("role") not in ("section", "universite", "assistant"):
        raise ValueError("FORBIDDEN")
    uni = user.get("universite")
    if not uni:
        raise ValueError("INVALID_INPUT")

    section_id = user.get("sectionId")
    filiere = user.get("filiere")
    cutoff = _active_presence_cutoff(90)
    db = get_db()

    q = """SELECT user_email, role, classe, updated_at FROM online_presence
           WHERE universite=? AND updated_at >= ?"""
    params: list = [uni, cutoff]
    if section_id:
        q += " AND (section_id = ? OR (section_id IS NULL AND filiere = ?))"
        params.extend([section_id, filiere])
    elif filiere:
        q += " AND filiere = ?"
        params.append(filiere)
    q += " ORDER BY updated_at DESC"

    rows = db.execute(q, params).fetchall()
    role_counts = {"etudiant": 0, "professeur": 0, "section": 0, "assistant": 0, "universite": 0}
    for r in rows:
        rr = r["role"] or ""
        if rr in role_counts:
            role_counts[rr] += 1

    return {
        "onlineCount": len(rows),
        "roleCounts": role_counts,
        "updatedAt": _now(),
    }


def professor_presence_by_class(user: dict) -> dict:
    if user.get("role") != "professeur":
        raise ValueError("FORBIDDEN")
    uni = user.get("universite")
    if not uni:
        raise ValueError("INVALID_INPUT")

    raw_classes = user.get("coursClasses") or []
    class_names: list[str] = []
    for item in raw_classes:
        if not isinstance(item, dict):
            continue
        classe = str(item.get("classe") or "").strip()
        if classe and classe not in class_names:
            class_names.append(classe)

    cutoff = _active_presence_cutoff(90)
    db = get_db()
    rows = db.execute(
        """SELECT classe, COUNT(*) AS c FROM online_presence
           WHERE universite=? AND role='etudiant' AND updated_at >= ?
           GROUP BY classe""",
        (uni, cutoff),
    ).fetchall()
    counts = {str(r["classe"] or ""): int(r["c"] or 0) for r in rows}

    classes = []
    total = 0
    for name in class_names:
        c = counts.get(name, 0)
        total += c
        classes.append({"classe": name, "onlineCount": c})

    return {"onlineCount": total, "classes": classes, "updatedAt": _now()}
