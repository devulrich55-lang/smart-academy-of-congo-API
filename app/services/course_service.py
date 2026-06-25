from datetime import datetime, timezone

from app.database import get_db
from app.utils.platform_security import uid
from app.utils.sanitize import clean_text

MANAGE_ROLES = frozenset({"universite", "professeur"})
CATEGORIES = frozenset({"mooc", "revision", "td", "certifiant", "autre"})
NIVEAUX = frozenset({"l1", "l2", "l3", "master1", "master2", "doctorat", "tous"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _campus(actor: dict) -> str:
    return clean_text(
        actor.get("universite") or actor.get("codeUni") or actor.get("sigle"), 80
    )


def _row_to_item(row, enrolled: bool = False) -> dict:
    return {
        "id": row["id"],
        "code": row["code"] or "",
        "title": row["title"],
        "description": row["description"] or "",
        "category": row["category"] or "mooc",
        "universite": row["universite"],
        "universityName": row["university_name"] or "",
        "filiere": row["filiere"] or "",
        "niveau": row["niveau"] or "tous",
        "classe": row["classe"] or "",
        "professorEmail": row["professor_email"] or "",
        "professorName": row["professor_name"] or "",
        "coverUrl": row["cover_url"] or "",
        "resourceUrl": row["resource_url"] or "",
        "durationHours": row["duration_hours"] or 0,
        "credits": row["credits"] or 0,
        "published": bool(row["published"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "enrolled": enrolled,
        "courseCode": row["code"] or row["id"],
        "courseName": row["title"],
    }


def _assert_manage(actor: dict) -> str:
    if actor.get("role") not in MANAGE_ROLES:
        raise ValueError("FORBIDDEN")
    campus = _campus(actor)
    if not campus:
        raise ValueError("INVALID_INPUT")
    return campus


def _student_campus(actor: dict) -> str:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")
    campus = _campus(actor)
    if not campus:
        raise ValueError("INVALID_INPUT")
    return campus


def _match_student(row, actor: dict) -> bool:
    filiere = clean_text(actor.get("filiere"), 120)
    niveau = clean_text(actor.get("niveau"), 40)
    row_niv = row["niveau"] or "tous"
    row_fil = row["filiere"] or ""
    if row_niv not in ("", "tous") and niveau:
        if row_niv.lower() != niveau.lower().replace(" ", ""):
            # soft match: l2 vs licence 2 handled loosely
            if row_niv not in niveau.lower():
                return False
    if row_fil and filiere:
        rf = row_fil.lower()
        sf = filiere.lower()
        if rf not in sf and sf not in rf:
            return False
    return True


def list_public(universite: str | None = None) -> list[dict]:
    if universite:
        rows = get_db().execute(
            """SELECT * FROM platform_courses
               WHERE published = 1 AND universite = ?
               ORDER BY created_at DESC""",
            (clean_text(universite, 80),),
        ).fetchall()
    else:
        rows = get_db().execute(
            """SELECT * FROM platform_courses
               WHERE published = 1
               ORDER BY created_at DESC"""
        ).fetchall()
    return [_row_to_item(r) for r in rows]


def list_for_student(actor: dict) -> list[dict]:
    campus = _student_campus(actor)
    email = (actor.get("email") or actor.get("identifiant") or "").lower()
    rows = get_db().execute(
        """SELECT c.* FROM platform_courses c
           WHERE c.published = 1 AND c.universite = ?
           ORDER BY c.created_at DESC""",
        (campus,),
    ).fetchall()
    enrolled_ids = {
        r["course_id"]
        for r in get_db().execute(
            "SELECT course_id FROM course_enrollments WHERE LOWER(student_email) = ?",
            (email,),
        ).fetchall()
    }
    out = []
    for row in rows:
        if not _match_student(row, actor):
            continue
        out.append(_row_to_item(row, enrolled=row["id"] in enrolled_ids))
    return out


def list_manage(actor: dict) -> list[dict]:
    campus = _assert_manage(actor)
    rows = get_db().execute(
        """SELECT * FROM platform_courses
           WHERE universite = ?
           ORDER BY created_at DESC""",
        (campus,),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def create_course(actor: dict, data: dict) -> dict:
    campus = _assert_manage(actor)
    title = clean_text(data.get("title"), 200)
    code = clean_text(data.get("code"), 40).upper()
    if not title or len(title) < 3:
        raise ValueError("INVALID_INPUT")
    if not code or len(code) < 2:
        code = uid("CRS").split("-")[-1][:8].upper()

    category = clean_text(data.get("category"), 40) or "mooc"
    if category not in CATEGORIES:
        category = "autre"
    niveau = clean_text(data.get("niveau"), 40) or "tous"
    if niveau not in NIVEAUX:
        niveau = "tous"

    now = _now()
    item_id = uid("crs")
    prof_email = clean_text(data.get("professorEmail"), 255)
    prof_name = clean_text(data.get("professorName"), 200)
    if actor.get("role") == "professeur" and not prof_email:
        prof_email = actor.get("email") or actor.get("identifiant") or ""
        prof_name = " ".join(
            p for p in [actor.get("prenom"), actor.get("nom")] if p
        ).strip()

    get_db().execute(
        """INSERT INTO platform_courses (
            id, code, title, description, category, universite, university_name,
            filiere, niveau, classe, professor_email, professor_name,
            cover_url, resource_url, duration_hours, credits, published,
            author_id, author_role, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item_id,
            code,
            title,
            clean_text(data.get("description"), 2000),
            category,
            campus,
            clean_text(data.get("universityName"), 200),
            clean_text(data.get("filiere"), 120),
            niveau,
            clean_text(data.get("classe"), 120),
            prof_email,
            prof_name,
            clean_text(data.get("coverUrl"), 500),
            clean_text(data.get("resourceUrl"), 500),
            int(data.get("durationHours") or 0),
            int(data.get("credits") or 0),
            1 if data.get("published", True) else 0,
            actor.get("email") or actor.get("identifiant") or "",
            actor.get("role") or "",
            now,
            now,
        ),
    )
    get_db().commit()
    row = get_db().execute("SELECT * FROM platform_courses WHERE id = ?", (item_id,)).fetchone()
    return _row_to_item(row)


def update_course(actor: dict, course_id: str, data: dict) -> dict:
    campus = _assert_manage(actor)
    row = get_db().execute(
        "SELECT * FROM platform_courses WHERE id = ?", (clean_text(course_id, 80),)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    if row["universite"] != campus:
        raise ValueError("FORBIDDEN")

    fields = []
    values = []
    mapping = {
        "title": ("title", 200),
        "description": ("description", 2000),
        "category": ("category", 40),
        "filiere": ("filiere", 120),
        "niveau": ("niveau", 40),
        "classe": ("classe", 120),
        "professorEmail": ("professor_email", 255),
        "professorName": ("professor_name", 200),
        "coverUrl": ("cover_url", 500),
        "resourceUrl": ("resource_url", 500),
        "universityName": ("university_name", 200),
    }
    for key, (col, max_len) in mapping.items():
        if key in data:
            val = clean_text(data.get(key), max_len)
            if key == "category" and val not in CATEGORIES:
                val = "autre"
            if key == "niveau" and val not in NIVEAUX:
                val = "tous"
            fields.append(f"{col} = ?")
            values.append(val)
    if "code" in data:
        fields.append("code = ?")
        values.append(clean_text(data.get("code"), 40).upper())
    if "durationHours" in data:
        fields.append("duration_hours = ?")
        values.append(int(data.get("durationHours") or 0))
    if "credits" in data:
        fields.append("credits = ?")
        values.append(int(data.get("credits") or 0))
    if "published" in data:
        fields.append("published = ?")
        values.append(1 if data.get("published") else 0)

    if not fields:
        return _row_to_item(row)

    fields.append("updated_at = ?")
    values.append(_now())
    values.append(row["id"])
    get_db().execute(
        f"UPDATE platform_courses SET {', '.join(fields)} WHERE id = ?", tuple(values)
    )
    get_db().commit()
    updated = get_db().execute("SELECT * FROM platform_courses WHERE id = ?", (row["id"],)).fetchone()
    return _row_to_item(updated)


def delete_course(actor: dict, course_id: str) -> dict:
    campus = _assert_manage(actor)
    row = get_db().execute(
        "SELECT * FROM platform_courses WHERE id = ?", (clean_text(course_id, 80),)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    if row["universite"] != campus:
        raise ValueError("FORBIDDEN")
    get_db().execute("DELETE FROM course_enrollments WHERE course_id = ?", (row["id"],))
    get_db().execute("DELETE FROM platform_courses WHERE id = ?", (row["id"],))
    get_db().commit()
    return {"ok": True, "id": row["id"]}


def enroll(actor: dict, course_id: str) -> dict:
    campus = _student_campus(actor)
    email = (actor.get("email") or actor.get("identifiant") or "").lower()
    row = get_db().execute(
        "SELECT * FROM platform_courses WHERE id = ? AND published = 1",
        (clean_text(course_id, 80),),
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    if row["universite"] != campus:
        raise ValueError("FORBIDDEN_CAMPUS")
    if not _match_student(row, actor):
        raise ValueError("FORBIDDEN")

    existing = get_db().execute(
        """SELECT id FROM course_enrollments
           WHERE course_id = ? AND LOWER(student_email) = ?""",
        (row["id"], email),
    ).fetchone()
    if existing:
        enr = get_db().execute(
            "SELECT * FROM course_enrollments WHERE id = ?", (existing["id"],)
        ).fetchone()
        return _enrollment_to_dict(enr, row)

    now = _now()
    enr_id = uid("enr")
    get_db().execute(
        """INSERT INTO course_enrollments (
            id, course_id, student_email, student_name, matricule, universite,
            progress, status, enrolled_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 'active', ?)""",
        (
            enr_id,
            row["id"],
            email,
            " ".join(p for p in [actor.get("prenom"), actor.get("nom")] if p).strip(),
            clean_text(actor.get("matricule"), 50),
            campus,
            now,
        ),
    )
    get_db().commit()
    enr = get_db().execute(
        "SELECT * FROM course_enrollments WHERE id = ?", (enr_id,)
    ).fetchone()
    return _enrollment_to_dict(enr, row)


def _enrollment_to_dict(enr, course_row) -> dict:
    course = _row_to_item(course_row, enrolled=True)
    return {
        "id": enr["id"],
        "courseId": enr["course_id"],
        "studentEmail": enr["student_email"],
        "progress": int(enr["progress"] or 0),
        "status": enr["status"],
        "enrolledAt": enr["enrolled_at"],
        "course": course,
    }


def list_my_enrollments(actor: dict) -> list[dict]:
    campus = _student_campus(actor)
    email = (actor.get("email") or actor.get("identifiant") or "").lower()
    rows = get_db().execute(
        """SELECT e.*, c.title, c.code, c.description, c.category, c.universite,
                  c.filiere, c.niveau, c.classe, c.professor_email, c.professor_name,
                  c.cover_url, c.resource_url, c.duration_hours, c.credits,
                  c.published, c.university_name, c.created_at, c.updated_at
           FROM course_enrollments e
           JOIN platform_courses c ON c.id = e.course_id
           WHERE LOWER(e.student_email) = ? AND e.universite = ?
           ORDER BY e.enrolled_at DESC""",
        (email, campus),
    ).fetchall()
    out = []
    for r in rows:
        course_row = {
            "id": r["course_id"],
            "code": r["code"],
            "title": r["title"],
            "description": r["description"],
            "category": r["category"],
            "universite": r["universite"],
            "university_name": r["university_name"],
            "filiere": r["filiere"],
            "niveau": r["niveau"],
            "classe": r["classe"],
            "professor_email": r["professor_email"],
            "professor_name": r["professor_name"],
            "cover_url": r["cover_url"],
            "resource_url": r["resource_url"],
            "duration_hours": r["duration_hours"],
            "credits": r["credits"],
            "published": r["published"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        out.append(
            {
                "id": r["id"],
                "courseId": r["course_id"],
                "progress": int(r["progress"] or 0),
                "enrolledAt": r["enrolled_at"],
                "course": _row_to_item(course_row, enrolled=True),
            }
        )
    return out
