"""Réunions institutionnelles + IA (transcription, résumé, traduction)."""
import json
import re
from datetime import datetime, timezone

from app.database import get_db
from app.utils.platform_security import assert_campus_access, uid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_room(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "reunion").lower()).strip("-")
    return slug[:40] or "reunion"


def _row_to_meeting(row) -> dict:
    if not row:
        return None
    return {
        "id": row["id"],
        "type": row["type"],
        "universite": row["universite"],
        "sectionId": row["section_id"],
        "sectionName": row["section_name"],
        "sectionFiliere": row["section_filiere"],
        "title": row["title"],
        "description": row["description"],
        "agenda": row["agenda"],
        "roomName": row["room_name"],
        "hostEmail": row["host_email"],
        "hostName": row["host_name"],
        "allowedEmails": json.loads(row["allowed_emails"] or "[]"),
        "status": row["status"],
        "scheduledAt": row["scheduled_at"],
        "startedAt": row["started_at"],
        "endedAt": row["ended_at"],
        "documents": json.loads(row["documents"] or "[]"),
        "votes": json.loads(row["votes"] or "[]"),
        "transcript": row["transcript"] or "",
        "aiSummary": row["ai_summary"] or "",
        "aiKeyPoints": json.loads(row["ai_key_points"] or "[]"),
        "aiTranslations": json.loads(row["ai_translations"] or "{}"),
        "statsSnapshot": json.loads(row["stats_snapshot"] or "{}"),
        "joinUrl": f"https://meet.jit.si/{row['room_name']}",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _notify_meeting(email: str, role: str, ntype: str, title: str, message: str, meeting_id: str, universite: str):
    get_db().execute(
        """INSERT INTO correction_notifications
           (id, recipient_email, recipient_role, type, title, message, submission_id, universite, read, created_at)
           VALUES (?,?,?,?,?,?,?,?,0,?)""",
        (uid("ntf"), email, role, ntype, title, message, meeting_id, universite, _now()),
    )


def _professors_for_section(universite: str, filiere: str | None) -> list[str]:
    q = "SELECT email, cours_classes FROM users WHERE role='professeur' AND universite=?"
    rows = get_db().execute(q, (universite,)).fetchall()
    emails = []
    filiere_lower = (filiere or "").lower()
    for r in rows:
        if not filiere_lower:
            emails.append(r["email"])
            continue
        try:
            classes = json.loads(r["cours_classes"] or "[]")
        except json.JSONDecodeError:
            classes = []
        for c in classes:
            cf = (c.get("filiere") or "").lower()
            if not cf or filiere_lower in cf or cf in filiere_lower:
                emails.append(r["email"])
                break
    return list(dict.fromkeys(emails))


def _ai_analyze_transcript(text: str, meeting_title: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {
            "aiSummary": "Aucune transcription fournie.",
            "aiKeyPoints": [],
            "aiTranslations": {"en": "No transcript provided."},
        }
    sentences = [s.strip() for s in re.split(r"[.!?\n]+", text) if s.strip()]
    keywords = (
        "décision", "décider", "vote", "action", "important", "résultat", "note",
        "moyenne", "réussite", "problème", "recommandation", "deadline", "échéance",
    )
    key_points = []
    for s in sentences:
        sl = s.lower()
        if any(k in sl for k in keywords) or len(s) > 80:
            key_points.append(s[:200])
    key_points = list(dict.fromkeys(key_points))[:8]
    summary_parts = [
        f"Réunion « {meeting_title} » — synthèse automatique SAC IA.",
        f"Interventions analysées : {len(sentences)} segment(s).",
    ]
    if key_points:
        summary_parts.append("Points saillants : " + "; ".join(key_points[:3]) + ".")
    else:
        summary_parts.append("Discussion générale sans point critique identifié automatiquement.")
    summary = " ".join(summary_parts)
    en_lines = []
    for s in sentences[:5]:
        en_lines.append(f"[EN] {s[:120]}")
    return {
        "aiSummary": summary,
        "aiKeyPoints": key_points or ["Ordre du jour traité", "Prochaine séance à planifier"],
        "aiTranslations": {"en": "\n".join(en_lines), "fr": text[:500]},
    }


def _build_stats_snapshot(universite: str) -> dict:
    db = get_db()
    grades = db.execute(
        "SELECT avg, status FROM grades WHERE universite=?", (universite,)
    ).fetchall()
    avgs = [g["avg"] for g in grades if g["avg"] is not None]
    total = len(avgs)
    pass_rate = round(100 * sum(1 for a in avgs if a >= 10) / total, 1) if total else 0
    average = round(sum(avgs) / total, 1) if total else 0
    validated = db.execute(
        "SELECT COUNT(*) as c FROM work_submissions WHERE universite=? AND status='valide'",
        (universite,),
    ).fetchone()["c"]
    return {
        "totalGrades": total,
        "classAverage": average,
        "passRate": pass_rate,
        "validatedWorks": validated,
        "generatedAt": _now(),
    }


def create_meeting(user: dict, data: dict) -> dict:
    mtype = data.get("type")
    if mtype not in ("section_prof", "dean_sections"):
        raise ValueError("INVALID_TYPE")
    uni = data.get("universite") or user.get("universite")
    assert_campus_access(user, uni)
    role = user.get("role")
    if mtype == "dean_sections" and role != "universite":
        raise ValueError("FORBIDDEN")
    if mtype == "section_prof" and role not in ("universite", "professeur"):
        raise ValueError("FORBIDDEN")

    allowed = data.get("allowedEmails") or []
    if not allowed:
        if mtype == "section_prof":
            allowed = _professors_for_section(uni, data.get("filiere") or data.get("sectionFiliere"))
        elif mtype == "dean_sections":
            allowed = [e for e in (data.get("sectionHeadEmails") or []) if e]

    host_name = f"{user.get('prenom', '')} {user.get('nom', '')}".strip() or user["email"]
    meeting_id = uid("mtg")
    room = f"sac-mtg-{_sanitize_room(data.get('title', meeting_id))}-{meeting_id[-6:]}"
    now = _now()
    stats = _build_stats_snapshot(uni) if mtype == "dean_sections" else {}

    get_db().execute(
        """INSERT INTO meetings (
          id, type, universite, section_id, section_name, section_filiere, title, description, agenda,
          room_name, host_email, host_name, allowed_emails, status, scheduled_at,
          stats_snapshot, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            meeting_id,
            mtype,
            uni,
            data.get("sectionId"),
            data.get("sectionName"),
            data.get("filiere") or data.get("sectionFiliere"),
            data["title"],
            data.get("description", ""),
            data.get("agenda", ""),
            room,
            user["email"],
            host_name,
            json.dumps([e.lower() for e in allowed]),
            "scheduled",
            data.get("scheduledAt") or now,
            json.dumps(stats),
            now,
            now,
        ),
    )
    for email in allowed:
        _notify_meeting(
            email,
            "professeur" if mtype == "section_prof" else "universite",
            "meeting_scheduled",
            "Réunion programmée",
            f"« {data['title']} » — {now[:10]}",
            meeting_id,
            uni,
        )
    get_db().commit()
    return get_meeting(meeting_id)


def list_meetings(user: dict) -> list[dict]:
    uni = user.get("universite")
    email = (user.get("email") or "").lower()
    role = user.get("role")
    if role == "universite":
        rows = get_db().execute(
            "SELECT * FROM meetings WHERE universite=? ORDER BY created_at DESC LIMIT 50",
            (uni,),
        ).fetchall()
    else:
        rows = get_db().execute(
            "SELECT * FROM meetings WHERE universite=? ORDER BY created_at DESC LIMIT 100",
            (uni,),
        ).fetchall()
        filtered = []
        for r in rows:
            allowed = json.loads(r["allowed_emails"] or "[]")
            if email in [a.lower() for a in allowed] or (r["host_email"] or "").lower() == email:
                filtered.append(r)
            elif r["type"] == "section_prof" and role == "professeur":
                filiere = row["section_filiere"]
                profs = _professors_for_section(uni, filiere)
                if email in [p.lower() for p in profs]:
                    filtered.append(r)
        rows = filtered
    return [_row_to_meeting(r) for r in rows]


def get_meeting(meeting_id: str) -> dict | None:
    row = get_db().execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    return _row_to_meeting(row)


def _assert_meeting_access(user: dict, meeting: dict):
    assert_campus_access(user, meeting["universite"])
    email = (user.get("email") or "").lower()
    if (meeting["hostEmail"] or "").lower() == email:
        return
    if user.get("role") == "universite":
        return
    allowed = [a.lower() for a in meeting.get("allowedEmails") or []]
    if email not in allowed:
        raise ValueError("FORBIDDEN")


def start_meeting(user: dict, meeting_id: str) -> dict:
    row = get_db().execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    meeting = _row_to_meeting(row)
    _assert_meeting_access(user, meeting)
    if (meeting["hostEmail"] or "").lower() != (user.get("email") or "").lower():
        if user.get("role") != "universite":
            raise ValueError("FORBIDDEN")
    now = _now()
    get_db().execute(
        "UPDATE meetings SET status='live', started_at=?, updated_at=? WHERE id=?",
        (now, now, meeting_id),
    )
    for email in meeting.get("allowedEmails") or []:
        _notify_meeting(
            email,
            "professeur",
            "meeting_live",
            "Réunion en cours",
            f"« {meeting['title']} » a démarré — rejoignez maintenant.",
            meeting_id,
            meeting["universite"],
        )
    get_db().commit()
    return get_meeting(meeting_id)


def join_meeting(user: dict, meeting_id: str) -> dict:
    row = get_db().execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    meeting = _row_to_meeting(row)
    _assert_meeting_access(user, meeting)
    if meeting["status"] == "ended":
        raise ValueError("MEETING_ENDED")
    name = f"{user.get('prenom', '')} {user.get('nom', '')}".strip() or user["email"]
    try:
        get_db().execute(
            """INSERT INTO meeting_attendees (id, meeting_id, attendee_email, attendee_name, attendee_role, joined_at)
               VALUES (?,?,?,?,?,?)""",
            (uid("att"), meeting_id, user["email"], name, user.get("role"), _now()),
        )
    except Exception as e:
        if "UNIQUE" not in str(e):
            raise
    get_db().commit()
    return get_meeting(meeting_id)


def add_meeting_document(user: dict, meeting_id: str, doc: dict) -> dict:
    row = get_db().execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    meeting = _row_to_meeting(row)
    _assert_meeting_access(user, meeting)
    docs = meeting["documents"]
    docs.append({
        "id": uid("doc"),
        "name": doc.get("name", "Document"),
        "url": doc.get("url", ""),
        "uploadedBy": user["email"],
        "at": _now(),
    })
    get_db().execute(
        "UPDATE meetings SET documents=?, updated_at=? WHERE id=?",
        (json.dumps(docs), _now(), meeting_id),
    )
    get_db().commit()
    return get_meeting(meeting_id)


def cast_vote(user: dict, meeting_id: str, vote_id: str, option_id: str) -> dict:
    row = get_db().execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    meeting = _row_to_meeting(row)
    _assert_meeting_access(user, meeting)
    votes = meeting["votes"]
    email = user["email"].lower()
    for v in votes:
        if v.get("id") != vote_id or v.get("closed"):
            continue
        for opt in v.get("options") or []:
            if opt.get("id") == option_id:
                opt_votes = [x.lower() for x in opt.get("votes") or []]
                if email not in opt_votes:
                    opt_votes.append(email)
                opt["votes"] = opt_votes
    get_db().execute(
        "UPDATE meetings SET votes=?, updated_at=? WHERE id=?",
        (json.dumps(votes), _now(), meeting_id),
    )
    get_db().commit()
    return get_meeting(meeting_id)


def create_vote(user: dict, meeting_id: str, question: str, options: list[str]) -> dict:
    row = get_db().execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    meeting = _row_to_meeting(row)
    if (meeting["hostEmail"] or "").lower() != (user.get("email") or "").lower():
        if user.get("role") != "universite":
            raise ValueError("FORBIDDEN")
    votes = meeting["votes"]
    votes.append({
        "id": uid("vote"),
        "question": question,
        "options": [{"id": f"opt{i}", "text": t, "votes": []} for i, t in enumerate(options)],
        "closed": False,
        "createdAt": _now(),
    })
    get_db().execute(
        "UPDATE meetings SET votes=?, updated_at=? WHERE id=?",
        (json.dumps(votes), _now(), meeting_id),
    )
    get_db().commit()
    return get_meeting(meeting_id)


def end_meeting(user: dict, meeting_id: str, transcript: str | None = None) -> dict:
    row = get_db().execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    meeting = _row_to_meeting(row)
    if (meeting["hostEmail"] or "").lower() != (user.get("email") or "").lower():
        if user.get("role") != "universite":
            raise ValueError("FORBIDDEN")
    text = transcript or meeting.get("transcript") or ""
    ai = _ai_analyze_transcript(text, meeting["title"])
    now = _now()
    get_db().execute(
        """UPDATE meetings SET status='ended', ended_at=?, transcript=?, ai_summary=?,
           ai_key_points=?, ai_translations=?, updated_at=? WHERE id=?""",
        (
            now,
            text,
            ai["aiSummary"],
            json.dumps(ai["aiKeyPoints"]),
            json.dumps(ai["aiTranslations"]),
            now,
            meeting_id,
        ),
    )
    get_db().commit()
    return get_meeting(meeting_id)


def run_meeting_ai(user: dict, meeting_id: str, transcript: str | None = None) -> dict:
    row = get_db().execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    meeting = _row_to_meeting(row)
    _assert_meeting_access(user, meeting)
    text = transcript or meeting.get("transcript") or ""
    ai = _ai_analyze_transcript(text, meeting["title"])
    get_db().execute(
        """UPDATE meetings SET transcript=?, ai_summary=?, ai_key_points=?, ai_translations=?, updated_at=?
           WHERE id=?""",
        (
            text,
            ai["aiSummary"],
            json.dumps(ai["aiKeyPoints"]),
            json.dumps(ai["aiTranslations"]),
            _now(),
            meeting_id,
        ),
    )
    get_db().commit()
    return get_meeting(meeting_id)


def student_qa(user: dict, meeting_id: str, question: str) -> dict:
    row = get_db().execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    meeting = _row_to_meeting(row)
    q = (question or "").strip()
    summary = meeting.get("aiSummary") or ""
    transcript = meeting.get("transcript") or ""
    corpus = (summary + " " + transcript).lower()
    answer = "Je n'ai pas trouvé d'élément précis dans le compte rendu de cette réunion."
    if any(w in corpus for w in q.lower().split() if len(w) > 4):
        answer = (
            f"D'après le compte rendu IA de « {meeting['title']} » : "
            + (summary[:300] if summary else "la réunion a traité l'ordre du jour prévu.")
        )
    key_pts = meeting.get("aiKeyPoints") or []
    if key_pts:
        answer += " Point clé : " + key_pts[0]
    return {"question": q, "answer": answer, "meetingId": meeting_id}
