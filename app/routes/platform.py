from fastapi import APIRouter, Depends, HTTPException, Request

from pathlib import Path

from app.deps import get_current_user, require_roles
from app.services import ai_correction_service, audit_service, meeting_service, platform_service
from app.utils.guards import assert_submission_access, pick_fields, strip_identity_fields

router = APIRouter(prefix="/platform", tags=["platform"])


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


@router.get("/grades/me")
def grades_me(user: dict = Depends(get_current_user)):
    role = user.get("role")
    if role == "professeur":
        return {
            "grades": platform_service.list_grades_for_professor(
                user["email"], user["universite"]
            )
        }
    if role in ("assistant", "universite"):
        return {
            "grades": platform_service.list_grades_for_campus(user["universite"])
        }
    return {
        "grades": platform_service.list_grades_for_student(
            user["email"], user["universite"]
        )
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


@router.get("/social")
def list_social(user: dict = Depends(get_current_user)):
    return {
        "posts": platform_service.list_social_posts(
            user["universite"], user.get("filiere")
        )
    }


@router.post("/social")
def create_social(body: dict, request: Request, user: dict = Depends(get_current_user)):
    try:
        post = platform_service.create_social_post(user, body)
        audit_service.log_audit(request, "create_social", "social", resource_id=post.get("id"))
        return {"post": post}
    except ValueError as e:
        _handle_platform_error(e)


@router.post("/social/{post_id}/like")
def like_social(post_id: str, user: dict = Depends(get_current_user)):
    try:
        return platform_service.toggle_social_like(post_id, user["email"])
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


@router.post("/orientation")
def orientation(body: dict, request: Request, user: dict = Depends(require_roles("etudiant"))):
    advice = platform_service.get_orientation_advice(
        {
            "filiere": body.get("filiere") or user.get("filiere"),
            "niveau": body.get("niveau") or user.get("niveau"),
            "universite": user.get("universite"),
            "interests": body.get("interests"),
        }
    )
    audit_service.log_audit(
        request, "orientation_ia", "orientation", meta={"domain": advice.get("domain")}
    )
    return {"advice": advice}


def _handle_platform_error(exc: ValueError) -> None:
    code = str(exc)
    if code == "AUTH_REQUIRED":
        raise HTTPException(status_code=401, detail={"error": code})
    if code in ("FORBIDDEN", "FORBIDDEN_CAMPUS", "INVALID_STATUS"):
        raise HTTPException(
            status_code=403, detail={"error": code, "message": "Accès refusé."}
        )
    if code == "NOT_FOUND":
        raise HTTPException(status_code=404, detail={"error": code})
    if code == "INVALID_INPUT":
        raise HTTPException(status_code=400, detail={"error": code})
    raise exc
