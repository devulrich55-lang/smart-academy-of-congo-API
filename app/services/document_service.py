import json
import uuid
from datetime import datetime, timezone

from app.database import get_db, is_duplicate_key_error, row_to_document
from app.utils.sanitize import (
    clean_media_category,
    clean_niveau,
    clean_reaction_type,
    clean_text,
)
from app.utils.visibility import SOURCE_BY_ROLE, student_sees_document


def _attach_view_counts(docs: list[dict]) -> list[dict]:
    if not docs:
        return docs
    ids = [d["id"] for d in docs if d.get("id")]
    if not ids:
        return docs
    placeholders = ",".join("?" for _ in ids)
    rows = get_db().execute(
        f"""SELECT document_id, COUNT(*) AS c FROM document_views
            WHERE document_id IN ({placeholders}) GROUP BY document_id""",
        tuple(ids),
    ).fetchall()
    unique_map = {row["document_id"]: int(row["c"] or 0) for row in rows}
    for doc in docs:
        doc["uniqueViewCount"] = unique_map.get(doc["id"], 0)
    return docs


def _assert_can_view_document(user: dict, doc: dict) -> None:
    role = user.get("role")
    if role == "etudiant":
        student = {
            "universite": user.get("universite"),
            "filiere": user.get("filiere"),
            "niveau": user.get("niveau"),
            "classe": user.get("classe"),
            "sectionId": user.get("sectionId"),
            "email": user.get("email"),
        }
        if not student_sees_document(student, doc):
            raise ValueError("FORBIDDEN")
        return
    if role == "universite":
        if doc.get("source") == "administration":
            campus = user.get("universite")
            if doc.get("universite") and campus and doc.get("universite") != campus:
                raise ValueError("FORBIDDEN")
        return
    if role == "section":
        if doc.get("audienceType") == "section" and doc.get("sectionId"):
            if user.get("sectionId") and doc.get("sectionId") != user.get("sectionId"):
                raise ValueError("FORBIDDEN")
        return
    if role in ("professeur", "assistant"):
        src = SOURCE_BY_ROLE.get(role)
        if doc.get("source") == src and doc.get("authorId") == user.get("id"):
            return
        if doc.get("source") == "administration":
            return
        if role == "professeur" and doc.get("source") in ("professeur", "assistant"):
            if doc.get("universite") == user.get("universite"):
                return
    if role in ("ministere", "superadmin"):
        return
    raise ValueError("FORBIDDEN")


def record_document_view(user: dict, doc_id: str, viewer_key: str) -> dict:
    clean_id = clean_text(doc_id, 80)
    clean_key = clean_text(viewer_key, 120)
    if not clean_id or not clean_key:
        raise ValueError("INVALID_INPUT")
    doc = get_document_by_id(clean_id)
    if not doc:
        raise ValueError("NOT_FOUND")
    _assert_can_view_document(user, doc)

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    is_new_viewer = False
    try:
        db.execute(
            "INSERT INTO document_views (document_id, viewer_key, viewed_at) VALUES (?,?,?)",
            (clean_id, clean_key, now),
        )
        is_new_viewer = True
    except Exception as exc:
        if not is_duplicate_key_error(exc):
            raise

    db.execute(
        "UPDATE documents SET view_count = COALESCE(view_count, 0) + 1, updated_at = ? WHERE id = ?",
        (now, clean_id),
    )
    db.commit()

    stats = db.execute(
        "SELECT view_count FROM documents WHERE id = ?", (clean_id,)
    ).fetchone()
    unique_row = db.execute(
        "SELECT COUNT(*) AS c FROM document_views WHERE document_id = ?", (clean_id,)
    ).fetchone()
    return {
        "ok": True,
        "viewCount": int(stats["view_count"] or 0) if stats else 0,
        "uniqueViewCount": int(unique_row["c"] or 0) if unique_row else 0,
        "isNewViewer": is_new_viewer,
    }


def get_all_documents(limit: int = 200, offset: int = 0) -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM documents ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [row_to_document(r) for r in rows]


