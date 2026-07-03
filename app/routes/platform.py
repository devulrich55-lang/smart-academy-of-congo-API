from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from pathlib import Path
import uuid

from app.config import settings
from app.deps import get_current_user, require_roles
from app.rate_limit import limiter
from app.services import ai_correction_service, audit_service, career_service, course_service, dictionary_service, diploma_service, home_news_service, library_service, live_service, meeting_service, orientation_service, platform_service, social_service
from app.services import reclamation_service
from app.services.user_service import get_campus_branding, list_students_for_professor, public_platform_stats
from app.utils.campus_catalog import catalog_payload, get_by_id, resolve_campus_id
from app.utils.guards import assert_submission_access, pick_fields, strip_identity_fields
from app.utils.pagination import clamp_page

router = APIRouter(prefix="/platform", tags=["platform"])

HOME_NEWS_MAX_SIZE = 5 * 1024 * 1024
HOME_NEWS_ALLOWED_EXT = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".mp3",
    ".wav",
    ".mp4",
    ".webm",
    ".mov",
    ".doc",
    ".docx",
}
HOME_NEWS_BLOCKED_EXT = {
    ".exe",
    ".bat",
    ".cmd",
    ".sh",
    ".php",
    ".js",
    ".html",
    ".svg",
    ".zip",
    ".rar",
}


def _home_news_media_kind(ext: str) -> str:
    if ext in {".jpg", ".jpeg", ".png", ".webp"}:
        return "image"
    if ext in {".mp4", ".webm", ".mov"}:
        return "video"
    if ext in {".mp3", ".wav"}:
        return "audio"
    return "document"


async def _save_home_news_uploads(files: list[UploadFile]) -> list[dict]:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    attachments = []
    for f in files[:3]:
        ext = Path(f.filename or "").suffix.lower()
        if ext in HOME_NEWS_BLOCKED_EXT:
            raise HTTPException(status_code=400, detail={"error": "INVALID_FILE"})
        safe_ext = ext.lstrip(".") if ext in HOME_NEWS_ALLOWED_EXT else "bin"
        name = f"{uuid.uuid4()}.{safe_ext}"
        dest = settings.upload_dir / name
        content = await f.read()
        if len(content) > HOME_NEWS_MAX_SIZE:
            raise HTTPException(
                status_code=413,
                detail={"error": "FILE_TOO_LARGE", "message": "Fichier max 5 Mo"},
            )
        dest.write_bytes(content)
        attachments.append(
            {
                "name": f.filename or name,
                "mediaPath": name,
                "mediaUrl": f"/uploads/{name}",
                "size": f"{len(content) // 1024} Ko",
                "type": _home_news_media_kind(ext).upper(),
                "mediaType": _home_news_media_kind(ext),
            }
        )
    return attachments


@router.post("/home-news/upload")
async def upload_home_news_media_route(
    files: list[UploadFile] = File(...),
    user: dict = Depends(require_roles("ministere", "universite")),
):
    del user
    saved = await _save_home_news_uploads(files)
    if not saved:
        raise HTTPException(status_code=400, detail={"error": "INVALID_INPUT"})
    primary = saved[0]
    return {
        "ok": True,
        "mediaUrl": primary["mediaUrl"],
        "mediaType": primary["mediaType"],
        "mediaName": primary["name"],
        "size": primary["size"],
        "attachments": saved[1:],
    }


@router.post("/diplomas/verify")
def verify_diploma_route(body: dict, request: Request):
    code = body.get("verificationCode")
    number = body.get("diplomaNumber")
    if not code or not number:
        return {"valid": False, "message": "Code et numéro de diplôme requis."}
    audit_service.log_audit(
        request,
        "verify_diploma",
        "diploma",
        meta={"number": str(number)[:12]},
    )
    return platform_service.verify_diploma(code, number)


@router.get("/campus-catalog")
def campus_catalog_route():
    """Catalogue national des établissements — public, aligné sac-universities.js."""
    return catalog_payload()


@router.get("/public-stats")
def public_stats_route():
    """Compteurs publics page d'accueil — universités partenaires et inscriptions."""
    return public_platform_stats()


@router.get("/campus-catalog/resolve")
def campus_catalog_resolve_route(q: str = Query("", alias="q")):
    campus_id = resolve_campus_id(q)
    item = get_by_id(campus_id) if campus_id else None
    return {"id": campus_id, "item": item}


@router.get("/grades/me")
def grades_me(
    user: dict = Depends(get_current_user),
    limit: int | None = Query(None, ge=1),
    offset: int | None = Query(None, ge=0),
):
    page_limit, page_offset = clamp_page(
        limit,
        offset,
        default=settings.api_page_default,
        maximum=settings.api_page_max,
    )
    role = user.get("role")
    if role == "professeur":
        grades = platform_service.list_grades_for_professor(
            user["email"], user["universite"], page_limit, page_offset
        )
    elif role in ("assistant", "universite"):
        grades = platform_service.list_grades_for_campus(
            user["universite"], page_limit, page_offset
        )
    else:
        grades = platform_service.list_grades_for_student(
            user["email"], user["universite"], page_limit, page_offset
        )
    return {
        "grades": grades,
        "pagination": {
            "limit": page_limit,
            "offset": page_offset,
            "hasMore": len(grades) == page_limit,
        },
    }


