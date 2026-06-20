from datetime import datetime, timezone

from app.database import get_db
from app.utils.platform_security import uid
from app.utils.sanitize import clean_text

SCOPES = frozenset({"national", "university"})
AUTHOR_ROLES = frozenset({"ministere", "universite"})
CATEGORIES = frozenset(
    {"officiel", "gouvernemental", "concours", "opportunite", "bourse", "education"}
)
MINISTRY_CODE = "ministere"
NATIONAL_CODE = "national"
MINISTRY_NAME = "Ministère de l'Enseignement Supérieur et Universitaire (MESU)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_expired(valid_until: str | None) -> bool:
    if not valid_until:
        return False
    try:
        end = datetime.fromisoformat(str(valid_until).strip() + "T23:59:59")
        return end.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)
    except ValueError:
        return False


def _row_to_item(row) -> dict:
    return {
        "id": row["id"],
        "scope": row["scope"],
        "authorRole": row["author_role"],
        "universite": row["universite"],
        "universityName": row["university_name"],
        "authorId": row["author_id"],
        "authorName": row["author_name"],
        "category": row["category"],
        "title": row["title"],
        "excerpt": row["excerpt"],
        "body": row["body"] or "",
        "linkUrl": row["link_url"] or "",
        "linkLabel": row["link_label"] or "",
        "published": bool(row["published"]),
        "pinned": bool(row["pinned"]),
        "validUntil": row["valid_until"] or "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _can_manage(actor: dict, row) -> bool:
    role = actor.get("role")
    if role == "ministere":
        return row["author_role"] == "ministere" or row["universite"] == MINISTRY_CODE
    if role == "universite":
        campus = actor.get("universite") or actor.get("codeUni") or actor.get("sigle")
        author_id = (actor.get("email") or "").lower()
        if row["author_role"] == "ministere":
            return False
        if row["scope"] == "national":
            return (row["author_id"] or "").lower() == author_id
        return bool(campus and row["universite"] == campus)
    return False


def list_public_home_news() -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM home_news WHERE published = 1 ORDER BY pinned DESC, created_at DESC"
    ).fetchall()
    return [_row_to_item(r) for r in rows if not _is_expired(r["valid_until"])]


def list_manage_home_news(actor: dict) -> list[dict]:
    role = actor.get("role")
    if role not in ("ministere", "universite"):
        raise ValueError("FORBIDDEN")
    db = get_db()
    if role == "ministere":
        rows = db.execute(
            """SELECT * FROM home_news
               WHERE author_role = 'ministere' OR universite = ?
               ORDER BY created_at DESC""",
            (MINISTRY_CODE,),
        ).fetchall()
    else:
        campus = actor.get("universite") or actor.get("codeUni") or actor.get("sigle")
        email = actor.get("email")
        rows = db.execute(
            """SELECT * FROM home_news
               WHERE (scope = 'university' AND universite = ?)
                  OR (scope = 'national' AND author_id = ? COLLATE NOCASE)
               ORDER BY created_at DESC""",
            (campus, email),
        ).fetchall()
    return [_row_to_item(r) for r in rows]


def create_home_news(actor: dict, data: dict) -> dict:
    role = actor.get("role")
    if role not in ("ministere", "universite"):
        raise ValueError("FORBIDDEN")

    category = clean_text(data.get("category"), 40)
    title = clean_text(data.get("title"), 200)
    excerpt = clean_text(data.get("excerpt"), 400)
    if category not in CATEGORIES or not title or len(title) < 5:
        raise ValueError("INVALID_INPUT")
    if not excerpt or len(excerpt) < 10:
        raise ValueError("INVALID_INPUT")

    now = _now()
    item_id = uid("hn")

    if role == "ministere":
        scope = "national"
        author_role = "ministere"
        universite = MINISTRY_CODE
        university_name = MINISTRY_NAME
    else:
        scope = "national" if data.get("scope") == "national" else "university"
        author_role = "universite"
        campus = actor.get("universite") or actor.get("codeUni") or actor.get("sigle")
        if not campus:
            raise ValueError("INVALID_INPUT")
        universite = NATIONAL_CODE if scope == "national" else campus
        display = actor.get("nomUniversite") or actor.get("nom") or campus
        university_name = (
            f"{display} — Espace national"
            if scope == "national"
            else str(display)
        )

    get_db().execute(
        """INSERT INTO home_news (
          id, scope, author_role, universite, university_name, author_id, author_name,
          category, title, excerpt, body, link_url, link_label, published, pinned,
          valid_until, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            item_id,
            scope,
            author_role,
            universite,
            university_name,
            actor.get("email"),
            clean_text(
                actor.get("nom") or actor.get("displayName") or actor.get("nomUniversite"),
                150,
            )
            or "Administration",
            category,
            title,
            excerpt,
            clean_text(data.get("body"), 5000) or "",
            clean_text(data.get("linkUrl"), 500) or "",
            clean_text(data.get("linkLabel"), 120) or "En savoir plus",
            1 if data.get("published", True) else 0,
            1 if data.get("pinned") else 0,
            clean_text(data.get("validUntil"), 20) or None,
            now,
            now,
        ),
    )
    get_db().commit()
    row = get_db().execute("SELECT * FROM home_news WHERE id = ?", (item_id,)).fetchone()
    return _row_to_item(row)


def update_home_news(actor: dict, item_id: str, data: dict) -> dict:
    row = get_db().execute("SELECT * FROM home_news WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    if not _can_manage(actor, row):
        raise ValueError("FORBIDDEN")

    category = clean_text(data.get("category") or row["category"], 40)
    title = clean_text(data.get("title") or row["title"], 200)
    excerpt = clean_text(data.get("excerpt") or row["excerpt"], 400)
    if category not in CATEGORIES or not title or len(title) < 5:
        raise ValueError("INVALID_INPUT")
    if not excerpt or len(excerpt) < 10:
        raise ValueError("INVALID_INPUT")

    now = _now()
    get_db().execute(
        """UPDATE home_news SET
          category=?, title=?, excerpt=?, body=?, link_url=?, link_label=?,
          published=?, pinned=?, valid_until=?, updated_at=?
          WHERE id=?""",
        (
            category,
            title,
            excerpt,
            clean_text(data.get("body") if "body" in data else row["body"], 5000) or "",
            clean_text(
                data.get("linkUrl") if "linkUrl" in data else row["link_url"], 500
            )
            or "",
            clean_text(
                data.get("linkLabel") if "linkLabel" in data else row["link_label"], 120
            )
            or "En savoir plus",
            1
            if (data.get("published") if "published" in data else row["published"])
            else 0,
            1 if (data.get("pinned") if "pinned" in data else row["pinned"]) else 0,
            clean_text(
                data.get("validUntil") if "validUntil" in data else row["valid_until"], 20
            )
            or None,
            now,
            item_id,
        ),
    )
    get_db().commit()
    row = get_db().execute("SELECT * FROM home_news WHERE id = ?", (item_id,)).fetchone()
    return _row_to_item(row)


def delete_home_news(actor: dict, item_id: str) -> dict:
    row = get_db().execute("SELECT * FROM home_news WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    if not _can_manage(actor, row):
        raise ValueError("FORBIDDEN")
    get_db().execute("DELETE FROM home_news WHERE id = ?", (item_id,))
    get_db().commit()
    return {"ok": True, "id": item_id}
