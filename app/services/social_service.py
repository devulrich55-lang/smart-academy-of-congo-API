"""Réseau social campus — fil, réactions, commentaires, messagerie, événements."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.database import get_db
from app.utils.platform_security import uid
from app.utils.sanitize import clean_text

POST_ROLES = frozenset({"etudiant", "professeur", "assistant"})
MODERATE_ROLES = frozenset({"universite", "section", "ministere"})
AUDIENCES = frozenset({"campus", "filiere", "promotion"})
POST_TYPES = frozenset({"text", "photo", "document", "event"})
REACTIONS = frozenset({"like", "love", "celebrate", "support"})
SOCIAL_MEDIA_MAX = 8 * 1024 * 1024
SOCIAL_DOC_MAX = 12 * 1024 * 1024
SOCIAL_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
SOCIAL_DOC_EXT = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".txt"}
HASHTAG_RE = re.compile(r"#([\w\u00C0-\u024F\u0400-\u04FF]{2,40})", re.UNICODE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _campus(actor: dict) -> str:
    return clean_text(
        actor.get("universite") or actor.get("codeUni") or actor.get("sigle"), 80
    )


def _email(actor: dict) -> str:
    return (actor.get("email") or actor.get("identifiant") or "").lower()


def _author_name(actor: dict) -> str:
    return " ".join(
        p for p in [clean_text(actor.get("prenom"), 80), clean_text(actor.get("nom"), 80)] if p
    ).strip() or clean_text(actor.get("email"), 255)


def _json_load(val, default):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (TypeError, json.JSONDecodeError):
        return default


def _parse_reactions(raw, likes_raw) -> dict:
    reactions = _json_load(raw, {})
    if not isinstance(reactions, dict):
        reactions = {}
    likes = _json_load(likes_raw, [])
    if isinstance(likes, list) and likes and "like" not in reactions:
        reactions["like"] = [str(x).lower() for x in likes]
    out = {}
    for key in REACTIONS:
        emails = reactions.get(key) or []
        if isinstance(emails, list):
            out[key] = list(dict.fromkeys(str(x).lower() for x in emails))
    return out


def _extract_hashtags(content: str, extra: list | None = None) -> list[str]:
    found = [m.group(1) for m in HASHTAG_RE.finditer(content or "")]
    tags = []
    for t in (extra or []) + found:
        tag = clean_text(str(t).lstrip("#"), 40)
        if tag and tag.lower() not in [x.lower() for x in tags]:
            tags.append(tag)
    return tags[:12]


def _group_key(audience: str, filiere: str, niveau: str) -> str:
    if audience == "promotion" and filiere and niveau:
        return f"promo:{filiere}:{niveau}"
    if audience == "filiere" and filiere:
        return f"filiere:{filiere}"
    return "campus"


def _notify(
    universite: str,
    recipient: str,
    ntype: str,
    title: str,
    message: str,
    post_id: str = "",
) -> None:
    if not recipient:
        return
    get_db().execute(
        """INSERT INTO social_notifications
           (id, universite, recipient_email, type, title, message, post_id, read_flag, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
        (uid("sntf"), universite, recipient.lower(), ntype, title, message, post_id or "", _now()),
    )


def _dm_enabled(universite: str) -> bool:
    row = get_db().execute(
        "SELECT private_dm_enabled FROM social_campus_settings WHERE universite = ?",
        (universite,),
    ).fetchone()
    if not row:
        return True
    return bool(row["private_dm_enabled"])


