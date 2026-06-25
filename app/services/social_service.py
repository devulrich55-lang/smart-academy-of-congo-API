import json
from datetime import datetime, timezone

from app.database import get_db
from app.utils.platform_security import uid
from app.utils.sanitize import clean_text

POST_ROLES = frozenset({"etudiant", "professeur", "assistant"})
MODERATE_ROLES = frozenset({"universite", "section"})
AUDIENCES = frozenset({"campus", "filiere"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _campus(actor: dict) -> str:
    return clean_text(
        actor.get("universite") or actor.get("codeUni") or actor.get("sigle"), 80
    )


def _author_name(actor: dict) -> str:
    return " ".join(
        p for p in [clean_text(actor.get("prenom"), 80), clean_text(actor.get("nom"), 80)] if p
    ).strip() or clean_text(actor.get("email"), 255)


def _parse_likes(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).lower() for x in raw]
    try:
        data = json.loads(raw)
        return [str(x).lower() for x in data] if isinstance(data, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _row_to_post(row, viewer_email: str = "") -> dict:
    likes = _parse_likes(row["likes_json"])
    email = (viewer_email or "").lower()
    return {
        "id": row["id"],
        "universite": row["universite"] or "",
        "authorEmail": row["author_email"] or "",
        "authorName": row["author_name"] or "",
        "authorRole": row["author_role"] or "",
        "content": row["content"] or "",
        "audience": row["audience"] or "campus",
        "filiere": row["filiere"] or "",
        "likes": likes,
        "likeCount": len(likes),
        "likedByMe": email in likes if email else False,
        "hidden": bool(row["hidden"]),
        "createdAt": row["created_at"],
    }


def _visible_for_actor(row, actor: dict) -> bool:
    if row["hidden"]:
        role = actor.get("role")
        if role not in MODERATE_ROLES and role != "universite":
            email = (actor.get("email") or actor.get("identifiant") or "").lower()
            if (row["author_email"] or "").lower() != email:
                return False
    campus = _campus(actor)
    if row["universite"] != campus:
        return False
    if row["audience"] == "filiere":
        post_fil = (row["filiere"] or "").lower()
        actor_fil = clean_text(actor.get("filiere"), 120).lower()
        if post_fil and actor_fil and post_fil not in actor_fil and actor_fil not in post_fil:
            return False
    return True


def list_posts(actor: dict) -> list[dict]:
    campus = _campus(actor)
    if not campus:
        raise ValueError("INVALID_INPUT")
    email = (actor.get("email") or actor.get("identifiant") or "").lower()
    rows = get_db().execute(
        """SELECT * FROM social_posts
           WHERE universite = ?
           ORDER BY created_at DESC
           LIMIT 200""",
        (campus,),
    ).fetchall()
    out = []
    for row in rows:
        if not _visible_for_actor(row, actor):
            continue
        out.append(_row_to_post(row, email))
    return out


def create_post(actor: dict, data: dict) -> dict:
    if actor.get("role") not in POST_ROLES:
        raise ValueError("FORBIDDEN")
    campus = _campus(actor)
    if not campus:
        raise ValueError("INVALID_INPUT")
    content = clean_text(data.get("content"), 2000)
    if len(content) < 2:
        raise ValueError("INVALID_INPUT")
    audience = clean_text(data.get("audience"), 20) or "campus"
    if audience not in AUDIENCES:
        audience = "campus"
    filiere = clean_text(actor.get("filiere"), 120) if audience == "filiere" else ""
    email = (actor.get("email") or actor.get("identifiant") or "").lower()
    now = _now()
    item_id = uid("soc")
    get_db().execute(
        """INSERT INTO social_posts (
            id, universite, author_email, author_name, author_role,
            content, audience, filiere, likes_json, hidden, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', 0, ?)""",
        (
            item_id,
            campus,
            email,
            _author_name(actor),
            actor.get("role") or "",
            content,
            audience,
            filiere,
            now,
        ),
    )
    get_db().commit()
    row = get_db().execute("SELECT * FROM social_posts WHERE id = ?", (item_id,)).fetchone()
    return _row_to_post(row, email)


def toggle_like(actor: dict, post_id: str) -> dict:
    campus = _campus(actor)
    email = (actor.get("email") or actor.get("identifiant") or "").lower()
    if not email:
        raise ValueError("INVALID_INPUT")
    row = get_db().execute(
        "SELECT * FROM social_posts WHERE id = ? AND universite = ?",
        (clean_text(post_id, 80), campus),
    ).fetchone()
    if not row or row["hidden"]:
        raise ValueError("NOT_FOUND")
    if not _visible_for_actor(row, actor):
        raise ValueError("FORBIDDEN")
    likes = _parse_likes(row["likes_json"])
    if email in likes:
        likes = [x for x in likes if x != email]
    else:
        likes.append(email)
    get_db().execute(
        "UPDATE social_posts SET likes_json = ? WHERE id = ?",
        (json.dumps(likes), row["id"]),
    )
    get_db().commit()
    updated = get_db().execute("SELECT * FROM social_posts WHERE id = ?", (row["id"],)).fetchone()
    return _row_to_post(updated, email)


def delete_post(actor: dict, post_id: str) -> dict:
    campus = _campus(actor)
    email = (actor.get("email") or actor.get("identifiant") or "").lower()
    row = get_db().execute(
        "SELECT * FROM social_posts WHERE id = ? AND universite = ?",
        (clean_text(post_id, 80), campus),
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    role = actor.get("role")
    if (row["author_email"] or "").lower() != email and role not in MODERATE_ROLES.union({"universite"}):
        raise ValueError("FORBIDDEN")
    get_db().execute("DELETE FROM social_posts WHERE id = ?", (row["id"],))
    get_db().commit()
    return {"ok": True, "id": row["id"]}


def set_hidden(actor: dict, post_id: str, hidden: bool) -> dict:
    if actor.get("role") not in MODERATE_ROLES.union({"universite"}):
        raise ValueError("FORBIDDEN")
    campus = _campus(actor)
    row = get_db().execute(
        "SELECT * FROM social_posts WHERE id = ? AND universite = ?",
        (clean_text(post_id, 80), campus),
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    get_db().execute(
        "UPDATE social_posts SET hidden = ? WHERE id = ?",
        (1 if hidden else 0, row["id"]),
    )
    get_db().commit()
    updated = get_db().execute("SELECT * FROM social_posts WHERE id = ?", (row["id"],)).fetchone()
    email = (actor.get("email") or actor.get("identifiant") or "").lower()
    return _row_to_post(updated, email)
