import json
import sqlite3
from pathlib import Path
from typing import Any

from app.config import settings

_db: sqlite3.Connection | None = None


def get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(str(settings.db_path), check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute("PRAGMA journal_mode = WAL")
        _db.execute("PRAGMA foreign_keys = ON")
        db_dir = Path(__file__).resolve().parent.parent / "db"
        _db.executescript((db_dir / "schema.sql").read_text(encoding="utf-8"))
        _db.executescript((db_dir / "schema-platform.sql").read_text(encoding="utf-8"))
        ai_schema = db_dir / "schema-ai-correction.sql"
        if ai_schema.exists():
            _db.executescript(ai_schema.read_text(encoding="utf-8"))
        rec_schema = db_dir / "schema-reclamations.sql"
        if rec_schema.exists():
            _db.executescript(rec_schema.read_text(encoding="utf-8"))
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_users_telephone ON users(telephone)",
            "ALTER TABLE users ADD COLUMN campus_tariffs TEXT",
            "ALTER TABLE users ADD COLUMN section_id TEXT",
            "ALTER TABLE users ADD COLUMN nomination TEXT",
            "ALTER TABLE documents ADD COLUMN attachments TEXT DEFAULT '[]'",
            "ALTER TABLE documents ADD COLUMN section_id TEXT",
            "ALTER TABLE documents ADD COLUMN section_name TEXT",
            "ALTER TABLE meetings ADD COLUMN section_filiere TEXT",
            "CREATE INDEX IF NOT EXISTS idx_users_section ON users(section_id)",
        ):
            try:
                _db.execute(stmt)
            except sqlite3.OperationalError:
                pass
        _migrate_users_section_role(_db)
        _db.commit()
    return _db


def _migrate_users_section_role(db: sqlite3.Connection) -> None:
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    ddl = row["sql"] if row else ""
    if "'section'" in ddl:
        return

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users_new (
          id TEXT PRIMARY KEY,
          email TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL CHECK (role IN ('etudiant','professeur','assistant','universite','section')),
          prenom TEXT,
          nom TEXT,
          telephone TEXT,
          universite TEXT,
          filiere TEXT,
          niveau TEXT,
          matricule TEXT,
          date_naissance TEXT,
          departement TEXT,
          grade TEXT,
          service TEXT,
          fonction TEXT,
          num_employe TEXT,
          num_assist TEXT,
          nom_universite TEXT,
          sigle TEXT,
          ville TEXT,
          adresse TEXT,
          nb_etudiants TEXT,
          site_web TEXT,
          responsable TEXT,
          code_uni TEXT,
          cours_classes TEXT DEFAULT '[]',
          payment TEXT,
          inscription_fee TEXT,
          campus_tariffs TEXT,
          failed_login_attempts INTEGER DEFAULT 0,
          locked_until TEXT,
          section_id TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        INSERT INTO users_new (
          id, email, password_hash, role, prenom, nom, telephone, universite,
          filiere, niveau, matricule, date_naissance, departement, grade, service,
          fonction, num_employe, num_assist, nom_universite, sigle, ville, adresse,
          nb_etudiants, site_web, responsable, code_uni, cours_classes, payment,
          inscription_fee, campus_tariffs, failed_login_attempts, locked_until,
          section_id, created_at, updated_at
        )
        SELECT
          id, email, password_hash, role, prenom, nom, telephone, universite,
          filiere, niveau, matricule, date_naissance, departement, grade, service,
          fonction, num_employe, num_assist, nom_universite, sigle, ville, adresse,
          nb_etudiants, site_web, responsable, code_uni, cours_classes, payment,
          inscription_fee, campus_tariffs, failed_login_attempts, locked_until,
          NULL, created_at, updated_at
        FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
        CREATE INDEX IF NOT EXISTS idx_users_matricule ON users(matricule);
        CREATE INDEX IF NOT EXISTS idx_users_telephone ON users(telephone);
        CREATE INDEX IF NOT EXISTS idx_users_section ON users(section_id);
        """
    )


def row_to_user(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "role": row["role"],
        "prenom": row["prenom"],
        "nom": row["nom"],
        "telephone": row["telephone"],
        "universite": row["universite"],
        "filiere": row["filiere"],
        "niveau": row["niveau"],
        "matricule": row["matricule"],
        "dateNaissance": row["date_naissance"],
        "departement": row["departement"],
        "grade": row["grade"],
        "service": row["service"],
        "fonction": row["fonction"],
        "numEmploye": row["num_employe"],
        "numAssist": row["num_assist"],
        "nomUniversite": row["nom_universite"],
        "sigle": row["sigle"],
        "ville": row["ville"],
        "adresse": row["adresse"],
        "nbEtudiants": row["nb_etudiants"],
        "siteWeb": row["site_web"],
        "responsable": row["responsable"],
        "codeUni": row["code_uni"],
        "coursClasses": json.loads(row["cours_classes"] or "[]"),
        "payment": json.loads(row["payment"]) if row["payment"] else None,
        "inscriptionFee": json.loads(row["inscription_fee"])
        if row["inscription_fee"]
        else None,
        "campusTariffs": json.loads(row["campus_tariffs"])
        if row["campus_tariffs"]
        else None,
        "sectionId": row["section_id"] if "section_id" in row.keys() else None,
        "nomination": row["nomination"] if "nomination" in row.keys() else None,
        "createdAt": row["created_at"],
    }


def row_to_document(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    media_path = row["media_path"]
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "source": row["source"],
        "author": row["author"],
        "authorId": row["author_id"],
        "date": row["date"],
        "mediaCategory": row["media_category"],
        "type": row["type"],
        "size": row["size"],
        "mediaUrl": row["media_url"]
        or (f"/uploads/{media_path}" if media_path else ""),
        "attachments": json.loads(row["attachments"] or "[]"),
        "audienceType": row["audience_type"],
        "sectionId": row["section_id"],
        "sectionName": row["section_name"],
        "universite": row["universite"],
        "filiere": row["filiere"],
        "niveau": row["niveau"],
        "courseCode": row["course_code"],
        "courseName": row["course_name"],
        "classe": row["classe"],
        "allowReactions": bool(row["allow_reactions"]),
        "reactions": json.loads(row["reactions"] or "{}"),
        "updatedAt": row["updated_at"],
    }
