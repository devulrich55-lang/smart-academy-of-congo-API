"""Cours en direct SAC — sessions, présence, replays."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.database import get_db
from app.utils.platform_security import assert_campus_access, uid
from app.utils.sanitize import clean_text

HOST_ROLES = frozenset({"professeur", "assistant", "section", "universite", "ministere"})
LIVE_RECORDING_MAX_BYTES = 100 * 1024 * 1024
LIVE_RECORDING_EXT = {".webm", ".mp4", ".mov"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_room(value: str, session_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "cours").lower()).strip("-")[:40]
    return f"sac-{slug or 'cours'}-{str(session_id)[-6:]}"


def _json_load(val, default):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (TypeError, json.JSONDecodeError):
        return default


def _professor_name(user: dict) -> str:
    return " ".join(
        p for p in [clean_text(user.get("prenom"), 80), clean_text(user.get("nom"), 80)] if p
    ).strip() or clean_text(user.get("email"), 255)


def _ai_analyze(text: str, title: str) -> dict:
    text = (text or "").strip()
    sentences = [s.strip() for s in re.split(r"[.!?\n]+", text) if s.strip()]
    keywords = (
        "décision", "important", "chapitre", "exercice", "examen", "résumé", "notion", "définition",
    )
    key_points = []
    for s in sentences:
        if any(k in s.lower() for k in keywords):
            key_points.append(s[:200])
    key_points = list(dict.fromkeys(key_points))[:8]
    summary = (
        f"Cours « {title} » — {len(sentences)} segment(s) transcrit(s). "
        + (
            "Points clés : " + "; ".join(key_points[:3]) + "."
            if key_points
            else "Contenu enregistré pour révision."
        )
    )
    return {
        "aiSummary": summary,
        "aiKeyPoints": key_points or ["Cours tenu en direct", "Supports partagés disponibles"],
    }


def _build_participation_report(row) -> dict:
    attendance = _json_load(row["attendance_json"], [])
    questions = _json_load(row["questions_json"], [])
    started = row["started_at"]
    ended = row["ended_at"]
    duration_min = None
    if started and ended:
        try:
            duration_min = max(
                1,
                round(
                    (
                        datetime.fromisoformat(ended.replace("Z", "+00:00"))
                        - datetime.fromisoformat(started.replace("Z", "+00:00"))
                    ).total_seconds()
                    / 60
                ),
            )
        except ValueError:
            duration_min = None
    return {
        "generatedAt": _now(),
        "totalPresent": len(attendance),
        "totalQuestions": len(questions),
        "durationMinutes": duration_min,
        "attendees": [
            {
                "name": a.get("name"),
                "email": a.get("email"),
                "role": a.get("role"),
                "joinedAt": a.get("joinedAt"),
                "durationMinutes": a.get("durationMinutes") or duration_min,
            }
            for a in attendance
        ],
        "engagementRate": min(100, round((len(questions) / len(attendance)) * 100))
        if attendance
        else 0,
    }


def _row_to_session(row) -> dict:
    if not row:
        return None
    session_id = row["id"]
    room = row["room_name"] or _sanitize_room(row["title"], session_id)
    return {
        "id": session_id,
        "universite": row["universite"],
        "professorEmail": row["professor_email"],
        "professorName": row["professor_name"],
        "title": row["title"],
        "description": row["description"] or "",
        "courseCode": row["course_code"] or "",
        "filiere": row["filiere"] or "",
        "niveau": row["niveau"] or "",
        "roomName": room,
        "status": row["status"],
        "joinUrl": f"sac-live:{room}",
        "scheduledAt": row["scheduled_at"],
        "startedAt": row["started_at"],
        "endedAt": row["ended_at"],
        "recordingUrl": row["recording_url"] or "",
        "transcript": row["transcript"] or "",
        "aiSummary": row["ai_summary"] or "",
        "aiKeyPoints": _json_load(row["ai_key_points_json"], []),
        "documents": _json_load(row["documents_json"], []),
        "attendance": _json_load(row["attendance_json"], []),
        "questions": _json_load(row["questions_json"], []),
        "participationReport": _json_load(row["participation_report_json"], None),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _get_row(session_id: str):
    return get_db().execute(
        "SELECT * FROM live_sessions WHERE id = ?",
        (clean_text(session_id, 80),),
    ).fetchone()


def _assert_host(user: dict, row) -> None:
    if user.get("role") not in HOST_ROLES:
        raise ValueError("FORBIDDEN")
    email = (user.get("email") or user.get("identifiant") or "").lower()
    if (row["professor_email"] or "").lower() != email and user.get("role") not in (
        "universite",
        "ministere",
        "section",
    ):
        raise ValueError("FORBIDDEN")


def _visible_for_user(row, user: dict) -> bool:
    campus = user.get("universite") or user.get("codeUni")
    if row["universite"] != campus and user.get("role") != "ministere":
        return False
    role = user.get("role")
    if role in HOST_ROLES and role != "section":
        return True
    if role == "section":
        sf = (user.get("filiere") or "").lower()
        xf = (row["filiere"] or "").lower()
        if not sf or not xf:
            return True
        return sf == xf or sf in xf or xf in sf
    if row["status"] not in ("live", "scheduled", "ended"):
        return False
    if not row["filiere"] and not row["niveau"]:
        return True
    uf = (user.get("filiere") or "").lower()
    un = (user.get("niveau") or "").lower()
    rf = (row["filiere"] or "").lower()
    rn = (row["niveau"] or "").lower()
    if rf and uf and rf not in uf and uf not in rf:
        return False
    if rn and un and rn != un:
        return False
    return True


def list_sessions(user: dict) -> list[dict]:
    assert_campus_access(user, user.get("universite"))
    campus = user.get("universite") or user.get("codeUni")
    rows = get_db().execute(
        """SELECT * FROM live_sessions
           WHERE universite = ?
           ORDER BY updated_at DESC
           LIMIT 200""",
        (campus,),
    ).fetchall()
    return [_row_to_session(r) for r in rows if _visible_for_user(r, user)]


def create_session(user: dict, data: dict) -> dict:
    if user.get("role") not in HOST_ROLES:
        raise ValueError("FORBIDDEN")
    campus = assert_campus_access(user, user.get("universite"))
    title = clean_text(data.get("title"), 200)
    if len(title) < 2:
        raise ValueError("INVALID_INPUT")
    session_id = uid("live")
    now = _now()
    room = _sanitize_room(title, session_id)
    email = (user.get("email") or user.get("identifiant") or "").lower()
    get_db().execute(
        """INSERT INTO live_sessions (
            id, universite, professor_email, professor_name, title, description,
            course_code, filiere, niveau, room_name, status, scheduled_at,
            documents_json, attendance_json, questions_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id,
            campus,
            email,
            _professor_name(user),
            title,
            clean_text(data.get("description"), 2000),
            clean_text(data.get("courseCode"), 40),
            clean_text(data.get("filiere") or user.get("filiere"), 120),
            clean_text(data.get("niveau") or user.get("niveau"), 40),
            room,
            "scheduled",
            data.get("scheduledAt") or now,
            "[]",
            "[]",
            "[]",
            now,
            now,
        ),
    )
    get_db().commit()
    return _row_to_session(_get_row(session_id))


