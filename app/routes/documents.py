import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile

from app.config import settings
from app.deps import get_current_user, require_roles
from app.services import document_service
from app.utils.pagination import clamp_page
from app.utils.visibility import SOURCE_BY_ROLE, student_sees_document

router = APIRouter(prefix="/documents", tags=["documents"])

PUBLISH_ROLES = ("professeur", "assistant", "universite")
MAX_SIZE = 5 * 1024 * 1024
ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav", ".mp4", ".webm", ".mov", ".doc", ".docx"}
BLOCKED_EXT = {".exe", ".bat", ".cmd", ".sh", ".php", ".js", ".html", ".svg", ".zip", ".rar"}


async def _save_uploads(files: list[UploadFile]) -> list[dict]:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    attachments = []
    for f in files[:10]:
        ext = Path(f.filename or "").suffix.lower()
        if ext in BLOCKED_EXT:
            raise HTTPException(status_code=400, detail={"error": "INVALID_FILE"})
        safe_ext = ext.lstrip(".") if ext in ALLOWED_EXT else "bin"
        name = f"{uuid.uuid4()}.{safe_ext}"
        dest = settings.upload_dir / name
        content = await f.read()
        if len(content) > MAX_SIZE:
            raise HTTPException(status_code=413, detail={"error": "FILE_TOO_LARGE", "message": "Fichier max 5 Mo"})
        dest.write_bytes(content)
        attachments.append(
            {
                "name": f.filename or name,
                "mediaPath": name,
                "mediaUrl": f"/uploads/{name}",
                "size": f"{len(content) // 1024} Ko",
                "type": safe_ext.upper(),
            }
        )
    return attachments


@router.get("")
def list_documents(
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
    if user["role"] == "etudiant":
        student = {
            "universite": user.get("universite"),
            "filiere": user.get("filiere"),
            "niveau": user.get("niveau"),
            "classe": user.get("classe"),
            "sectionId": user.get("sectionId"),
            "email": user.get("email"),
        }
        docs = document_service.get_documents_for_student(
            student, limit=page_limit, offset=page_offset
        )
    elif user["role"] in PUBLISH_ROLES:
        docs = document_service.get_my_documents(
            user, limit=page_limit, offset=page_offset
        )
    else:
        docs = []
    return {
        "documents": docs,
        "pagination": {
            "limit": page_limit,
            "offset": page_offset,
            "hasMore": len(docs) == page_limit,
        },
    }


@router.get("/{doc_id}")
def get_document(doc_id: str, user: dict = Depends(get_current_user)):
    doc = document_service.get_document_by_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
    if user["role"] == "etudiant":
        student = {
            "universite": user.get("universite"),
            "filiere": user.get("filiere"),
            "niveau": user.get("niveau"),
            "classe": user.get("classe"),
            "sectionId": user.get("sectionId"),
        }
        if not student_sees_document(student, doc):
            raise HTTPException(status_code=403, detail={"error": "FORBIDDEN"})
    elif user["role"] in PUBLISH_ROLES:
        src = SOURCE_BY_ROLE.get(user["role"])
        if doc["source"] != src and user["role"] != "universite":
            if doc["authorId"] != user["id"]:
                raise HTTPException(status_code=403, detail={"error": "FORBIDDEN"})
    return {"document": doc}


@router.post("", status_code=201)
async def create_document_route(
    request: Request,
    user: dict = Depends(require_roles(*PUBLISH_ROLES)),
):
    ct = request.headers.get("content-type", "")
    data: dict = {}
    if "multipart/form-data" in ct:
        form = await request.form()
        data = {k: form.get(k) for k in form.keys() if k != "files"}
        data["allowReactions"] = str(data.get("allowReactions", "")).lower() in (
            "true",
            "1",
        )
        upload_files = form.getlist("files")
        if upload_files:
            uploaded = await _save_uploads(upload_files)
            primary = uploaded[0]
            data["mediaPath"] = primary["mediaPath"]
            data["mediaUrl"] = primary["mediaUrl"]
            data["size"] = primary["size"]
            data["attachments"] = uploaded[1:] if len(uploaded) > 1 else []
    else:
        data = await request.json()
    if not data.get("title"):
        raise HTTPException(status_code=400, detail={"error": "TITLE_REQUIRED"})
    doc = document_service.create_document(user, data)
    return {"document": doc}


@router.patch("/{doc_id}")
def update_document_route(
    doc_id: str, body: dict, user: dict = Depends(require_roles(*PUBLISH_ROLES))
):
    try:
        doc = document_service.update_document(user, doc_id, body)
        return {"document": doc}
    except ValueError as e:
        if str(e) == "FORBIDDEN":
            raise HTTPException(status_code=403, detail={"error": "FORBIDDEN"})
        raise


@router.delete("/{doc_id}")
def delete_document_route(
    doc_id: str, user: dict = Depends(require_roles(*PUBLISH_ROLES))
):
    try:
        document_service.delete_document(user, doc_id)
        return {"ok": True}
    except ValueError as e:
        if str(e) == "FORBIDDEN":
            raise HTTPException(status_code=403, detail={"error": "FORBIDDEN"})
        raise


@router.post("/{doc_id}/reactions")
def add_reaction_route(
    doc_id: str, body: dict, user: dict = Depends(require_roles("etudiant"))
):
    try:
        doc = document_service.add_reaction(doc_id, body.get("type"), user["id"])
        return {"document": doc}
    except ValueError as e:
        code = str(e)
        if code == "NOT_FOUND":
            raise HTTPException(status_code=404, detail={"error": code})
        if code in ("FORBIDDEN", "INVALID_REACTION"):
            raise HTTPException(status_code=403, detail={"error": code})
        raise