def get_documents_for_student(
    student: dict,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    uni = student.get("universite")
    # Requête ciblée (évite de charger toute la table documents)
    rows = get_db().execute(
        """SELECT * FROM documents
           WHERE (
             source = 'administration'
             AND (universite IS NULL OR universite = '' OR LOWER(universite) = LOWER(?))
           ) OR (
             source IN ('professeur', 'assistant')
             AND (universite IS NULL OR universite = '' OR LOWER(universite) = LOWER(?))
           )
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (uni, uni, limit * 5, offset),
    ).fetchall()
    docs = []
    for r in rows:
        d = row_to_document(r)
        if d:
            docs.append(d)
    filtered = [d for d in docs if student_sees_document(student, d)]
    return _attach_view_counts(filtered[:limit])


def get_my_documents(
    user: dict,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    source = SOURCE_BY_ROLE.get(user.get("role"))
    if not source:
        return []
    db = get_db()
    if user.get("role") == "universite":
        rows = db.execute(
            """SELECT * FROM documents
               WHERE source = 'administration'
               AND (universite IS NULL OR universite = ?)
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (user.get("universite"), limit, offset),
        ).fetchall()
    elif user.get("role") == "section" and user.get("sectionId"):
        rows = db.execute(
            """SELECT * FROM documents
               WHERE source = 'administration'
               AND audience_type = 'section' AND section_id = ?
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (user.get("sectionId"), limit, offset),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT * FROM documents
               WHERE source = ? AND author_id = ?
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (source, user["id"], limit, offset),
        ).fetchall()
    return _attach_view_counts([row_to_document(r) for r in rows if row_to_document(r)])


def get_document_by_id(doc_id: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    doc = row_to_document(row)
    if not doc:
        return None
    return _attach_view_counts([doc])[0]


def can_edit(user: dict | None, doc: dict | None) -> bool:
    if not user or not doc:
        return False
    if user.get("role") == "universite":
        return doc.get("source") == "administration"
    if user.get("role") == "section" and doc.get("audienceType") == "section":
        if doc.get("sectionId") and user.get("sectionId") and doc.get("sectionId") != user.get("sectionId"):
            return False
        return doc.get("source") == "administration" and doc.get("authorId") == user.get("id")
    src = SOURCE_BY_ROLE.get(user.get("role"))
    return src == doc.get("source") and doc.get("authorId") == user.get("id")


def create_document(user: dict, data: dict) -> dict:
    source = SOURCE_BY_ROLE.get(user.get("role"))
    if not source:
        raise ValueError("FORBIDDEN")

    is_campus = user.get("role") == "universite"
    is_section = user.get("role") == "section" or (
        data.get("audienceType") == "section" and data.get("sectionId")
    )
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    audience_type = (
        "section" if is_section else "campus" if is_campus else "ma_classe"
    )
    section_id = clean_text(data.get("sectionId"), 80) if is_section else None
    if is_section and not section_id:
        section_id = clean_text(user.get("sectionId"), 80)
    section_name = clean_text(data.get("sectionName"), 200) if is_section else None

    get_db().execute(
        """INSERT INTO documents (
          id, title, description, source, author, author_id, date,
          media_category, type, size, media_url, media_path, attachments, audience_type,
          section_id, section_name, universite, filiere, niveau, course_code, course_name, classe,
          allow_reactions, reactions, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            doc_id,
            clean_text(data.get("title"), 300),
            clean_text(data.get("description"), 5000),
            source,
            clean_text(data.get("author"), 150) or user["email"],
            user["id"],
            now[:10],
            clean_media_category(data.get("mediaCategory")),
            clean_text(data.get("type"), 20) or "PDF",
            clean_text(data.get("size"), 30) or "—",
            clean_text(data.get("mediaUrl"), 2000)
            if data.get("mediaUrl") and not str(data["mediaUrl"]).startswith("data:")
            else "",
            data.get("mediaPath"),
            json.dumps(data.get("attachments") or []),
            audience_type,
            section_id,
            section_name,
            clean_text(data.get("universite") or user.get("universite"), 50),
            clean_text(data.get("filiere"), 200),
            clean_niveau(data.get("niveau")) or data.get("niveau"),
            clean_text(data.get("courseCode"), 30),
            clean_text(data.get("courseName"), 200),
            clean_text(data.get("classe"), 150),
            0 if is_campus else (1 if data.get("allowReactions") else 0),
            json.dumps({"useful": [], "question": [], "thanks": []}),
            now,
            now,
        ),
    )
    get_db().commit()
    return get_document_by_id(doc_id)


def update_document(user: dict, doc_id: str, data: dict) -> dict:
    doc = get_document_by_id(doc_id)
    if not doc or not can_edit(user, doc):
        raise ValueError("FORBIDDEN")
    now = datetime.now(timezone.utc).isoformat()
    fields = {
        "title": clean_text(data["title"], 300) if data.get("title") is not None else doc["title"],
        "description": clean_text(data["description"], 5000)
        if data.get("description") is not None
        else doc["description"],
        "media_category": clean_media_category(data["mediaCategory"])
        if data.get("mediaCategory") is not None
        else doc["mediaCategory"],
        "type": clean_text(data["type"], 20) if data.get("type") is not None else doc["type"],
        "size": clean_text(data["size"], 30) if data.get("size") is not None else doc["size"],
        "media_url": (
            clean_text(data["mediaUrl"], 2000)
            if data.get("mediaUrl") and not str(data["mediaUrl"]).startswith("data:")
            else "" if data.get("mediaUrl") == "" else doc["mediaUrl"]
        ),
        "filiere": clean_text(data["filiere"], 200)
        if data.get("filiere") is not None
        else doc["filiere"],
        "niveau": (clean_niveau(data["niveau"]) or data["niveau"])
        if data.get("niveau") is not None
        else doc["niveau"],
        "course_code": clean_text(data["courseCode"], 30)
        if data.get("courseCode") is not None
        else doc["courseCode"],
        "course_name": clean_text(data["courseName"], 200)
        if data.get("courseName") is not None
        else doc["courseName"],
        "classe": clean_text(data["classe"], 150)
        if data.get("classe") is not None
        else doc["classe"],
        "allow_reactions": (1 if data["allowReactions"] else 0)
        if data.get("allowReactions") is not None
        else (1 if doc["allowReactions"] else 0),
    }
    get_db().execute(
        """UPDATE documents SET title=?, description=?, media_category=?, type=?, size=?,
           media_url=?, filiere=?, niveau=?, course_code=?, course_name=?, classe=?,
           allow_reactions=?, updated_at=? WHERE id=?""",
        (
            fields["title"],
            fields["description"],
            fields["media_category"],
            fields["type"],
            fields["size"],
            fields["media_url"],
            fields["filiere"],
            fields["niveau"],
            fields["course_code"],
            fields["course_name"],
            fields["classe"],
            fields["allow_reactions"],
            now,
            doc_id,
        ),
    )
    get_db().commit()
    return get_document_by_id(doc_id)


def delete_document(user: dict, doc_id: str) -> bool:
    doc = get_document_by_id(doc_id)
    if not doc or not can_edit(user, doc):
        raise ValueError("FORBIDDEN")
    get_db().execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    get_db().commit()
    return True


def add_reaction(doc_id: str, reaction_type: str, student_id: str) -> dict:
    rt = clean_reaction_type(reaction_type)
    if not rt:
        raise ValueError("INVALID_REACTION")
    doc = get_document_by_id(doc_id)
    if not doc:
        raise ValueError("NOT_FOUND")
    if not doc.get("allowReactions") or doc.get("source") not in (
        "professeur",
        "assistant",
    ):
        raise ValueError("FORBIDDEN")

    reactions = doc.get("reactions") or {"useful": [], "question": [], "thanks": []}
    for t in ("useful", "question", "thanks"):
        reactions[t] = [i for i in reactions.get(t, []) if i != student_id]
    if student_id not in reactions.get(rt, []):
        reactions.setdefault(rt, []).append(student_id)

    now = datetime.now(timezone.utc).isoformat()
    get_db().execute(
        "UPDATE documents SET reactions = ?, updated_at = ? WHERE id = ?",
        (json.dumps(reactions), now, doc_id),
    )
    get_db().commit()
    return get_document_by_id(doc_id)
