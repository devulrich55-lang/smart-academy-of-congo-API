from datetime import datetime, timezone

from app.database import get_db
from app.services import edb_service
from app.utils.platform_security import uid
from app.utils.sanitize import clean_text

MINISTRY_ROLE = "ministere"
AUTHOR_ROLE = "auteur"
EDB_SOURCE = "evodigitalbooks"
CATEGORIES = frozenset(
    {
        "education",
        "pedagogie",
        "manuel",
        "programme",
        "examen",
        "methodes",
        "memoire",
        "mathematiques",
        "physique",
        "chimie",
        "biologie",
        "sciences",
        "informatique",
        "ingenierie",
        "medecine",
        "sante",
        "agriculture",
        "environnement",
        "histoire",
        "geographie",
        "philosophie",
        "droit",
        "economie",
        "gestion",
        "politique",
        "sociologie",
        "psychologie",
        "roman",
        "litterature",
        "poesie",
        "theatre",
        "langues",
        "arts",
        "musique",
        "religion",
        "enfants",
        "dictionnaire",
        "culture",
        "developpement",
        "autre",
    }
)
LANGUAGES = frozenset({"fr", "en", "bilingue"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_item(row, viewer_email: str | None = None) -> dict:
    source = row["source"] if "source" in row.keys() else ""
    is_free = bool(row["is_free"]) if "is_free" in row.keys() else True
    price = float(row["price"] or 0) if "price" in row.keys() else 0.0
    currency = row["currency"] if "currency" in row.keys() else "USD"
    author_email = row["author_email"] if "author_email" in row.keys() else ""
    author_mobile = (
        row["author_mobile_money"] if "author_mobile_money" in row.keys() else ""
    )
    file_url = row["file_url"] or ""
    if source == EDB_SOURCE and not is_free:
        if not viewer_email or not edb_service.buyer_owns_book(viewer_email, row["id"]):
            file_url = ""

    item = {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"] or "",
        "category": row["category"],
        "description": row["description"] or "",
        "language": row["language"] or "fr",
        "fileUrl": file_url,
        "coverUrl": row["cover_url"] or "",
        "published": bool(row["published"]),
        "authorId": row["author_id"],
        "authorRole": row["author_role"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "source": source or "",
        "isFree": is_free,
        "is_free": is_free,
        "free": is_free,
        "price": price,
        "currency": currency or "USD",
        "authorEmail": author_email or "",
        "authorMobileMoney": author_mobile or "",
        "accessType": "free" if is_free else "paid",
    }
    return item


def _assert_ministry(actor: dict) -> None:
    if actor.get("role") not in (MINISTRY_ROLE, "superadmin"):
        raise ValueError("FORBIDDEN")


def _assert_author(actor: dict) -> None:
    if actor.get("role") != AUTHOR_ROLE:
        raise ValueError("FORBIDDEN")
    status = edb_service.get_author_status(actor.get("email") or "")
    if status != "approved":
        raise ValueError("FORBIDDEN")


def _can_manage_row(actor: dict, row) -> None:
    role = actor.get("role")
    if role in (MINISTRY_ROLE, "superadmin"):
        return
    if role == AUTHOR_ROLE:
        _assert_author(actor)
        author_id = (actor.get("email") or "").lower()
        if row["author_role"] == AUTHOR_ROLE and (row["author_id"] or "").lower() == author_id:
            return
    raise ValueError("NOT_FOUND")


def list_public_books(viewer_email: str | None = None) -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM digital_library
           WHERE published = 1
           ORDER BY created_at DESC"""
    ).fetchall()
    email = (viewer_email or "").strip().lower() or None
    return [_row_to_item(r, email) for r in rows]


def list_manage_books(actor: dict) -> list[dict]:
    role = actor.get("role")
    if role in (MINISTRY_ROLE, "superadmin"):
        rows = get_db().execute(
            """SELECT * FROM digital_library
               WHERE author_role = ?
               ORDER BY created_at DESC""",
            (MINISTRY_ROLE,),
        ).fetchall()
        return [_row_to_item(r, actor.get("email")) for r in rows]
    if role == AUTHOR_ROLE:
        _assert_author(actor)
        email = (actor.get("email") or "").lower()
        rows = get_db().execute(
            """SELECT * FROM digital_library
               WHERE author_role = ? AND author_id = ? COLLATE NOCASE
               ORDER BY created_at DESC""",
            (AUTHOR_ROLE, email),
        ).fetchall()
        return [_row_to_item(r, email) for r in rows]
    raise ValueError("FORBIDDEN")


def create_book(actor: dict, data: dict) -> dict:
    role = actor.get("role")
    if role == AUTHOR_ROLE:
        _assert_author(actor)
        author_role = AUTHOR_ROLE
        author_id = (actor.get("email") or "").lower()
        source = clean_text(data.get("source"), 40) or EDB_SOURCE
    elif role in (MINISTRY_ROLE, "superadmin"):
        author_role = MINISTRY_ROLE
        author_id = (actor.get("email") or "").lower()
        source = clean_text(data.get("source"), 40) or ""
    else:
        raise ValueError("FORBIDDEN")

    title = clean_text(data.get("title"), 200)
    category = clean_text(data.get("category"), 40)
    if not title or len(title) < 3:
        raise ValueError("INVALID_INPUT")
    if category not in CATEGORIES:
        category = "autre"

    language = clean_text(data.get("language"), 20) or "fr"
    if language not in LANGUAGES:
        language = "fr"

    is_free = bool(
        data.get("isFree")
        if data.get("isFree") is not None
        else data.get("is_free", data.get("free", True))
    )
    price = float(data.get("price") or 0)
    if is_free:
        price = 0.0
    elif role == AUTHOR_ROLE and not is_free and price <= 0:
        raise ValueError("INVALID_INPUT")

    currency = clean_text(data.get("currency"), 10).upper() or "USD"
    author_email = clean_text(data.get("authorEmail") or data.get("author_email"), 255)
    if role == AUTHOR_ROLE and not author_email:
        author_email = author_id
    author_mobile = clean_text(
        data.get("authorMobileMoney") or data.get("author_mobile_money"), 30
    )

    item_id = clean_text(data.get("id"), 80) or uid("lib")
    now = _now()
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
        "author_id": author_id,
        "author_role": author_role,
        "source": source,
        "is_free": 1 if is_free else 0,
        "price": price,
        "currency": currency,
        "author_email": author_email or "",
        "author_mobile_money": author_mobile or "",
        "created_at": now,
        "updated_at": now,
    }
    db = get_db()
    db.execute(
        """INSERT INTO digital_library
           (id, title, author, category, description, language, file_url, cover_url,
            published, author_id, author_role, source, is_free, price, currency,
            author_email, author_mobile_money, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            row["source"],
            row["is_free"],
            row["price"],
            row["currency"],
            row["author_email"],
            row["author_mobile_money"],
            row["created_at"],
            row["updated_at"],
        ),
    )
    db.commit()
    return _row_to_item(row, actor.get("email"))


def update_book(actor: dict, item_id: str, data: dict) -> dict:
    clean_id = clean_text(item_id, 80)
    if not clean_id:
        raise ValueError("INVALID_INPUT")

    row = get_db().execute(
        "SELECT * FROM digital_library WHERE id = ?", (clean_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    _can_manage_row(actor, row)

    title = clean_text(data.get("title", row["title"]), 200)
    category = clean_text(data.get("category", row["category"]), 40)
    if category not in CATEGORIES:
        category = row["category"]
    language = clean_text(data.get("language", row["language"]), 20)
    if language not in LANGUAGES:
        language = row["language"]

    is_free = row["is_free"] if "is_free" in row.keys() else 1
    if "isFree" in data or "is_free" in data or "free" in data:
        is_free = 1 if bool(
            data.get("isFree")
            if data.get("isFree") is not None
            else data.get("is_free", data.get("free"))
        ) else 0
    price = float(data.get("price", row["price"] if "price" in row.keys() else 0) or 0)
    if is_free:
        price = 0.0
    currency = clean_text(data.get("currency", row["currency"] if "currency" in row.keys() else "USD"), 10) or "USD"

    now = _now()
    db = get_db()
    db.execute(
        """UPDATE digital_library SET
           title = ?, author = ?, category = ?, description = ?, language = ?,
           file_url = ?, cover_url = ?, published = ?, is_free = ?, price = ?,
           currency = ?, author_email = ?, author_mobile_money = ?, updated_at = ?
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
            is_free,
            price,
            currency,
            clean_text(
                data.get("authorEmail", row["author_email"] if "author_email" in row.keys() else ""),
                255,
            ),
            clean_text(
                data.get(
                    "authorMobileMoney",
                    row["author_mobile_money"] if "author_mobile_money" in row.keys() else "",
                ),
                30,
            ),
            now,
            clean_id,
        ),
    )
    db.commit()
    updated = get_db().execute(
        "SELECT * FROM digital_library WHERE id = ?", (clean_id,)
    ).fetchone()
    return _row_to_item(updated, actor.get("email"))


def delete_book(actor: dict, item_id: str) -> dict:
    clean_id = clean_text(item_id, 80)
    row = get_db().execute(
        "SELECT * FROM digital_library WHERE id = ?", (clean_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    _can_manage_row(actor, row)
    get_db().execute("DELETE FROM digital_library WHERE id = ?", (clean_id,))
    get_db().commit()
    return {"ok": True, "id": clean_id}