def _row_to_post(row, viewer_email: str = "", comments: list | None = None) -> dict:
    reactions = _parse_reactions(
        row["reactions_json"] if "reactions_json" in row.keys() else None,
        row["likes_json"],
    )
    email = (viewer_email or "").lower()
    my_reactions = [k for k, emails in reactions.items() if email in emails]
    total_reactions = sum(len(v) for v in reactions.values())
    hashtags = _json_load(row["hashtags_json"] if "hashtags_json" in row.keys() else None, [])
    return {
        "id": row["id"],
        "universite": row["universite"] or "",
        "authorEmail": row["author_email"] or "",
        "authorName": row["author_name"] or "",
        "authorRole": row["author_role"] or "",
        "content": row["content"] or "",
        "postType": (row["post_type"] if "post_type" in row.keys() else None) or "text",
        "mediaUrl": (row["media_url"] if "media_url" in row.keys() else None) or "",
        "mediaName": (row["media_name"] if "media_name" in row.keys() else None) or "",
        "audience": row["audience"] or "campus",
        "filiere": row["filiere"] or "",
        "niveau": (row["niveau"] if "niveau" in row.keys() else None) or "",
        "groupKey": (row["group_key"] if "group_key" in row.keys() else None) or "",
        "hashtags": hashtags,
        "pinned": bool(row["pinned"]) if "pinned" in row.keys() else False,
        "eventAt": (row["event_at"] if "event_at" in row.keys() else None) or "",
        "eventTitle": (row["event_title"] if "event_title" in row.keys() else None) or "",
        "reactions": reactions,
        "reactionCount": total_reactions,
        "myReactions": my_reactions,
        "likes": reactions.get("like", []),
        "likeCount": len(reactions.get("like", [])),
        "likedByMe": email in reactions.get("like", []),
        "commentCount": len(comments) if comments is not None else int(row["comment_count"] or 0)
        if "comment_count" in row.keys()
        else 0,
        "comments": comments or [],
        "hidden": bool(row["hidden"]),
        "createdAt": row["created_at"],
    }


def _visible_for_actor(row, actor: dict) -> bool:
    if row["hidden"]:
        role = actor.get("role")
        if role not in MODERATE_ROLES:
            em = _email(actor)
            if (row["author_email"] or "").lower() != em:
                return False
    campus = _campus(actor)
    if row["universite"] != campus:
        return False
    audience = row["audience"] or "campus"
    actor_fil = clean_text(actor.get("filiere"), 120).lower()
    actor_niv = clean_text(actor.get("niveau"), 40).lower()
    post_fil = (row["filiere"] or "").lower()
    post_niv = (row["niveau"] or "").lower() if "niveau" in row.keys() else ""
    if audience == "filiere" and post_fil and actor_fil:
        if post_fil not in actor_fil and actor_fil not in post_fil:
            return False
    if audience == "promotion":
        if post_fil and actor_fil and post_fil not in actor_fil and actor_fil not in post_fil:
            return False
        if post_niv and actor_niv and post_niv != actor_niv:
            return False
    return True


def _score_post(row, actor: dict, interest_tags: set[str]) -> float:
    score = 0.0
    if "pinned" in row.keys() and row["pinned"]:
        score += 1000
    actor_fil = clean_text(actor.get("filiere"), 120).lower()
    actor_niv = clean_text(actor.get("niveau"), 40).lower()
    if (row["filiere"] or "").lower() and actor_fil:
        pf = (row["filiere"] or "").lower()
        if pf in actor_fil or actor_fil in pf:
            score += 40
    if "niveau" in row.keys() and (row["niveau"] or "").lower() == actor_niv:
        score += 25
    tags = _json_load(row["hashtags_json"] if "hashtags_json" in row.keys() else None, [])
    score += 15 * len(set(t.lower() for t in tags) & interest_tags)
    if (row["author_email"] or "").lower() == _email(actor):
        score += 5
    try:
        score += min(10, (datetime.now(timezone.utc) - datetime.fromisoformat(
            str(row["created_at"]).replace("Z", "+00:00")
        )).total_seconds() / -86400)
    except (ValueError, TypeError):
        pass
    return score


def _comment_counts(post_ids: list[str]) -> dict[str, int]:
    if not post_ids:
        return {}
    counts = {pid: 0 for pid in post_ids}
    placeholders = ",".join("?" for _ in post_ids)
    rows = get_db().execute(
        f"SELECT post_id, COUNT(*) as c FROM social_comments WHERE post_id IN ({placeholders}) GROUP BY post_id",
        tuple(post_ids),
    ).fetchall()
    for r in rows:
        counts[r["post_id"]] = int(r["c"])
    return counts


def get_settings(actor: dict) -> dict:
    campus = _campus(actor)
    return {
        "privateDmEnabled": _dm_enabled(campus),
        "canModerate": actor.get("role") in MODERATE_ROLES,
        "canPost": actor.get("role") in POST_ROLES,
        "canMessage": actor.get("role") == "etudiant" and _dm_enabled(campus),
    }


