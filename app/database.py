import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.cursors import DictCursor

from app.config import settings

_db: "SACDatabase | None" = None


def _adapt_sql(sql: str, backend: str) -> str:
    sql = re.sub(r"\s+COLLATE NOCASE", "", sql, flags=re.IGNORECASE)
    if backend == "mysql":
        sql = sql.replace("?", "%s")
        sql = re.sub(r",(\s*)read(\s*,)", r", \1`read`\2", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\((\s*)read(\s*,)", r"(\1`read`\2", sql, flags=re.IGNORECASE)
        sql = re.sub(r"(\W)read(\s*=)", r"\1`read`\2", sql, flags=re.IGNORECASE)
    return sql


def is_duplicate_key_error(exc: BaseException) -> bool:
    if hasattr(exc, "args") and exc.args:
        code = exc.args[0]
        if code in (1062, 19):  # MySQL duplicate / SQLite UNIQUE
            return True
    msg = str(exc).upper()
    return "UNIQUE" in msg or "DUPLICATE" in msg


def _json_load(val: Any, default: Any) -> Any:
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (TypeError, json.JSONDecodeError):
        return default


class SACCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class SACDatabase:
    def __init__(self, conn, backend: str):
        self._conn = conn
        self._backend = backend

    def execute(self, sql: str, params: tuple | list = ()):
        sql = _adapt_sql(sql, self._backend)
        if self._backend == "sqlite":
            cur = self._conn.execute(sql, params or ())
            return SACCursor(cur)
        cur = self._conn.cursor()
        try:
            cur.execute(sql, params or ())
        except Exception:
            cur.close()
            raise
        return SACCursor(cur)

    def commit(self) -> None:
        self._conn.commit()

    def ping(self) -> None:
        if self._backend == "mysql":
            self._conn.ping(reconnect=True)

    def executescript(self, script: str) -> None:
        if self._backend == "sqlite":
            self._conn.executescript(script)
            return
        cur = self._conn.cursor()
        for stmt in (s.strip() for s in script.split(";") if s.strip()):
            if stmt.startswith("--"):
                continue
            cur.execute(stmt)
        cur.close()


def _parse_database_url(url: str) -> dict[str, Any]:
    normalized = url.replace("mysql+pymysql://", "mysql://").replace(
        "mysql+mysqlconnector://", "mysql://"
    )
    parsed = urlparse(normalized)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "/").lstrip("/"),
    }


def _init_mysql_schema(conn) -> None:
    schema_path = Path(__file__).resolve().parent.parent / "db" / "schema-mysql.sql"
    sql = schema_path.read_text(encoding="utf-8")
    cur = conn.cursor()
    for stmt in (s.strip() for s in sql.split(";") if s.strip()):
        if stmt.startswith("--"):
            continue
        try:
            cur.execute(stmt)
        except pymysql.err.OperationalError as exc:
            # 1050 table exists, 1061 duplicate key name
            if exc.args[0] in (1050, 1061):
                continue
            raise
    conn.commit()
    cur.close()


def _connect_mysql() -> SACDatabase:
    cfg = settings.mysql_config
    conn = pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )
    _init_mysql_schema(conn)
    return SACDatabase(conn, "mysql")


def _migrate_users_section_role_sqlite(db: sqlite3.Connection) -> None:
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
          prenom TEXT, nom TEXT, telephone TEXT, universite TEXT, filiere TEXT, niveau TEXT,
          matricule TEXT, date_naissance TEXT, departement TEXT, grade TEXT, service TEXT,
          fonction TEXT, num_employe TEXT, num_assist TEXT, nom_universite TEXT, sigle TEXT,
          ville TEXT, adresse TEXT, nb_etudiants TEXT, site_web TEXT, responsable TEXT,
          code_uni TEXT, cours_classes TEXT DEFAULT '[]', payment TEXT, inscription_fee TEXT,
          campus_tariffs TEXT, failed_login_attempts INTEGER DEFAULT 0, locked_until TEXT,
          section_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO users_new SELECT
          id, email, password_hash, role, prenom, nom, telephone, universite, filiere, niveau,
          matricule, date_naissance, departement, grade, service, fonction, num_employe,
          num_assist, nom_universite, sigle, ville, adresse, nb_etudiants, site_web,
          responsable, code_uni, cours_classes, payment, inscription_fee, campus_tariffs,
          failed_login_attempts, locked_until, NULL, created_at, updated_at FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
        """
    )


def _connect_sqlite() -> SACDatabase:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    db_dir = Path(__file__).resolve().parent.parent / "db"
    conn.executescript((db_dir / "schema.sql").read_text(encoding="utf-8"))
    conn.executescript((db_dir / "schema-platform.sql").read_text(encoding="utf-8"))
    for name in ("schema-ai-correction.sql", "schema-reclamations.sql"):
        path = db_dir / name
        if path.exists():
            conn.executescript(path.read_text(encoding="utf-8"))
    for stmt in (
        "ALTER TABLE users ADD COLUMN campus_tariffs TEXT",
        "ALTER TABLE users ADD COLUMN section_id TEXT",
        "ALTER TABLE users ADD COLUMN nomination TEXT",
        "ALTER TABLE documents ADD COLUMN attachments TEXT DEFAULT '[]'",
        "ALTER TABLE documents ADD COLUMN section_id TEXT",
        "ALTER TABLE documents ADD COLUMN section_name TEXT",
        "ALTER TABLE meetings ADD COLUMN section_filiere TEXT",
        "CREATE INDEX IF NOT EXISTS idx_grades_student ON grades(student_email, universite)",
        "CREATE INDEX IF NOT EXISTS idx_grades_prof ON grades(professor_email, universite)",
        "CREATE INDEX IF NOT EXISTS idx_grades_campus ON grades(universite, semester)",
        "CREATE INDEX IF NOT EXISTS idx_docs_author ON documents(author_id, source)",
        "CREATE INDEX IF NOT EXISTS idx_docs_universite ON documents(universite, source)",
        "CREATE INDEX IF NOT EXISTS idx_docs_created ON documents(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_refresh_expires ON refresh_tokens(expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_users_universite ON users(universite, role)",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    _migrate_users_section_role_sqlite(conn)
    conn.commit()
    return SACDatabase(conn, "sqlite")


def get_db() -> SACDatabase:
    global _db
    if _db is None:
        if settings.use_mysql:
            _db = _connect_mysql()
        else:
            _db = _connect_sqlite()
    else:
        _db.ping()
    return _db


def row_to_user(row: Any | None) -> dict[str, Any] | None:
    if not row:
        return None
    keys = row.keys() if hasattr(row, "keys") else []
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
        "coursClasses": _json_load(row["cours_classes"], []),
        "payment": _json_load(row["payment"], None),
        "inscriptionFee": _json_load(row["inscription_fee"], None),
        "campusTariffs": _json_load(row["campus_tariffs"], None),
        "sectionId": row["section_id"] if "section_id" in keys else None,
        "nomination": row["nomination"] if "nomination" in keys else None,
        "createdAt": row["created_at"],
    }


def row_to_document(row: Any | None) -> dict[str, Any] | None:
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
        "attachments": _json_load(row["attachments"], []),
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
        "reactions": _json_load(row["reactions"], {}),
        "updatedAt": row["updated_at"],
    }
