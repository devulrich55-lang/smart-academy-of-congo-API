from datetime import datetime, timezone

from app.database import get_db
from app.utils.platform_security import uid
from app.utils.sanitize import clean_text

AUTHOR_ROLE = "ministere"
CATEGORIES = frozenset(
    {"roman", "sciences", "langues", "methodes", "informatique", "histoire", "education", "autre"}
)
LANGUAGES = frozenset({"fr", "en", "bilingue"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_item(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"] or "",
        "category": row["category"],
        "description": row["description"] or "",
        "language": row["language"] or "fr",
        "fileUrl": row["file_url"] or "",
        "coverUrl": row["cover_url"] or "",
        "published": bool(row["published"]),
        "authorId": row["author_id"],
        "authorRole": row["author_role"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _assert_ministry(actor: dict) -> None:
    if actor.get("role") != AUTHOR_ROLE:
        raise ValueError("FORBIDDEN")


def list_public_books() -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM digital_library
           WHERE published = 1
           ORDER BY created_at DESC"""
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def list_manage_books(actor: dict) -> list[dict]:
    _assert_ministry(actor)
    rows = get_db().execute(
        """SELECT * FROM digital_library
           WHERE author_role = ?
           ORDER BY created_at DESC""",
        (AUTHOR_ROLE,),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def create_book(actor: dict, data: dict) -> dict:
    _assert_ministry(actor)
    title = clean_text(data.get("title"), 200)
    category = clean_text(data.get("category"), 40)
    if not title or len(title) < 3:
        raise ValueError("INVALID_INPUT")
    if category not in CATEGORIES:
        category = "autre"

    language = clean_text(data.get("language"), 20) or "fr"
    if language not in LANGUAGES:
        language = "fr"

    now = _now()
    item_id = uid("lib")
    row = {
        "id": item_id,
        "title": title,
        "author": clean_text(data.get("author"), 120),
        "category": category,
        "description": clean_text(data.get("description"), 2000),
        "language": language,
        "file_url": clean_text(data.get("fileUrl") or data.get("file_url"), 500),
        "cover_url": clean_text(data.get("coverUrl") or data.get("cover_url"), 500),
        "published": 1 if data.get("published", True) else 0,
        "author_id": (actor.get("email") or "").lower(),
        "author_role": AUTHOR_ROLE,
        "created_at": now,
        "updated_at": now,
    }
    db = get_db()
    db.execute(
        """INSERT INTO digital_library
           (id, title, author, category, description, language, file_url, cover_url,
            published, author_id, author_role, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["id"],
            row["title"],
            row["author"],
            row["category"],
            row["description"],
            row["language"],
            row["file_url"],
            row["cover_url"],
            row["published"],
            row["author_id"],
            row["author_role"],
            row["created_at"],
            row["updated_at"],
        ),
    )
    db.commit()
    return _row_to_item(row)


def update_book(actor: dict, item_id: str, data: dict) -> dict:
    _assert_ministry(actor)
    clean_id = clean_text(item_id, 80)
    if not clean_id:
        raise ValueError("INVALID_INPUT")

    row = get_db().execute(
        "SELECT * FROM digital_library WHERE id = ?", (clean_id,)
    ).fetchone()
    if not row or row["author_role"] != AUTHOR_ROLE:
        raise ValueError("NOT_FOUND")

    title = clean_text(data.get("title", row["title"]), 200)
    category = clean_text(data.get("category", row["category"]), 40)
    if category not in CATEGORIES:
        category = row["category"]
    language = clean_text(data.get("language", row["language"]), 20)
    if language not in LANGUAGES:
        language = row["language"]

    now = _now()
    db = get_db()
    db.execute(
        """UPDATE digital_library SET
           title = ?, author = ?, category = ?, description = ?, language = ?,
           file_url = ?, cover_url = ?, published = ?, updated_at = ?
           WHERE id = ?""",
        (
            title,
            clean_text(data.get("author", row["author"]), 120),
            category,
            clean_text(data.get("description", row["description"]), 2000),
            language,
            clean_text(data.get("fileUrl", row["file_url"]), 500),
            clean_text(data.get("coverUrl", row["cover_url"]), 500),
            1 if data.get("published", row["published"]) else 0,
            now,
            clean_id,
        ),
    )
    db.commit()
    updated = get_db().execute(
        "SELECT * FROM digital_library WHERE id = ?", (clean_id,)
    ).fetchone()
    return _row_to_item(updated)


def delete_book(actor: dict, item_id: str) -> dict:
    _assert_ministry(actor)
    clean_id = clean_text(item_id, 80)
    row = get_db().execute(
        "SELECT * FROM digital_library WHERE id = ?", (clean_id,)
    ).fetchone()
    if not row or row["author_role"] != AUTHOR_ROLE:
        raise ValueError("NOT_FOUND")
    get_db().execute("DELETE FROM digital_library WHERE id = ?", (clean_id,))
    get_db().commit()
    return {"ok": True, "id": clean_id}