def update_settings(actor: dict, data: dict) -> dict:
    if actor.get("role") not in MODERATE_ROLES.union({"universite"}):
        raise ValueError("FORBIDDEN")
    campus = _campus(actor)
    enabled = 1 if data.get("privateDmEnabled", True) else 0
    now = _now()
    existing = get_db().execute(
        "SELECT universite FROM social_campus_settings WHERE universite = ?", (campus,)
    ).fetchone()
    if existing:
        get_db().execute(
            "UPDATE social_campus_settings SET private_dm_enabled = ?, updated_at = ? WHERE universite = ?",
            (enabled, now, campus),
        )
    else:
        get_db().execute(
            "INSERT INTO social_campus_settings (universite, private_dm_enabled, updated_at) VALUES (?, ?, ?)",
            (campus, enabled, now),
        )
    get_db().commit()
    return get_settings(actor)


def list_posts(actor: dict, filters: dict | None = None) -> list[dict]:
    campus = _campus(actor)
    if not campus:
        raise ValueError("INVALID_INPUT")
    filters = filters or {}
    email = _email(actor)
    rows = get_db().execute(
        """SELECT * FROM social_posts
           WHERE universite = ?
           ORDER BY created_at DESC
           LIMIT 300""",
        (campus,),
    ).fetchall()
    q = clean_text(filters.get("q"), 120).lower()
    hashtag = clean_text(filters.get("hashtag"), 40).lstrip("#").lower()
    group = clean_text(filters.get("group"), 80).lower()
    feed = clean_text(filters.get("feed"), 20) or "all"
    interest_tags: set[str] = set()
    if feed == "personal":
        liked_rows = get_db().execute(
            "SELECT hashtags_json FROM social_posts WHERE universite = ? LIMIT 100",
            (campus,),
        ).fetchall()
        for lr in liked_rows:
            reactions = _parse_reactions(
                lr["reactions_json"] if "reactions_json" in lr.keys() else None,
                lr["likes_json"],
            )
            if email in reactions.get("like", []):
                for t in _json_load(lr["hashtags_json"] if "hashtags_json" in lr.keys() else None, []):
                    interest_tags.add(str(t).lower())

    visible = []
    for row in rows:
        if not _visible_for_actor(row, actor):
            continue
        if q:
            blob = " ".join(
                [
                    row["content"] or "",
                    row["author_name"] or "",
                    row["filiere"] or "",
                    " ".join(_json_load(row["hashtags_json"] if "hashtags_json" in row.keys() else None, [])),
                ]
            ).lower()
            if q not in blob:
                continue
        if hashtag:
            tags = [t.lower() for t in _json_load(row["hashtags_json"] if "hashtags_json" in row.keys() else None, [])]
            if hashtag not in tags:
                continue
        if group:
            gk = (row["group_key"] if "group_key" in row.keys() else "") or ""
            aud = row["audience"] or "campus"
            if group == "filiere" and aud != "filiere":
                continue
            if group == "promotion" and aud != "promotion":
                continue
            if group == "campus" and aud != "campus":
                continue
            if gk and group not in gk.lower() and gk.lower() not in group:
                if group not in (aud, gk.lower()):
                    continue
        visible.append(row)

    counts = _comment_counts([r["id"] for r in visible])
    posts = []
    for row in visible:
        p = _row_to_post(row, email)
        p["commentCount"] = counts.get(row["id"], 0)
        p["_score"] = _score_post(row, actor, interest_tags)
        posts.append(p)

    if feed == "personal":
        posts.sort(key=lambda x: (-int(x.get("pinned", False)), -x.get("_score", 0), x["createdAt"]), reverse=True)
    else:
        posts.sort(key=lambda x: (-int(x.get("pinned", False)), x["createdAt"]), reverse=True)

    for p in posts:
        p.pop("_score", None)
    return posts[:200]


def list_events(actor: dict) -> list[dict]:
    campus = _campus(actor)
    now = _now()
    rows = get_db().execute(
        """SELECT * FROM social_posts
           WHERE universite = ? AND event_at IS NOT NULL AND event_at >= ?
           ORDER BY event_at ASC LIMIT 30""",
        (campus, now),
    ).fetchall()
    email = _email(actor)
    out = []
    for row in rows:
        if not _visible_for_actor(row, actor):
            continue
        out.append(_row_to_post(row, email))
    return out


def trending_hashtags(actor: dict) -> list[dict]:
    campus = _campus(actor)
    rows = get_db().execute(
        "SELECT hashtags_json FROM social_posts WHERE universite = ? ORDER BY created_at DESC LIMIT 150",
        (campus,),
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        for tag in _json_load(row["hashtags_json"] if "hashtags_json" in row.keys() else None, []):
            key = str(tag).lower()
            counts[key] = counts.get(key, 0) + 1
    return [{"tag": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])[:15]]