def start_session(user: dict, session_id: str) -> dict:
    row = _get_row(session_id)
    if not row:
        raise ValueError("NOT_FOUND")
    assert_campus_access(user, row["universite"])
    _assert_host(user, row)
    now = _now()
    get_db().execute(
        "UPDATE live_sessions SET status='live', started_at=?, updated_at=? WHERE id=?",
        (now, now, row["id"]),
    )
    get_db().commit()
    return _row_to_session(_get_row(session_id))


def end_session(user: dict, session_id: str, data: dict) -> dict:
    row = _get_row(session_id)
    if not row:
        raise ValueError("NOT_FOUND")
    assert_campus_access(user, row["universite"])
    _assert_host(user, row)
    transcript = clean_text(data.get("transcript"), 20000) or (row["transcript"] or "")
    recording_url = clean_text(data.get("recordingUrl"), 500) or (row["recording_url"] or "")
    ai = _ai_analyze(transcript, row["title"])
    ended = _now()
    updated_row = dict(row)
    updated_row["ended_at"] = ended
    report = _build_participation_report(updated_row)
    get_db().execute(
        """UPDATE live_sessions SET
            status='ended', ended_at=?, recording_url=?, transcript=?,
            ai_summary=?, ai_key_points_json=?, participation_report_json=?,
            updated_at=?
           WHERE id=?""",
        (
            ended,
            recording_url,
            transcript,
            ai["aiSummary"],
            json.dumps(ai["aiKeyPoints"]),
            json.dumps(report),
            ended,
            row["id"],
        ),
    )
    get_db().commit()
    return _row_to_session(_get_row(session_id))


def join_session(user: dict, session_id: str) -> dict:
    row = _get_row(session_id)
    if not row:
        raise ValueError("NOT_FOUND")
    assert_campus_access(user, row["universite"])
    if not _visible_for_user(row, user):
        raise ValueError("FORBIDDEN")
    email = (user.get("email") or user.get("identifiant") or "").lower()
    if not email:
        raise ValueError("INVALID_INPUT")
    attendance = _json_load(row["attendance_json"], [])
    if not any((a.get("email") or "").lower() == email for a in attendance):
        attendance.insert(
            0,
            {
                "email": email,
                "name": _professor_name(user) if user.get("role") in HOST_ROLES else _professor_name(user),
                "role": user.get("role") or "",
                "joinedAt": _now(),
            },
        )
        get_db().execute(
            "UPDATE live_sessions SET attendance_json=?, updated_at=? WHERE id=?",
            (json.dumps(attendance[:500]), _now(), row["id"]),
        )
        get_db().commit()
    return _row_to_session(_get_row(session_id))


def save_recording(user: dict, session_id: str, filename: str, content: bytes) -> dict:
    row = _get_row(session_id)
    if not row:
        raise ValueError("NOT_FOUND")
    assert_campus_access(user, row["universite"])
    _assert_host(user, row)
    ext = Path(filename or "").suffix.lower()
    if ext not in LIVE_RECORDING_EXT:
        ext = ".webm"
    if len(content) > LIVE_RECORDING_MAX_BYTES:
        raise ValueError("FILE_TOO_LARGE")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    name = f"live-{session_id[-12:]}-{uid('rec').split('-')[-1]}{ext}"
    dest = settings.upload_dir / name
    dest.write_bytes(content)
    recording_url = f"/uploads/{name}"
    now = _now()
    get_db().execute(
        "UPDATE live_sessions SET recording_url=?, updated_at=? WHERE id=?",
        (recording_url, now, row["id"]),
    )
    get_db().commit()
    return {"ok": True, "recordingUrl": recording_url, "session": _row_to_session(_get_row(session_id))}
