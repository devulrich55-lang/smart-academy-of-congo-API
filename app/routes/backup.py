import subprocess

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.deps import require_roles
from app.rate_limit import limiter
from app.services import audit_service, backup_service

router = APIRouter(prefix="/admin/backups", tags=["backups"])

ERROR_MAP = {
    "NOT_FOUND": (404, "Sauvegarde introuvable"),
    "INVALID_BACKUP_ID": (400, "Identifiant de sauvegarde invalide"),
    "INVALID_BACKUP": (400, "Archive de sauvegarde corrompue ou incomplète"),
    "CONFIRM_REQUIRED": (400, "Confirmation requise — saisissez RESTAURER-{id}"),
    "MYSQL_DUMP_FAILED": (503, "Export MySQL indisponible (mysqldump requis)"),
    "SQLITE_BACKUP_LOCKED": (503, "Base occupée — réessayez dans quelques secondes"),
}


def _map_error(exc: ValueError) -> None:
    code = str(exc)
    if code in ERROR_MAP:
        status, message = ERROR_MAP[code]
        raise HTTPException(status_code=status, detail={"error": code, "message": message})
    raise exc


@router.get("/status")
def backup_status(user: dict = Depends(require_roles("superadmin"))):
    del user
    return backup_service.get_status()


@router.get("")
def backup_list(user: dict = Depends(require_roles("superadmin"))):
    del user
    status = backup_service.get_status()
    return {"backups": backup_service.list_backups(), "status": status}


@router.post("", status_code=202)
@router.post("/create", status_code=202)
@limiter.limit("20/hour")
def backup_create(request: Request, user: dict = Depends(require_roles("superadmin"))):
    del user
    try:
        job_id = backup_service.start_backup_job(trigger="manual")
        audit_service.log_audit(
            request,
            "create_backup",
            "backup",
            resource_id=job_id,
            meta={"trigger": "manual", "async": True},
        )
        return {"ok": True, "jobId": job_id, "status": "running"}
    except ValueError as e:
        _map_error(e)


@router.get("/jobs/{job_id}")
def backup_job_status(job_id: str, user: dict = Depends(require_roles("superadmin"))):
    del user
    return {"ok": True, "job": backup_service.get_backup_job(job_id)}


@router.post("/purge")
@limiter.limit("20/hour")
def backup_purge(request: Request, user: dict = Depends(require_roles("superadmin"))):
    result = backup_service.purge_old_backups()
    audit_service.log_audit(request, "purge_backups", "backup", meta=result)
    return {"ok": True, **result, "status": backup_service.get_status()}


@router.get("/{backup_id}/download")
def backup_download(backup_id: str, user: dict = Depends(require_roles("superadmin"))):
    del user
    try:
        path = backup_service.get_backup_file(backup_id)
        return FileResponse(
            path,
            media_type="application/zip",
            filename=path.name,
        )
    except ValueError as e:
        _map_error(e)


@router.post("/{backup_id}/restore")
@limiter.limit("3/hour")
def backup_restore(
    backup_id: str,
    body: dict,
    request: Request,
    user: dict = Depends(require_roles("superadmin")),
):
    confirm = str(body.get("confirm") or body.get("confirmToken") or "").strip()
    try:
        result = backup_service.restore_backup(backup_id, confirm)
        audit_service.log_audit(
            request,
            "restore_backup",
            "backup",
            resource_id=backup_id,
            meta={
                "preRestoreBackupId": result.get("preRestoreBackupId"),
                "by": user.get("email"),
            },
        )
        return result
    except ValueError as e:
        _map_error(e)
    except subprocess.CalledProcessError:
        raise HTTPException(
            status_code=503,
            detail={"error": "RESTORE_FAILED", "message": "Échec restauration MySQL"},
        )