def create_post(actor: dict, data: dict) -> dict:
    if actor.get("role") not in POST_ROLES and actor.get("role") not in MODERATE_ROLES:
        raise ValueError("FORBIDDEN")
    campus = _campus(actor)
    if not campus:
        raise ValueError("INVALID_INPUT")
    content = clean_text(data.get("content"), 2000)
    post_type = clean_text(data.get("postType"), 20) or "text"
    if post_type not in POST_TYPES:
        post_type = "text"
    media_url = clean_text(data.get("mediaUrl"), 500)
    if post_type in ("photo", "document") and not media_url and len(content) < 2:
        raise ValueError("INVALID_INPUT")
    if post_type == "text" and len(content) < 2:
        raise ValueError("INVALID_INPUT")
    audience = clean_text(data.get("audience"), 20) or "campus"
    if audience not in AUDIENCES:
        audience = "campus"
    filiere = clean_text(actor.get("filiere"), 120) if audience in ("filiere", "promotion") else ""
    niveau = clean_text(actor.get("niveau"), 40) if audience == "promotion" else ""
    hashtags = _extract_hashtags(content, data.get("hashtags") or [])
    pinned = bool(data.get("pinned")) and actor.get("role") in MODERATE_ROLES
    event_at = clean_text(data.get("eventAt"), 40) or ""
    event_title = clean_text(data.get("eventTitle"), 200) or ""
    if post_type == "event" and not event_at:
        raise ValueError("INVALID_INPUT")
    email = _email(actor)
    now = _now()
    item_id = uid("soc")
    get_db().execute(
        """INSERT INTO social_posts (
            id, universite, author_email, author_name, author_role,
            content, audience, filiere, niveau, group_key, post_type,
            media_url, media_name, hashtags_json, pinned, event_at, event_title,
            likes_json, reactions_json, hidden, comment_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '{}', 0, 0, ?)""",
        (
            item_id,
            campus,
            email,
            _author_name(actor),
            actor.get("role") or "",
            content,
            audience,
            filiere,
            niveau,
            _group_key(audience, filiere, niveau),
            post_type,
            media_url,
            clean_text(data.get("mediaName"), 200),
            json.dumps(hashtags),
            1 if pinned else 0,
            event_at or None,
            event_title,
            now,
        ),
    )
    get_db().commit()
    row = get_db().execute("SELECT * FROM social_posts WHERE id = ?", (item_id,)).fetchone()
    return _row_to_post(row, email)


def save_upload(actor: dict, filename: str, content: bytes, kind: str) -> dict:
    if actor.get("role") not in POST_ROLES.union(MODERATE_ROLES):
        raise ValueError("FORBIDDEN")
    ext = Path(filename or "").suffix.lower()
    if kind == "photo":
        if ext not in SOCIAL_IMAGE_EXT:
            ext = ".jpg"
        if len(content) > SOCIAL_MEDIA_MAX:
            raise ValueError("FILE_TOO_LARGE")
    else:
        if ext not in SOCIAL_DOC_EXT:
            ext = ".pdf"
        if len(content) > SOCIAL_DOC_MAX:
            raise ValueError("FILE_TOO_LARGE")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    name = f"social-{uid('med').split('-')[-1]}{ext}"
    dest = settings.upload_dir / name
    dest.write_bytes(content)
    return {"ok": True, "mediaUrl": f"/uploads/{name}", "mediaName": filename or name}


def toggle_reaction(actor: dict, post_id: str, reaction: str) -> dict:
    reaction = clean_text(reaction, 20) or "like"
    if reaction not in REACTIONS:
        reaction = "like"
    campus = _campus(actor)
    email = _email(actor)
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
    reactions = _parse_reactions(
        row["reactions_json"] if "reactions_json" in row.keys() else None,
        row["likes_json"],
    )
    active = None
    for key in REACTIONS:
        emails = reactions.get(key, [])
        if email in emails:
            emails = [x for x in emails if x != email]
            reactions[key] = emails
            if key == reaction:
                active = None
            else:
                active = key
    if active is None and reaction not in [k for k, v in reactions.items() if email in v]:
        reactions.setdefault(reaction, []).append(email)
        if (row["author_email"] or "").lower() != email:
            _notify(
                campus,
                row["author_email"],
                "reaction",
                "Nouvelle réaction",
                f"{_author_name(actor)} a réagi à votre publication.",
                row["id"],
            )
    get_db().execute(
        "UPDATE social_posts SET reactions_json = ?, likes_json = ? WHERE id = ?",
        (json.dumps(reactions), json.dumps(reactions.get("like", [])), row["id"]),
    )
    get_db().commit()
    updated = get_db().execute("SELECT * FROM social_posts WHERE id = ?", (row["id"],)).fetchone()
    return _row_to_post(updated, email)