@router.get("/grades/transcript")
def grade_transcript(
    studentEmail: str,
    semester: str = "s1-2025",
    user: dict = Depends(get_current_user),
):
    try:
        return platform_service.get_transcript(user, studentEmail, semester)
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/grades")
def upsert_grade(body: dict, request: Request, user: dict = Depends(get_current_user)):
    try:
        row = platform_service.upsert_grade(user, strip_identity_fields(body))
        audit_service.log_audit(
            request,
            "upsert_grade",
            "grade",
            resource_id=row.get("id"),
            universite=body.get("universite"),
        )
        return {"grade": row}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/library")
def list_library(user: dict = Depends(get_current_user)):
    return {
        "items": platform_service.list_library(user["universite"], user["role"])
    }


@router.post("/library")
def create_library(body: dict, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = platform_service.create_library_item(user, body)
        audit_service.log_audit(
            request, "create_library", "library", resource_id=item.get("id"), universite=item.get("universite")
        )
        return {"item": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/careers")
def list_careers(scope: str | None = None, user: dict = Depends(get_current_user)):
    return {
        "items": platform_service.list_careers(user["universite"], scope)
    }


@router.post("/careers")
def create_career(body: dict, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = platform_service.create_career_post(user, body)
        audit_service.log_audit(request, "create_career", "career", resource_id=item.get("id"))
        return {"item": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/courses")
def list_courses(user: dict = Depends(get_current_user)):
    return {
        "items": platform_service.list_courses(
            user["universite"], user.get("filiere"), user.get("niveau")
        )
    }


@router.post("/courses")
def create_course(body: dict, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = platform_service.create_course(user, body)
        audit_service.log_audit(request, "create_course", "course", resource_id=item.get("id"))
        return {"item": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/courses/{course_id}/enroll")
def enroll_course(
    course_id: str, request: Request, user: dict = Depends(require_roles("etudiant"))
):
    try:
        enrollment = platform_service.enroll_course(user["email"], course_id)
        audit_service.log_audit(request, "enroll_course", "course", resource_id=course_id)
        return {"enrollment": enrollment}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/live/sessions")
def list_live_sessions(user: dict = Depends(get_current_user)):
    return {"sessions": platform_service.list_live_sessions(user)}


@router.post("/live/sessions")
def create_live_session(body: dict, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = platform_service.create_live_session(user, strip_identity_fields(body))
        audit_service.log_audit(request, "create_live_session", "live", resource_id=item.get("id"))
        return {"session": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/live/sessions/{session_id}")
def get_live_session(session_id: str, user: dict = Depends(get_current_user)):
    item = platform_service.get_live_session(session_id)
    if not item:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
    return {"session": item}


@router.post("/live/sessions/{session_id}/start")
def start_live_session(session_id: str, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = platform_service.start_live_session(user, session_id)
        audit_service.log_audit(request, "start_live_session", "live", resource_id=session_id)
        return {"session": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/live/sessions/{session_id}/end")
def end_live_session(
    session_id: str,
    body: dict,
    request: Request,
    user: dict = Depends(get_current_user),
):
    try:
        item = platform_service.end_live_session(
            user, session_id, body.get("recordingUrl") or body.get("recording_url")
        )
        audit_service.log_audit(request, "end_live_session", "live", resource_id=session_id)
        return {"session": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/live/sessions/{session_id}/join")
def join_live_session(session_id: str, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = platform_service.join_live_session(user, session_id)
        audit_service.log_audit(request, "join_live_session", "live", resource_id=session_id)
        return {"session": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.patch("/live/sessions/{session_id}/recording")
def update_live_recording(
    session_id: str,
    body: dict,
    request: Request,
    user: dict = Depends(get_current_user),
):
    try:
        url = body.get("recordingUrl") or body.get("recording_url")
        if not url:
            raise HTTPException(status_code=400, detail={"error": "RECORDING_URL_REQUIRED"})
        item = platform_service.update_live_recording(user, session_id, url)
        audit_service.log_audit(request, "update_live_recording", "live", resource_id=session_id)
        return {"session": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/meetings")
def list_meetings(user: dict = Depends(get_current_user)):
    return {"meetings": meeting_service.list_meetings(user)}


@router.post("/meetings")
def create_meeting(body: dict, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = meeting_service.create_meeting(user, strip_identity_fields(body))
        audit_service.log_audit(request, "create_meeting", "meeting", resource_id=item.get("id"))
        return {"meeting": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/meetings/{meeting_id}")
def get_meeting(meeting_id: str, user: dict = Depends(get_current_user)):
    item = meeting_service.get_meeting(meeting_id)
    if not item:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
    try:
        meeting_service._assert_meeting_access(user, item)
    except ValueError as e:
        _handle_platform_error(e)
    return {"meeting": item}


@router.post("/meetings/{meeting_id}/start")
def start_meeting(meeting_id: str, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = meeting_service.start_meeting(user, meeting_id)
        audit_service.log_audit(request, "start_meeting", "meeting", resource_id=meeting_id)
        return {"meeting": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/meetings/{meeting_id}/join")
def join_meeting(meeting_id: str, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = meeting_service.join_meeting(user, meeting_id)
        audit_service.log_audit(request, "join_meeting", "meeting", resource_id=meeting_id)
        return {"meeting": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/meetings/{meeting_id}/end")
def end_meeting(meeting_id: str, body: dict, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = meeting_service.end_meeting(
            user, meeting_id, body.get("transcript") or body.get("transcriptText")
        )
        audit_service.log_audit(request, "end_meeting", "meeting", resource_id=meeting_id)
        return {"meeting": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/meetings/{meeting_id}/documents")
def add_meeting_document(meeting_id: str, body: dict, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = meeting_service.add_meeting_document(user, meeting_id, body)
        audit_service.log_audit(request, "meeting_document", "meeting", resource_id=meeting_id)
        return {"meeting": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/meetings/{meeting_id}/votes")
def create_meeting_vote(meeting_id: str, body: dict, request: Request, user: dict = Depends(get_current_user)):
    try:
        item = meeting_service.create_vote(
            user, meeting_id, body.get("question", ""), body.get("options") or []
        )
        audit_service.log_audit(request, "meeting_vote_create", "meeting", resource_id=meeting_id)
        return {"meeting": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/meetings/{meeting_id}/votes/{vote_id}")
def cast_meeting_vote(
    meeting_id: str, vote_id: str, body: dict, user: dict = Depends(get_current_user)
):
    try:
        item = meeting_service.cast_vote(user, meeting_id, vote_id, body.get("optionId"))
        return {"meeting": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/meetings/{meeting_id}/ai")
def meeting_ai(meeting_id: str, body: dict, user: dict = Depends(get_current_user)):
    try:
        item = meeting_service.run_meeting_ai(
            user, meeting_id, body.get("transcript") or body.get("transcriptText")
        )
        return {"meeting": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/meetings/{meeting_id}/qa")
def meeting_qa(meeting_id: str, body: dict, user: dict = Depends(get_current_user)):
    try:
        return meeting_service.student_qa(user, meeting_id, body.get("question", ""))
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/diplomas/me")
def my_diplomas(user: dict = Depends(require_roles("etudiant"))):
    return {"diplomas": platform_service.list_diplomas_for_student(user["email"])}


@router.post("/diplomas/issue")
def issue_diploma(
    body: dict,
    request: Request,
    user: dict = Depends(require_roles("universite")),
):
    try:
        diploma = platform_service.issue_diploma(user, strip_identity_fields(body))
        audit_service.log_audit(
            request,
            "issue_diploma",
            "diploma",
            resource_id=diploma.get("id"),
            universite=body.get("universite"),
        )
        return {"diploma": diploma}
    except ValueError as e:
        _handle_platform_error(e)


_CORRECTION_ALLOWED_EXT = {".txt", ".md", ".pdf", ".doc", ".docx"}
_CORRECTION_BLOCKED_EXT = {".exe", ".bat", ".php", ".js", ".html", ".svg", ".zip", ".sh"}
_CORRECTION_MAX_SIZE = 5 * 1024 * 1024


@router.post("/corrections/submit")
async def submit_correction(request: Request, user: dict = Depends(require_roles("etudiant"))):
    try:
        ct = request.headers.get("content-type", "")
        if "multipart/form-data" in ct:
            form = await request.form()
            data = strip_identity_fields(dict(form))
            text = data.get("textContent") or data.get("text") or ""
            file = form.get("file")
            file_path = file_url = None
            if file and hasattr(file, "read"):
                import uuid as _uuid

                from app.config import settings

                settings.upload_dir.mkdir(parents=True, exist_ok=True)
                ext = Path(file.filename or "txt").suffix.lower() or ".txt"
                if ext in _CORRECTION_BLOCKED_EXT:
                    raise HTTPException(status_code=400, detail={"error": "INVALID_FILE"})
                if ext not in _CORRECTION_ALLOWED_EXT:
                    raise HTTPException(status_code=400, detail={"error": "INVALID_FILE_TYPE"})
                raw = await file.read()
                if len(raw) > _CORRECTION_MAX_SIZE:
                    raise HTTPException(status_code=413, detail={"error": "FILE_TOO_LARGE"})
                name = f"{_uuid.uuid4()}{ext}"
                dest = settings.upload_dir / name
                dest.write_bytes(raw)
                file_path = name
                file_url = f"/uploads/{name}"
                if not text and ext in (".txt", ".md"):
                    text = dest.read_text(encoding="utf-8", errors="ignore")
            data["textContent"] = text
            sub = ai_correction_service.submit_work(user, data, file_path, file_url)
        else:
            body = strip_identity_fields(await request.json())
            sub = ai_correction_service.submit_work(user, body)
        audit_service.log_audit(request, "submit_work", "correction", resource_id=sub["id"])
        return {"submission": sub}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/corrections/reference")
async def upload_correction_reference(
    request: Request, user: dict = Depends(require_roles("professeur", "universite"))
):
    try:
        ct = request.headers.get("content-type", "")
        if "multipart/form-data" in ct:
            form = await request.form()
            data = strip_identity_fields(dict(form))
            text = data.get("referenceText") or data.get("textContent") or ""
            file = form.get("file")
            file_path = file_url = file_name = None
            if file and hasattr(file, "read"):
                import uuid as _uuid

                from app.config import settings

                settings.upload_dir.mkdir(parents=True, exist_ok=True)
                ext = Path(file.filename or "txt").suffix.lower() or ".txt"
                if ext in _CORRECTION_BLOCKED_EXT:
                    raise HTTPException(status_code=400, detail={"error": "INVALID_FILE"})
                if ext not in _CORRECTION_ALLOWED_EXT:
                    raise HTTPException(status_code=400, detail={"error": "INVALID_FILE_TYPE"})
                raw = await file.read()
                if len(raw) > _CORRECTION_MAX_SIZE:
                    raise HTTPException(status_code=413, detail={"error": "FILE_TOO_LARGE"})
                file_name = file.filename or f"reference{_uuid.uuid4()}{ext}"
                name = f"{_uuid.uuid4()}{ext}"
                dest = settings.upload_dir / name
                dest.write_bytes(raw)
                file_path = name
                file_url = f"/uploads/{name}"
                if not text and ext in (".txt", ".md"):
                    text = dest.read_text(encoding="utf-8", errors="ignore")
            data["referenceText"] = text
            ref = ai_correction_service.save_reference(
                user, data, file_path, file_url, file_name
            )
        else:
            body = strip_identity_fields(await request.json())
            ref = ai_correction_service.save_reference(user, body)
        audit_service.log_audit(request, "upload_reference", "correction", resource_id=ref["id"])
        return {"reference": ref}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/corrections/references")
def list_correction_references(user: dict = Depends(require_roles("professeur", "universite"))):
    return {
        "references": ai_correction_service.list_references(
            user["email"], user["universite"]
        )
    }


@router.delete("/corrections/reference/{reference_id}")
def delete_correction_reference(
    reference_id: str,
    request: Request,
    user: dict = Depends(require_roles("professeur", "universite")),
):
    try:
        ai_correction_service.delete_reference(user, reference_id)
        audit_service.log_audit(
            request, "delete_reference", "correction", resource_id=reference_id
        )
        return {"ok": True}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/corrections/me")
def my_corrections(user: dict = Depends(require_roles("etudiant"))):
    return {"submissions": ai_correction_service.list_for_student(user["email"])}


@router.get("/corrections/pending")
def pending_corrections(user: dict = Depends(require_roles("professeur", "universite", "assistant"))):
    if user.get("role") == "assistant":
        subs = ai_correction_service.list_pending_for_campus(user["universite"])
    else:
        subs = ai_correction_service.list_pending_for_professor(
            user["email"], user["universite"]
        )
    return {"submissions": subs}


@router.get("/corrections/inbox")
def correction_inbox(user: dict = Depends(require_roles("professeur", "universite"))):
    return {
        "submissions": ai_correction_service.list_for_professor(
            user["email"], user["universite"]
        )
    }


@router.get("/corrections/stats/class")
def class_correction_stats(
    classe: str | None = None,
    user: dict = Depends(require_roles("professeur", "universite", "assistant")),
):
    return ai_correction_service.get_class_stats(user["universite"], classe)


@router.get("/corrections/stats/course/{course_code}")
def course_correction_sheet(
    course_code: str,
    semester: str = "s1-2025",
    user: dict = Depends(require_roles("professeur", "universite", "assistant")),
):
    return ai_correction_service.get_course_sheet(
        user["universite"], course_code, semester
    )


@router.get("/corrections/notifications")
def correction_notifications(user: dict = Depends(get_current_user)):
    return {"notifications": ai_correction_service.get_notifications(user["email"])}


@router.post("/corrections/{submission_id}/validate")
def validate_correction(
    submission_id: str,
    body: dict,
    request: Request,
    user: dict = Depends(require_roles("professeur", "universite", "assistant")),
):
    try:
        safe_body = pick_fields(body, "action", "finalGrade", "comment")
        sub = ai_correction_service.validate_submission(user, submission_id, safe_body)
        audit_service.log_audit(
            request, "validate_correction", "correction", resource_id=submission_id
        )
        return {"submission": sub}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/corrections/{submission_id}")
def get_correction(submission_id: str, user: dict = Depends(get_current_user)):
    sub = ai_correction_service.get_submission(submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
    try:
        assert_submission_access(user, sub)
    except ValueError as e:
        _handle_platform_error(e)
    return {"submission": sub}


@router.get("/orientation/status")
def orientation_status_route(user: dict = Depends(get_current_user)):
    return orientation_service.status()


@router.post("/orientation")
def orientation(body: dict, request: Request, user: dict = Depends(require_roles("etudiant"))):
    try:
        advice = orientation_service.advise(user, body.get("interests") or "")
    except ValueError as e:
        _handle_platform_error(e)
    audit_service.log_audit(
        request,
        "orientation_ia",
        "orientation",
        meta={"domain": advice.get("domain"), "source": advice.get("source")},
    )
    return {"ok": True, "advice": advice}


@router.get("/students/teaching")
def professor_students(user: dict = Depends(require_roles("professeur"))):
    try:
        students = list_students_for_professor(user)
        return {
            "students": [
                {
                    "email": s["email"],
                    "prenom": s.get("prenom"),
                    "nom": s.get("nom"),
                    "matricule": s.get("matricule"),
                    "niveau": s.get("niveau"),
                    "classe": s.get("classe"),
                    "filiere": s.get("filiere"),
                    "universite": s.get("universite"),
                    "sectionId": s.get("sectionId"),
                }
                for s in students
            ]
        }
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/presence/ping")
def ping_presence(body: dict, user: dict = Depends(get_current_user)):
    try:
        payload = pick_fields(body or {}, "classe", "filiere", "sectionId", "universite")
        return platform_service.upsert_presence(user, payload)
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/presence/section")
def section_presence(user: dict = Depends(require_roles("section", "assistant", "universite"))):
    try:
        return platform_service.section_presence_summary(user)
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/presence/classes")
def professor_presence(user: dict = Depends(require_roles("professeur"))):
    try:
        return platform_service.professor_presence_by_class(user)
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/campus-branding")
def campus_branding_route(universite: str = Query(..., min_length=1, max_length=100)):
    branding = get_campus_branding(universite)
    if not branding:
        return {"universite": universite, "logoUrl": None, "nomUniversite": None}
    return branding


@router.get("/campus-sections")
def campus_sections_public_route(universite: str = Query(..., min_length=1, max_length=100)):
    sections = reclamation_service.list_campus_sections_public(universite)
    return {"sections": sections}


@router.get("/home-news")
def list_home_news_public():
    return {"items": home_news_service.list_public_home_news()}


@router.post("/home-news/{item_id}/view")
@limiter.limit("120/minute")
def record_home_news_view_route(item_id: str, body: dict, request: Request):
    viewer_key = str(body.get("viewerKey") or body.get("viewer_key") or "").strip()
    if not viewer_key:
        raise HTTPException(status_code=400, detail={"error": "INVALID_INPUT"})
    try:
        return home_news_service.record_home_news_view(item_id, viewer_key)
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/home-news/manage")
def list_home_news_manage(user: dict = Depends(require_roles("ministere", "universite"))):
    try:
        return {"items": home_news_service.list_manage_home_news(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/home-news", status_code=201)
def create_home_news_route(body: dict, user: dict = Depends(require_roles("ministere", "universite"))):
    try:
        item = home_news_service.create_home_news(user, body)
        return {"ok": True, "item": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.patch("/home-news/{item_id}")
def update_home_news_route(
    item_id: str, body: dict, user: dict = Depends(require_roles("ministere", "universite"))
):
    try:
        item = home_news_service.update_home_news(user, item_id, body)
        return {"ok": True, "item": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.delete("/home-news/{item_id}")
def delete_home_news_route(
    item_id: str, user: dict = Depends(require_roles("ministere", "universite"))
):
    try:
        return home_news_service.delete_home_news(user, item_id)
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/library")
def list_library_public():
    return {"items": library_service.list_public_books()}


@router.get("/dictionary/languages")
def dictionary_languages_route():
    return {"languages": dictionary_service.list_languages()}


@router.get("/dictionary/lookup")
def dictionary_lookup_route(
    q: str = Query(..., min_length=1, max_length=80),
    lang: str = Query("fr", max_length=8),
):
    try:
        return dictionary_service.lookup(q, lang=lang)
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/dictionary/translate")
def translate_dictionary_route(
    q: str = Query(..., min_length=1, max_length=80),
    source: str = Query("fr", max_length=8),
    target: str = Query("auto", max_length=8),
):
    """Rétrocompatibilité — redirige vers la recherche de définition."""
    del target
    try:
        return dictionary_service.lookup(q, lang=source)
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/library/manage")
def list_library_manage(user: dict = Depends(require_roles("ministere", "superadmin"))):
    try:
        return {"items": library_service.list_manage_books(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/library", status_code=201)
def create_library_route(body: dict, user: dict = Depends(require_roles("ministere", "superadmin"))):
    try:
        item = library_service.create_book(user, body)
        return {"ok": True, "item": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.patch("/library/{item_id}")
def update_library_route(
    item_id: str, body: dict, user: dict = Depends(require_roles("ministere", "superadmin"))
):
    try:
        item = library_service.update_book(user, item_id, body)
        return {"ok": True, "item": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.delete("/library/{item_id}")
def delete_library_route(item_id: str, user: dict = Depends(require_roles("ministere", "superadmin"))):
    try:
        return library_service.delete_book(user, item_id)
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/library/upload")
async def upload_library_file_route(
    files: list[UploadFile] = File(...),
    user: dict = Depends(require_roles("ministere", "superadmin")),
):
    del user
    saved = await _save_home_news_uploads(files)
    if not saved:
        raise HTTPException(status_code=400, detail={"error": "INVALID_INPUT"})
    primary = saved[0]
    return {
        "ok": True,
        "fileUrl": primary["mediaUrl"],
        "fileName": primary["name"],
        "mediaType": primary["mediaType"],
    }


@router.get("/diplomas/manage")
def list_diplomas_manage(user: dict = Depends(require_roles("universite"))):
    try:
        return {"diplomas": diploma_service.list_campus_diplomas(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/diplomas/me")
def list_my_diplomas(user: dict = Depends(require_roles("etudiant"))):
    try:
        return {"diplomas": diploma_service.list_student_diplomas(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/diplomas/issue", status_code=201)
def issue_diploma_route(body: dict, user: dict = Depends(require_roles("universite"))):
    try:
        item = diploma_service.issue_diploma(user, body)
        return {"ok": True, "diploma": item}
    except ValueError as e:
        _handle_diploma_error(e)


@router.patch("/diplomas/{diploma_id}")
def revoke_diploma_route(
    diploma_id: str, user: dict = Depends(require_roles("universite"))
):
    try:
        item = diploma_service.revoke_diploma(user, diploma_id)
        return {"ok": True, "diploma": item}
    except ValueError as e:
        _handle_diploma_error(e)


@router.post("/diplomas/verify")
@limiter.limit("30/15minutes")
def verify_diploma_route(request: Request, body: dict):
    code = body.get("verificationCode") or body.get("code") or ""
    number = body.get("diplomaNumber") or body.get("number") or ""
    return diploma_service.verify_diploma_public(code, number)


def _handle_diploma_error(exc: ValueError) -> None:
    code = str(exc)
    if code == "STUDENT_NOT_FOUND":
        raise HTTPException(
            status_code=404,
            detail={"error": code, "message": "Étudiant introuvable sur votre campus."},
        )
    _handle_platform_error(exc)


@router.get("/courses")
def list_courses_public(
    universite: str | None = Query(None),
):
    code = clean_text_universite(universite) if universite else None
    return {"items": course_service.list_public(code)}


@router.get("/courses/for-student")
def list_courses_for_student(user: dict = Depends(require_roles("etudiant"))):
    try:
        return {"courses": course_service.list_for_student(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/courses/enrollments/me")
def list_my_course_enrollments(user: dict = Depends(require_roles("etudiant"))):
    try:
        return {"enrollments": course_service.list_my_enrollments(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/courses/manage")
def list_courses_manage(
    user: dict = Depends(require_roles("universite", "professeur")),
):
    try:
        return {"items": course_service.list_manage(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/courses", status_code=201)
def create_course_route(
    body: dict,
    user: dict = Depends(require_roles("universite", "professeur")),
):
    try:
        item = course_service.create_course(user, body)
        return {"ok": True, "item": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.patch("/courses/{course_id}")
def update_course_route(
    course_id: str,
    body: dict,
    user: dict = Depends(require_roles("universite", "professeur")),
):
    try:
        item = course_service.update_course(user, course_id, body)
        return {"ok": True, "item": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.delete("/courses/{course_id}")
def delete_course_route(
    course_id: str,
    user: dict = Depends(require_roles("universite", "professeur")),
):
    try:
        return course_service.delete_course(user, course_id)
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/courses/{course_id}/enroll", status_code=201)
def enroll_course_route(
    course_id: str,
    user: dict = Depends(require_roles("etudiant")),
):
    try:
        enrollment = course_service.enroll(user, course_id)
        return {"ok": True, "enrollment": enrollment}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/careers")
def list_careers_public(
    scope: str | None = Query(None),
    universite: str | None = Query(None),
):
    from app.utils.sanitize import clean_text

    sc = clean_text(scope, 20) if scope else None
    uni = clean_text(universite, 80) if universite else None
    return {"items": career_service.list_public(scope=sc, universite=uni)}


@router.get("/careers/for-student")
def list_careers_for_student(user: dict = Depends(require_roles("etudiant"))):
    try:
        return {"items": career_service.list_for_student(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/careers/applications/me")
def list_my_career_applications(user: dict = Depends(require_roles("etudiant"))):
    try:
        return {"applications": career_service.list_my_applications(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/careers/manage")
def list_careers_manage(
    user: dict = Depends(require_roles("universite", "ministere")),
):
    try:
        return {"items": career_service.list_manage(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/careers/{offer_id}/applications")
def list_career_applications_route(
    offer_id: str,
    user: dict = Depends(require_roles("universite", "ministere")),
):
    try:
        return {"applications": career_service.list_applications_for_offer(user, offer_id)}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/careers", status_code=201)
def create_career_route(
    body: dict,
    user: dict = Depends(require_roles("universite", "ministere")),
):
    try:
        item = career_service.create_offer(user, body)
        return {"ok": True, "item": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.patch("/careers/{offer_id}")
def update_career_route(
    offer_id: str,
    body: dict,
    user: dict = Depends(require_roles("universite", "ministere")),
):
    try:
        item = career_service.update_offer(user, offer_id, body)
        return {"ok": True, "item": item}
    except ValueError as e:
        _handle_platform_error(e)


@router.delete("/careers/{offer_id}")
def delete_career_route(
    offer_id: str,
    user: dict = Depends(require_roles("universite", "ministere")),
):
    try:
        return career_service.delete_offer(user, offer_id)
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/careers/{offer_id}/apply", status_code=201)
def apply_career_route(
    offer_id: str,
    body: dict,
    user: dict = Depends(require_roles("etudiant")),
):
    try:
        app = career_service.apply(user, offer_id, body.get("message") or "")
        return {"ok": True, "application": app}
    except ValueError as e:
        _handle_platform_error(e)


@router.patch("/careers/applications/{app_id}")
def update_career_application_route(
    app_id: str,
    body: dict,
    user: dict = Depends(require_roles("universite", "ministere")),
):
    try:
        app = career_service.update_application_status(user, app_id, body.get("status") or "")
        return {"ok": True, "application": app}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/social")
def list_social_posts(
    user: dict = Depends(get_current_user),
    q: str | None = Query(None),
    hashtag: str | None = Query(None),
    group: str | None = Query(None),
    feed: str | None = Query(None),
):
    try:
        posts = social_service.list_posts(
            user,
            {"q": q, "hashtag": hashtag, "group": group, "feed": feed},
        )
        return {"posts": posts}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/social/events")
def list_social_events(user: dict = Depends(get_current_user)):
    try:
        return {"events": social_service.list_events(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/social/hashtags")
def list_social_hashtags(user: dict = Depends(get_current_user)):
    try:
        return {"hashtags": social_service.trending_hashtags(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/social/study-groups")
def list_social_study_groups(user: dict = Depends(get_current_user)):
    try:
        return {"groups": social_service.list_study_groups(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/social/study-groups", status_code=201)
def create_social_study_group(
    body: dict,
    user: dict = Depends(
        require_roles("etudiant", "professeur", "assistant", "universite", "section", "ministere")
    ),
):
    try:
        group = social_service.create_study_group(user, body)
        return {"ok": True, "group": group}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/social/study-groups/{group_id}/join")
def join_social_study_group(
    group_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        group = social_service.join_study_group(user, group_id)
        return {"ok": True, "group": group}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/social/settings")
def get_social_settings(user: dict = Depends(get_current_user)):
    try:
        return social_service.get_settings(user)
    except ValueError as e:
        _handle_platform_error(e)


@router.patch("/social/settings")
def patch_social_settings(
    body: dict,
    user: dict = Depends(require_roles("universite", "section", "ministere")),
):
    try:
        return social_service.update_settings(user, body)
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/social/notifications")
def list_social_notifications(user: dict = Depends(get_current_user)):
    try:
        return {"notifications": social_service.list_notifications(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.patch("/social/notifications/{notif_id}/read")
def read_social_notification(
    notif_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        return social_service.mark_notification_read(user, notif_id)
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/social/messages/conversations")
def list_social_conversations(user: dict = Depends(require_roles("etudiant"))):
    try:
        return {"conversations": social_service.list_conversations(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/social/messages/with/{peer_email}")
def list_social_messages(
    peer_email: str,
    user: dict = Depends(require_roles("etudiant")),
):
    try:
        return {"messages": social_service.list_messages(user, peer_email)}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/social/messages", status_code=201)
def send_social_message(
    body: dict,
    user: dict = Depends(require_roles("etudiant")),
):
    try:
        msg = social_service.send_message(user, body)
        return {"ok": True, "message": msg}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/social/upload")
async def upload_social_media(
    file: UploadFile = File(...),
    kind: str = Query("photo"),
    user: dict = Depends(
        require_roles(
            "etudiant", "professeur", "assistant", "universite", "section", "ministere"
        )
    ),
):
    try:
        content = await file.read()
        return social_service.save_upload(user, file.filename or "file", content, kind)
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/social", status_code=201)
def create_social_post(
    body: dict,
    user: dict = Depends(
        require_roles("etudiant", "professeur", "assistant", "universite", "section", "ministere")
    ),
):
    try:
        post = social_service.create_post(user, body)
        return {"ok": True, "post": post}
    except ValueError as e:
        _handle_platform_error(e)
    except Exception as exc:
        import logging

        logging.exception("create_social_post failed")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "SERVER_ERROR",
                "message": "Publication impossible — base de données réseau social à migrer.",
            },
        ) from exc


@router.post("/social/{post_id}/like")
def toggle_social_like(
    post_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        post = social_service.toggle_like(user, post_id)
        return {"ok": True, "post": post}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/social/{post_id}/reaction")
def toggle_social_reaction(
    post_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
):
    try:
        post = social_service.toggle_reaction(user, post_id, body.get("reaction") or "like")
        return {"ok": True, "post": post}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/social/{post_id}/comments")
def list_social_comments(
    post_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        return {"comments": social_service.list_comments(user, post_id)}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/social/{post_id}/comments", status_code=201)
def add_social_comment(
    post_id: str,
    body: dict,
    user: dict = Depends(get_current_user),
):
    try:
        comment = social_service.add_comment(user, post_id, body.get("content") or "")
        return {"ok": True, "comment": comment}
    except ValueError as e:
        _handle_platform_error(e)


@router.delete("/social/{post_id}")
def delete_social_post(
    post_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        return social_service.delete_post(user, post_id)
    except ValueError as e:
        _handle_platform_error(e)


@router.patch("/social/{post_id}")
def moderate_social_post(
    post_id: str,
    body: dict,
    user: dict = Depends(require_roles("universite", "section", "ministere")),
):
    try:
        if "pinned" in body:
            post = social_service.set_pinned(user, post_id, bool(body.get("pinned")))
            return {"ok": True, "post": post}
        post = social_service.set_hidden(user, post_id, bool(body.get("hidden")))
        return {"ok": True, "post": post}
    except ValueError as e:
        _handle_platform_error(e)


@router.get("/live/sessions")
def list_live_sessions(user: dict = Depends(get_current_user)):
    try:
        return {"sessions": live_service.list_sessions(user)}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/live/sessions", status_code=201)
def create_live_session(
    body: dict,
    user: dict = Depends(require_roles("professeur", "assistant", "section", "universite", "ministere")),
):
    try:
        session = live_service.create_session(user, body)
        return {"ok": True, "session": session}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/live/sessions/{session_id}/start")
def start_live_session(
    session_id: str,
    user: dict = Depends(require_roles("professeur", "assistant", "section", "universite", "ministere")),
):
    try:
        session = live_service.start_session(user, session_id)
        return {"ok": True, "session": session}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/live/sessions/{session_id}/end")
def end_live_session(
    session_id: str,
    body: dict,
    user: dict = Depends(require_roles("professeur", "assistant", "section", "universite", "ministere")),
):
    try:
        session = live_service.end_session(user, session_id, body)
        return {"ok": True, "session": session}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/live/sessions/{session_id}/join")
def join_live_session(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        session = live_service.join_session(user, session_id)
        return {"ok": True, "session": session}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/live/sessions/{session_id}/recording")
async def upload_live_recording(
    session_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(require_roles("professeur", "assistant", "section", "universite", "ministere")),
):
    try:
        content = await file.read()
        result = live_service.save_recording(
            user,
            session_id,
            file.filename or "recording.webm",
            content,
        )
        return result
    except ValueError as e:
        _handle_platform_error(e)


def clean_text_universite(val: str | None) -> str | None:
    from app.utils.sanitize import clean_text

    return clean_text(val, 80)


def _handle_platform_error(exc: ValueError) -> None:
    code = str(exc)
    if code == "AUTH_REQUIRED":
        raise HTTPException(status_code=401, detail={"error": code})
    if code in ("FORBIDDEN", "FORBIDDEN_CAMPUS", "INVALID_STATUS"):
        raise HTTPException(
            status_code=403, detail={"error": code, "message": "Accès refusé."}
        )
    if code == "NOT_FOUND":
        raise HTTPException(
            status_code=404,
            detail={"error": code, "message": "Élément introuvable ou accès refusé."},
        )
    if code == "INVALID_INPUT":
        raise HTTPException(status_code=400, detail={"error": code})
    if code == "DM_DISABLED":
        raise HTTPException(
            status_code=403,
            detail={"error": code, "message": "Messages privés désactivés sur ce campus."},
        )
    if code == "FILE_TOO_LARGE":
        raise HTTPException(
            status_code=413,
            detail={"error": code, "message": "Fichier trop volumineux."},
        )
    if code == "INVALID_LANG":
        raise HTTPException(
            status_code=400,
            detail={"error": code, "message": "Langue non prise en charge ou identique."},
        )
    raise exc
