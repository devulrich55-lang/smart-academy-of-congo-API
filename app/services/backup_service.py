"""Sauvegarde complète EvoSU — base SQLite/MySQL + fichiers uploads."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.utils.platform_security import uid

BACKUP_ID_RE = re.compile(r"^evosu-backup-[a-z0-9-]+$")
CONFIRM_PREFIX = "RESTAURER"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def backup_dir() -> Path:
    raw = os.getenv("BACKUP_DIR", "").strip()
    if raw:
        path = Path(raw).resolve()
    elif settings.db_on_render_disk or settings.uploads_on_render_disk:
        path = Path("/data/backups")
    else:
        path = settings.db_path.parent / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _interval_seconds() -> int:
    hours = max(1, int(os.getenv("BACKUP_INTERVAL_HOURS", "6")))
    return hours * 3600


def _retention_seconds() -> int:
    hours = max(6, int(os.getenv("BACKUP_RETENTION_HOURS", "24")))
    return hours * 3600


def _backup_path(backup_id: str) -> Path:
    if not BACKUP_ID_RE.match(backup_id):
        raise ValueError("INVALID_BACKUP_ID")
    return backup_dir() / f"{backup_id}.zip"


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def _sqlite_backup(dest: Path) -> None:
    src = settings.db_path
    if not src.exists():
        raise FileNotFoundError(f"Base SQLite introuvable: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _mysql_dump(dest: Path) -> None:
    cfg = settings.mysql_config
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "mysqldump",
        f"--host={cfg['host']}",
        f"--port={cfg['port']}",
        f"--user={cfg['user']}",
        f"--password={cfg['password']}",
        "--single-transaction",
        "--routines",
        "--triggers",
        cfg["database"],
    ]
    with dest.open("w", encoding="utf-8") as out:
        subprocess.run(cmd, check=True, stdout=out, stderr=subprocess.PIPE, text=True)


def _build_manifest(trigger: str, backup_id: str) -> dict:
    db_file = "database.db" if not settings.use_mysql else "database.sql"
    return {
        "id": backup_id,
        "version": "1",
        "platform": settings.platform_name,
        "trigger": trigger,
        "createdAt": _now_iso(),
        "databaseBackend": settings.database_backend,
        "databaseFile": db_file,
        "uploadsIncluded": settings.upload_dir.exists(),
        "uploadsFileCount": sum(1 for _ in settings.upload_dir.rglob("*") if _.is_file())
        if settings.upload_dir.exists()
        else 0,
        "intervalHours": int(os.getenv("BACKUP_INTERVAL_HOURS", "6")),
        "retentionHours": int(os.getenv("BACKUP_RETENTION_HOURS", "24")),
    }


def create_backup(trigger: str = "manual") -> dict:
    """Crée une archive ZIP complète (DB + uploads + manifest)."""
    trigger = trigger if trigger in ("auto", "manual", "pre_restore") else "manual"
    backup_id = f"evosu-backup-{uid()[:12]}"
    archive = _backup_path(backup_id)

    with tempfile.TemporaryDirectory(prefix="evosu-bak-") as tmp:
        tmp_path = Path(tmp)
        manifest = _build_manifest(trigger, backup_id)

        if settings.use_mysql:
            sql_path = tmp_path / "database.sql"
            try:
                _mysql_dump(sql_path)
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                raise RuntimeError("MYSQL_DUMP_FAILED") from exc
        else:
            _sqlite_backup(tmp_path / "database.db")

        if settings.upload_dir.exists():
            uploads_copy = tmp_path / "uploads"
            shutil.copytree(
                settings.upload_dir,
                uploads_copy,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.tmp"),
            )

        manifest["uploadsBytes"] = _dir_size(tmp_path / "uploads") if (tmp_path / "uploads").exists() else 0
        (tmp_path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in tmp_path.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(tmp_path).as_posix())

    purge_old_backups()
    row = _backup_row(archive, manifest)
    return row


def _backup_row(path: Path, manifest: dict | None = None) -> dict:
    stat = path.stat()
    meta = manifest
    if meta is None:
        try:
            with zipfile.ZipFile(path) as zf:
                if "manifest.json" in zf.namelist():
                    meta = json.loads(zf.read("manifest.json").decode("utf-8"))
        except (zipfile.BadZipFile, json.JSONDecodeError, KeyError):
            meta = {}
    age_h = (time.time() - stat.st_mtime) / 3600
    return {
        "id": meta.get("id") or path.stem,
        "filename": path.name,
        "trigger": meta.get("trigger", "unknown"),
        "createdAt": meta.get("createdAt")
        or datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "sizeBytes": stat.st_size,
        "sizeLabel": _fmt_bytes(stat.st_size),
        "ageHours": round(age_h, 1),
        "databaseBackend": meta.get("databaseBackend", settings.database_backend),
        "uploadsIncluded": bool(meta.get("uploadsIncluded")),
        "uploadsFileCount": meta.get("uploadsFileCount", 0),
    }


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} o"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} Ko"
    return f"{n / (1024 * 1024):.1f} Mo"


def list_backups() -> list[dict]:
    rows = []
    for path in sorted(backup_dir().glob("evosu-backup-*.zip"), key=lambda p: p.stat().st_mtime):
        try:
            rows.append(_backup_row(path))
        except OSError:
            continue
    rows.reverse()
    return rows


def purge_old_backups() -> dict:
    cutoff = time.time() - _retention_seconds()
    removed = []
    for path in backup_dir().glob("evosu-backup-*.zip"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path.name)
        except OSError:
            pass
    return {"removed": removed, "count": len(removed)}


def get_status() -> dict:
    backups = list_backups()
    latest = backups[0] if backups else None
    total_bytes = sum(b["sizeBytes"] for b in backups)
    interval_h = int(os.getenv("BACKUP_INTERVAL_HOURS", "6"))
    retention_h = int(os.getenv("BACKUP_RETENTION_HOURS", "24"))
    next_in_h = None
    if latest:
        elapsed = (time.time() - Path(backup_dir() / f"{latest['id']}.zip").stat().st_mtime) / 3600
        next_in_h = max(0, round(interval_h - elapsed, 1))
    return {
        "enabled": os.getenv("BACKUP_ENABLED", "true").lower() != "false",
        "directory": str(backup_dir()),
        "intervalHours": interval_h,
        "retentionHours": retention_h,
        "maxBackupsApprox": max(1, retention_h // interval_h),
        "totalBackups": len(backups),
        "totalBytes": total_bytes,
        "totalLabel": _fmt_bytes(total_bytes),
        "latest": latest,
        "nextScheduledInHours": next_in_h,
        "databaseBackend": settings.database_backend,
    }


def run_scheduled_backup() -> dict | None:
    if os.getenv("BACKUP_ENABLED", "true").lower() == "false":
        return None
    purge_old_backups()
    backups = list_backups()
    interval_s = _interval_seconds()
    if backups:
        latest_path = backup_dir() / f"{backups[0]['id']}.zip"
        if latest_path.exists() and (time.time() - latest_path.stat().st_mtime) < interval_s:
            return {"skipped": True, "reason": "interval_not_elapsed"}
    row = create_backup(trigger="auto")
    print(f"[Backup] Sauvegarde auto créée: {row['id']} ({row['sizeLabel']})")
    return row


def restore_backup(backup_id: str, confirm: str) -> dict:
    expected = f"{CONFIRM_PREFIX}-{backup_id}"
    if (confirm or "").strip() != expected:
        raise ValueError("CONFIRM_REQUIRED")
    archive = _backup_path(backup_id)
    if not archive.exists():
        raise ValueError("NOT_FOUND")

    pre = create_backup(trigger="pre_restore")

    with tempfile.TemporaryDirectory(prefix="evosu-restore-") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp_path)

        manifest_path = tmp_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

        if settings.use_mysql:
            sql_path = tmp_path / "database.sql"
            if not sql_path.exists():
                raise ValueError("INVALID_BACKUP")
            cfg = settings.mysql_config
            cmd = [
                "mysql",
                f"--host={cfg['host']}",
                f"--port={cfg['port']}",
                f"--user={cfg['user']}",
                f"--password={cfg['password']}",
                cfg["database"],
            ]
            subprocess.run(cmd, check=True, input=sql_path.read_text(encoding="utf-8"), text=True)
        else:
            db_src = tmp_path / "database.db"
            if not db_src.exists():
                raise ValueError("INVALID_BACKUP")
            settings.db_path.parent.mkdir(parents=True, exist_ok=True)
            if settings.db_path.exists():
                shutil.copy2(settings.db_path, settings.db_path.with_suffix(".db.pre-restore"))
            _sqlite_backup_to_path(db_src, settings.db_path)

        uploads_src = tmp_path / "uploads"
        if uploads_src.exists():
            if settings.upload_dir.exists():
                shutil.rmtree(settings.upload_dir)
            shutil.copytree(uploads_src, settings.upload_dir)

        global _db
        from app import database as db_mod

        db_mod._db = None

    return {
        "ok": True,
        "restoredId": backup_id,
        "preRestoreBackupId": pre["id"],
        "manifest": manifest,
        "restoredAt": _now_iso(),
    }


def _sqlite_backup_to_path(src: Path, dest: Path) -> None:
    src_conn = sqlite3.connect(str(src))
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def get_backup_file(backup_id: str) -> Path:
    path = _backup_path(backup_id)
    if not path.exists():
        raise ValueError("NOT_FOUND")
    return path