def toggle_like(actor: dict, post_id: str) -> dict:
    return toggle_reaction(actor, post_id, "like")


def list_comments(actor: dict, post_id: str) -> list[dict]:
    campus = _campus(actor)
    row = get_db().execute(
        "SELECT * FROM social_posts WHERE id = ? AND universite = ?",
        (clean_text(post_id, 80), campus),
    ).fetchone()
    if not row or not _visible_for_actor(row, actor):
        raise ValueError("NOT_FOUND")
    rows = get_db().execute(
        """SELECT * FROM social_comments WHERE post_id = ?
           ORDER BY created_at ASC LIMIT 100""",
        (row["id"],),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "postId": r["post_id"],
            "authorEmail": r["author_email"],
            "authorName": r["author_name"],
            "authorRole": r["author_role"],
            "content": r["content"],
            "createdAt": r["created_at"],
        }
        for r in rows
    ]


def add_comment(actor: dict, post_id: str, content: str) -> dict:
    campus = _campus(actor)
    email = _email(actor)
    text = clean_text(content, 1000)
    if len(text) < 1:
        raise ValueError("INVALID_INPUT")
    row = get_db().execute(
        "SELECT * FROM social_posts WHERE id = ? AND universite = ?",
        (clean_text(post_id, 80), campus),
    ).fetchone()
    if not row or row["hidden"] or not _visible_for_actor(row, actor):
        raise ValueError("NOT_FOUND")
    cid = uid("scmt")
    now = _now()
    get_db().execute(
        """INSERT INTO social_comments
           (id, post_id, universite, author_email, author_name, author_role, content, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (cid, row["id"], campus, email, _author_name(actor), actor.get("role") or "", text, now),
    )
    count = int(row["comment_count"] or 0) + 1 if "comment_count" in row.keys() else 1
    get_db().execute(
        "UPDATE social_posts SET comment_count = ? WHERE id = ?",
        (count, row["id"]),
    )
    if (row["author_email"] or "").lower() != email:
        _notify(
            campus,
            row["author_email"],
            "comment",
            "Nouveau commentaire",
            f"{_author_name(actor)} a commenté votre publication.",
            row["id"],
        )
    get_db().commit()
    return {
        "id": cid,
        "postId": row["id"],
        "authorEmail": email,
        "authorName": _author_name(actor),
        "authorRole": actor.get("role") or "",
        "content": text,
        "createdAt": now,
    }


def set_pinned(actor: dict, post_id: str, pinned: bool) -> dict:
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
        "UPDATE social_posts SET pinned = ? WHERE id = ?",
        (1 if pinned else 0, row["id"]),
    )
    get_db().commit()
    updated = get_db().execute("SELECT * FROM social_posts WHERE id = ?", (row["id"],)).fetchone()
    return _row_to_post(updated, _email(actor))


def delete_post(actor: dict, post_id: str) -> dict:
    campus = _campus(actor)
    email = _email(actor)
    row = get_db().execute(
        "SELECT * FROM social_posts WHERE id = ? AND universite = ?",
        (clean_text(post_id, 80), campus),
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    role = actor.get("role")
    if (row["author_email"] or "").lower() != email and role not in MODERATE_ROLES.union({"universite"}):
        raise ValueError("FORBIDDEN")
    get_db().execute("DELETE FROM social_comments WHERE post_id = ?", (row["id"],))
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
    return _row_to_post(updated, _email(actor))


def list_notifications(actor: dict) -> list[dict]:
    campus = _campus(actor)
    email = _email(actor)
    rows = get_db().execute(
        """SELECT * FROM social_notifications
           WHERE universite = ? AND recipient_email = ?
           ORDER BY created_at DESC LIMIT 50""",
        (campus, email),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "title": r["title"],
            "message": r["message"],
            "postId": r["post_id"] or "",
            "read": bool(r["read_flag"]),
            "createdAt": r["created_at"],
        }
        for r in rows
    ]


def mark_notification_read(actor: dict, notif_id: str) -> dict:
    campus = _campus(actor)
    email = _email(actor)
    get_db().execute(
        """UPDATE social_notifications SET read_flag = 1
           WHERE id = ? AND universite = ? AND recipient_email = ?""",
        (clean_text(notif_id, 80), campus, email),
    )
    get_db().commit()
    return {"ok": True}


def list_conversations(actor: dict) -> list[dict]:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")
    campus = _campus(actor)
    email = _email(actor)
    if not _dm_enabled(campus):
        return []
    rows = get_db().execute(
        """SELECT * FROM social_messages
           WHERE universite = ? AND (from_email = ? OR to_email = ?)
           ORDER BY created_at DESC LIMIT 500""",
        (campus, email, email),
    ).fetchall()
    convos: dict[str, dict] = {}
    for r in rows:
        peer = r["to_email"] if r["from_email"] == email else r["from_email"]
        peer_name = r["to_name"] if r["from_email"] == email else r["from_name"]
        if peer not in convos:
            unread = 0
            if r["to_email"] == email and not r["read_flag"]:
                unread = 1
            convos[peer] = {
                "peerEmail": peer,
                "peerName": peer_name or peer,
                "lastMessage": r["body"],
                "lastAt": r["created_at"],
                "unread": unread,
            }
        elif r["to_email"] == email and not r["read_flag"]:
            convos[peer]["unread"] = convos[peer].get("unread", 0) + 1
    return sorted(convos.values(), key=lambda x: x["lastAt"], reverse=True)


def list_messages(actor: dict, peer_email: str) -> list[dict]:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")
    campus = _campus(actor)
    email = _email(actor)
    peer = clean_text(peer_email, 255).lower()
    if not peer:
        raise ValueError("INVALID_INPUT")
    rows = get_db().execute(
        """SELECT * FROM social_messages
           WHERE universite = ? AND (
             (from_email = ? AND to_email = ?) OR (from_email = ? AND to_email = ?)
           ) ORDER BY created_at ASC LIMIT 200""",
        (campus, email, peer, peer, email),
    ).fetchall()
    get_db().execute(
        """UPDATE social_messages SET read_flag = 1
           WHERE universite = ? AND from_email = ? AND to_email = ? AND read_flag = 0""",
        (campus, peer, email),
    )
    get_db().commit()
    return [
        {
            "id": r["id"],
            "fromEmail": r["from_email"],
            "fromName": r["from_name"],
            "toEmail": r["to_email"],
            "toName": r["to_name"],
            "body": r["body"],
            "mine": r["from_email"] == email,
            "createdAt": r["created_at"],
        }
        for r in rows
    ]


def send_message(actor: dict, data: dict) -> dict:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")
    campus = _campus(actor)
    if not _dm_enabled(campus):
        raise ValueError("DM_DISABLED")
    email = _email(actor)
    to_email = clean_text(data.get("toEmail"), 255).lower()
    body = clean_text(data.get("body"), 2000)
    if not to_email or len(body) < 1:
        raise ValueError("INVALID_INPUT")
    if to_email == email:
        raise ValueError("INVALID_INPUT")
    peer = get_db().execute(
        "SELECT email, prenom, nom, role FROM users WHERE email = ? AND universite = ?",
        (to_email, campus),
    ).fetchone()
    if not peer or peer["role"] != "etudiant":
        raise ValueError("NOT_FOUND")
    to_name = " ".join(p for p in [peer["prenom"], peer["nom"]] if p).strip() or to_email
    mid = uid("smsg")
    now = _now()
    get_db().execute(
        """INSERT INTO social_messages
           (id, universite, from_email, from_name, to_email, to_name, body, read_flag, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
        (mid, campus, email, _author_name(actor), to_email, to_name, body, now),
    )
    _notify(campus, to_email, "message", "Nouveau message", f"{_author_name(actor)} vous a écrit.", "")
    get_db().commit()
    return {
        "id": mid,
        "fromEmail": email,
        "fromName": _author_name(actor),
        "toEmail": to_email,
        "toName": to_name,
        "body": body,
        "mine": True,
        "createdAt": now,
    }
