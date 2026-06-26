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


def _migrate_reset_code_column(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                "ALTER TABLE password_reset_tokens "
                "ADD COLUMN code_hash VARCHAR(255) NULL"
            )
            conn.commit()
        except pymysql.err.OperationalError as exc:
            if exc.args[0] != 1060:  # duplicate column
                raise
        try:
            cur.execute(
                "CREATE INDEX idx_reset_code ON password_reset_tokens(code_hash)"
            )
            conn.commit()
        except pymysql.err.OperationalError as exc:
            if exc.args[0] not in (1061, 1060):
                raise
        cur.close()
        return

    try:
        conn.execute("ALTER TABLE password_reset_tokens ADD COLUMN code_hash TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reset_code "
            "ON password_reset_tokens(code_hash)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_home_news_table(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS home_news (
                  id VARCHAR(36) PRIMARY KEY,
                  scope VARCHAR(20) NOT NULL,
                  author_role VARCHAR(20) NOT NULL,
                  universite VARCHAR(80) NOT NULL,
                  university_name VARCHAR(300) NOT NULL,
                  author_id VARCHAR(255) NOT NULL,
                  author_name VARCHAR(200) NOT NULL,
                  category VARCHAR(40) NOT NULL,
                  title VARCHAR(200) NOT NULL,
                  excerpt VARCHAR(400) NOT NULL,
                  body TEXT NULL,
                  link_url VARCHAR(500) NULL,
                  link_label VARCHAR(120) NULL,
                  published TINYINT(1) DEFAULT 1,
                  pinned TINYINT(1) DEFAULT 0,
                  valid_until VARCHAR(20) NULL,
                  media_url VARCHAR(500) NULL,
                  media_type VARCHAR(20) NULL,
                  media_name VARCHAR(255) NULL,
                  attachments TEXT NULL,
                  created_at VARCHAR(40) NOT NULL,
                  updated_at VARCHAR(40) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        for idx_sql in (
            "CREATE INDEX idx_home_news_scope ON home_news(scope, published, created_at)",
            "CREATE INDEX idx_home_news_uni ON home_news(universite, published, created_at)",
            "CREATE INDEX idx_home_news_author ON home_news(author_role, author_id)",
        ):
            try:
                cur.execute(idx_sql)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                if exc.args[0] not in (1061, 1060):
                    raise
        cur.close()
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS home_news (
              id TEXT PRIMARY KEY,
              scope TEXT NOT NULL CHECK (scope IN ('national','university')),
              author_role TEXT NOT NULL CHECK (author_role IN ('ministere','universite')),
              universite TEXT NOT NULL,
              university_name TEXT NOT NULL,
              author_id TEXT NOT NULL,
              author_name TEXT NOT NULL,
              category TEXT NOT NULL,
              title TEXT NOT NULL,
              excerpt TEXT NOT NULL,
              body TEXT DEFAULT '',
              link_url TEXT DEFAULT '',
              link_label TEXT DEFAULT 'En savoir plus',
              published INTEGER DEFAULT 1,
              pinned INTEGER DEFAULT 0,
              valid_until TEXT,
              media_url TEXT DEFAULT '',
              media_type TEXT DEFAULT '',
              media_name TEXT DEFAULT '',
              attachments TEXT DEFAULT '[]',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_home_news_scope ON home_news(scope, published, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_home_news_uni ON home_news(universite, published, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_home_news_author ON home_news(author_role, author_id)",
    ):
        try:
            conn.execute(idx_sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass


def _migrate_home_news_media_columns(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        for col_sql in (
            "ALTER TABLE home_news ADD COLUMN media_url VARCHAR(500) NULL",
            "ALTER TABLE home_news ADD COLUMN media_type VARCHAR(20) NULL",
            "ALTER TABLE home_news ADD COLUMN media_name VARCHAR(255) NULL",
            "ALTER TABLE home_news ADD COLUMN attachments TEXT NULL",
        ):
            try:
                cur.execute(col_sql)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                if exc.args[0] != 1060:
                    raise
        cur.close()
        return

    for col_sql in (
        "ALTER TABLE home_news ADD COLUMN media_url TEXT DEFAULT ''",
        "ALTER TABLE home_news ADD COLUMN media_type TEXT DEFAULT ''",
        "ALTER TABLE home_news ADD COLUMN media_name TEXT DEFAULT ''",
        "ALTER TABLE home_news ADD COLUMN attachments TEXT DEFAULT '[]'",
    ):
        try:
            conn.execute(col_sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass


def _migrate_home_news_views(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                "ALTER TABLE home_news ADD COLUMN view_count INT NOT NULL DEFAULT 0"
            )
            conn.commit()
        except pymysql.err.OperationalError as exc:
            if exc.args[0] != 1060:
                raise
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS home_news_views (
                  news_id VARCHAR(36) NOT NULL,
                  viewer_key VARCHAR(120) NOT NULL,
                  viewed_at VARCHAR(40) NOT NULL,
                  PRIMARY KEY (news_id, viewer_key),
                  INDEX idx_home_news_views_news (news_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        cur.close()
        return

    try:
        conn.execute(
            "ALTER TABLE home_news ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS home_news_views (
              news_id TEXT NOT NULL,
              viewer_key TEXT NOT NULL,
              viewed_at TEXT NOT NULL,
              PRIMARY KEY (news_id, viewer_key)
            )
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_home_news_views_news ON home_news_views(news_id)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_document_views(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                "ALTER TABLE documents ADD COLUMN view_count INT NOT NULL DEFAULT 0"
            )
            conn.commit()
        except pymysql.err.OperationalError as exc:
            if exc.args[0] != 1060:
                raise
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS document_views (
                  document_id VARCHAR(36) NOT NULL,
                  viewer_key VARCHAR(120) NOT NULL,
                  viewed_at VARCHAR(40) NOT NULL,
                  PRIMARY KEY (document_id, viewer_key),
                  INDEX idx_document_views_doc (document_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        cur.close()
        return

    try:
        conn.execute(
            "ALTER TABLE documents ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_views (
              document_id TEXT NOT NULL,
              viewer_key TEXT NOT NULL,
              viewed_at TEXT NOT NULL,
              PRIMARY KEY (document_id, viewer_key)
            )
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_document_views_doc ON document_views(document_id)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_users_logo_url_column(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute("ALTER TABLE users ADD COLUMN logo_url MEDIUMTEXT NULL")
            conn.commit()
        except pymysql.err.OperationalError as exc:
            if exc.args[0] != 1060:
                raise
        cur.close()
        return
    try:
        conn.execute("ALTER TABLE users ADD COLUMN logo_url TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_users_classe_column(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute("ALTER TABLE users ADD COLUMN classe VARCHAR(150) NULL")
            conn.commit()
        except pymysql.err.OperationalError as exc:
            if exc.args[0] != 1060:
                raise
        cur.close()
        return
    try:
        conn.execute("ALTER TABLE users ADD COLUMN classe TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_campus_academic_fees_columns(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        for col_sql in (
            "ALTER TABLE users ADD COLUMN campus_academic_fees TEXT NULL",
            "ALTER TABLE users ADD COLUMN university_fees TEXT NULL",
            "ALTER TABLE users ADD COLUMN campus_partner_bank TEXT NULL",
        ):
            try:
                cur.execute(col_sql)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                if exc.args[0] != 1060:
                    raise
        cur.close()
        return
    for col_sql in (
        "ALTER TABLE users ADD COLUMN campus_academic_fees TEXT",
        "ALTER TABLE users ADD COLUMN university_fees TEXT",
        "ALTER TABLE users ADD COLUMN campus_partner_bank TEXT",
    ):
        try:
            conn.execute(col_sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass


def _migrate_academic_payments_table(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS academic_payments (
                  id VARCHAR(80) PRIMARY KEY,
                  student_id VARCHAR(36) NOT NULL,
                  student_email VARCHAR(255) NOT NULL,
                  student_nom VARCHAR(200) NULL,
                  matricule VARCHAR(50) NULL,
                  universite VARCHAR(80) NOT NULL,
                  fee_key VARCHAR(40) NOT NULL,
                  fee_label VARCHAR(200) NOT NULL,
                  amount DECIMAL(12,2) NOT NULL,
                  currency VARCHAR(10) NOT NULL DEFAULT 'USD',
                  method VARCHAR(20) NOT NULL,
                  reference VARCHAR(120) NOT NULL,
                  status VARCHAR(20) NOT NULL DEFAULT 'pending',
                  created_at VARCHAR(40) NOT NULL,
                  confirmed_at VARCHAR(40) NULL,
                  confirmed_by VARCHAR(255) NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        for idx_sql in (
            "CREATE INDEX idx_pay_student ON academic_payments(student_email, created_at)",
            "CREATE INDEX idx_pay_campus ON academic_payments(universite, status, created_at)",
        ):
            try:
                cur.execute(idx_sql)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                if exc.args[0] not in (1061, 1060):
                    raise
        cur.close()
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS academic_payments (
              id TEXT PRIMARY KEY,
              student_id TEXT NOT NULL,
              student_email TEXT NOT NULL,
              student_nom TEXT,
              matricule TEXT,
              universite TEXT NOT NULL,
              fee_key TEXT NOT NULL,
              fee_label TEXT NOT NULL,
              amount REAL NOT NULL,
              currency TEXT NOT NULL DEFAULT 'USD',
              method TEXT NOT NULL,
              reference TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL,
              confirmed_at TEXT,
              confirmed_by TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pay_student ON academic_payments(student_email, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pay_campus ON academic_payments(universite, status, created_at)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_platform_tariffs_table(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_tariffs (
                  id VARCHAR(36) PRIMARY KEY,
                  payload TEXT NOT NULL,
                  updated_at VARCHAR(40) NOT NULL,
                  updated_by VARCHAR(255) NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        cur.close()
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_tariffs (
              id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              updated_by TEXT
            )
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_digital_library_table(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS digital_library (
                  id VARCHAR(80) PRIMARY KEY,
                  title VARCHAR(200) NOT NULL,
                  author VARCHAR(120) NULL,
                  category VARCHAR(40) NOT NULL,
                  description TEXT NULL,
                  language VARCHAR(20) NOT NULL DEFAULT 'fr',
                  file_url VARCHAR(500) NULL,
                  cover_url VARCHAR(500) NULL,
                  published TINYINT(1) NOT NULL DEFAULT 1,
                  author_id VARCHAR(255) NOT NULL,
                  author_role VARCHAR(20) NOT NULL DEFAULT 'ministere',
                  created_at VARCHAR(40) NOT NULL,
                  updated_at VARCHAR(40) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        for idx_sql in (
            "CREATE INDEX idx_library_pub ON digital_library(published, created_at)",
            "CREATE INDEX idx_library_cat ON digital_library(category, published)",
        ):
            try:
                cur.execute(idx_sql)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                if exc.args[0] not in (1061, 1060):
                    raise
        cur.close()
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS digital_library (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              author TEXT,
              category TEXT NOT NULL,
              description TEXT,
              language TEXT NOT NULL DEFAULT 'fr',
              file_url TEXT,
              cover_url TEXT,
              published INTEGER NOT NULL DEFAULT 1,
              author_id TEXT NOT NULL,
              author_role TEXT NOT NULL DEFAULT 'ministere',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_library_pub ON digital_library(published, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_library_cat ON digital_library(category, published)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_diplomas_table(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS diplomas (
                  id VARCHAR(80) PRIMARY KEY,
                  student_email VARCHAR(255) NOT NULL,
                  student_name VARCHAR(200) NOT NULL,
                  matricule VARCHAR(50) NULL,
                  universite VARCHAR(80) NOT NULL,
                  university_name VARCHAR(200) NULL,
                  filiere VARCHAR(120) NULL,
                  niveau VARCHAR(40) NULL,
                  diploma_type VARCHAR(40) NOT NULL,
                  graduation_year INT NOT NULL,
                  diploma_number VARCHAR(80) NOT NULL,
                  verification_code VARCHAR(80) NOT NULL,
                  hash_signature VARCHAR(128) NOT NULL,
                  status VARCHAR(20) NOT NULL DEFAULT 'actif',
                  issued_by VARCHAR(255) NOT NULL,
                  issued_at VARCHAR(40) NOT NULL,
                  revoked_at VARCHAR(40) NULL,
                  revoked_by VARCHAR(255) NULL,
                  notes VARCHAR(400) NULL,
                  UNIQUE KEY uq_diploma_number (diploma_number)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        for idx_sql in (
            "CREATE INDEX idx_diplomas_campus ON diplomas(universite, issued_at)",
            "CREATE INDEX idx_diplomas_student ON diplomas(student_email, status)",
        ):
            try:
                cur.execute(idx_sql)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                if exc.args[0] not in (1061, 1060):
                    raise
        cur.close()
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS diplomas (
              id TEXT PRIMARY KEY,
              student_email TEXT NOT NULL,
              student_name TEXT NOT NULL,
              matricule TEXT,
              universite TEXT NOT NULL,
              university_name TEXT,
              filiere TEXT,
              niveau TEXT,
              diploma_type TEXT NOT NULL,
              graduation_year INTEGER NOT NULL,
              diploma_number TEXT NOT NULL UNIQUE,
              verification_code TEXT NOT NULL,
              hash_signature TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'actif',
              issued_by TEXT NOT NULL,
              issued_at TEXT NOT NULL,
              revoked_at TEXT,
              revoked_by TEXT,
              notes TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_diplomas_campus ON diplomas(universite, issued_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_diplomas_student ON diplomas(student_email, status)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_platform_courses_table(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_courses (
                  id VARCHAR(80) PRIMARY KEY,
                  code VARCHAR(40) NOT NULL,
                  title VARCHAR(200) NOT NULL,
                  description TEXT NULL,
                  category VARCHAR(40) NOT NULL DEFAULT 'mooc',
                  universite VARCHAR(80) NOT NULL,
                  university_name VARCHAR(200) NULL,
                  filiere VARCHAR(120) NULL,
                  niveau VARCHAR(40) NULL,
                  classe VARCHAR(120) NULL,
                  professor_email VARCHAR(255) NULL,
                  professor_name VARCHAR(200) NULL,
                  cover_url VARCHAR(500) NULL,
                  resource_url VARCHAR(500) NULL,
                  duration_hours INT NOT NULL DEFAULT 0,
                  credits INT NOT NULL DEFAULT 0,
                  published TINYINT(1) NOT NULL DEFAULT 1,
                  author_id VARCHAR(255) NULL,
                  author_role VARCHAR(20) NULL,
                  created_at VARCHAR(40) NOT NULL,
                  updated_at VARCHAR(40) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS course_enrollments (
                  id VARCHAR(80) PRIMARY KEY,
                  course_id VARCHAR(80) NOT NULL,
                  student_email VARCHAR(255) NOT NULL,
                  student_name VARCHAR(200) NULL,
                  matricule VARCHAR(50) NULL,
                  universite VARCHAR(80) NOT NULL,
                  progress INT NOT NULL DEFAULT 0,
                  status VARCHAR(20) NOT NULL DEFAULT 'active',
                  enrolled_at VARCHAR(40) NOT NULL,
                  UNIQUE KEY uq_course_student (course_id, student_email)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        for idx_sql in (
            "CREATE INDEX idx_courses_campus ON platform_courses(universite, published, created_at)",
            "CREATE INDEX idx_enroll_student ON course_enrollments(student_email, enrolled_at)",
        ):
            try:
                cur.execute(idx_sql)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                if exc.args[0] not in (1061, 1060):
                    raise
        cur.close()
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_courses (
              id TEXT PRIMARY KEY,
              code TEXT NOT NULL,
              title TEXT NOT NULL,
              description TEXT,
              category TEXT NOT NULL DEFAULT 'mooc',
              universite TEXT NOT NULL,
              university_name TEXT,
              filiere TEXT,
              niveau TEXT,
              classe TEXT,
              professor_email TEXT,
              professor_name TEXT,
              cover_url TEXT,
              resource_url TEXT,
              duration_hours INTEGER NOT NULL DEFAULT 0,
              credits INTEGER NOT NULL DEFAULT 0,
              published INTEGER NOT NULL DEFAULT 1,
              author_id TEXT,
              author_role TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS course_enrollments (
              id TEXT PRIMARY KEY,
              course_id TEXT NOT NULL,
              student_email TEXT NOT NULL,
              student_name TEXT,
              matricule TEXT,
              universite TEXT NOT NULL,
              progress INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'active',
              enrolled_at TEXT NOT NULL,
              UNIQUE(course_id, student_email)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_courses_campus ON platform_courses(universite, published, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_enroll_student ON course_enrollments(student_email, enrolled_at DESC)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


    except sqlite3.OperationalError:
        pass


def _migrate_career_offers_table(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS career_offers (
                  id VARCHAR(80) PRIMARY KEY,
                  scope VARCHAR(20) NOT NULL DEFAULT 'campus',
                  offer_type VARCHAR(40) NOT NULL DEFAULT 'stage',
                  title VARCHAR(200) NOT NULL,
                  organization VARCHAR(200) NULL,
                  location VARCHAR(120) NULL,
                  description TEXT NULL,
                  requirements TEXT NULL,
                  filiere VARCHAR(120) NULL,
                  niveau VARCHAR(40) NULL,
                  universite VARCHAR(80) NOT NULL,
                  university_name VARCHAR(200) NULL,
                  contact_email VARCHAR(255) NULL,
                  apply_url VARCHAR(500) NULL,
                  deadline VARCHAR(20) NULL,
                  published TINYINT(1) NOT NULL DEFAULT 1,
                  author_id VARCHAR(255) NULL,
                  author_role VARCHAR(20) NULL,
                  created_at VARCHAR(40) NOT NULL,
                  updated_at VARCHAR(40) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS career_applications (
                  id VARCHAR(80) PRIMARY KEY,
                  career_id VARCHAR(80) NOT NULL,
                  student_email VARCHAR(255) NOT NULL,
                  student_name VARCHAR(200) NULL,
                  matricule VARCHAR(50) NULL,
                  universite VARCHAR(80) NOT NULL,
                  message TEXT NULL,
                  status VARCHAR(20) NOT NULL DEFAULT 'pending',
                  applied_at VARCHAR(40) NOT NULL,
                  UNIQUE KEY uq_career_student (career_id, student_email)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        for idx_sql in (
            "CREATE INDEX idx_careers_scope ON career_offers(scope, published, created_at)",
            "CREATE INDEX idx_careers_campus ON career_offers(universite, published, created_at)",
            "CREATE INDEX idx_career_apps ON career_applications(career_id, applied_at)",
        ):
            try:
                cur.execute(idx_sql)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                if exc.args[0] not in (1061, 1060):
                    raise
        cur.close()
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS career_offers (
              id TEXT PRIMARY KEY,
              scope TEXT NOT NULL DEFAULT 'campus',
              offer_type TEXT NOT NULL DEFAULT 'stage',
              title TEXT NOT NULL,
              organization TEXT,
              location TEXT,
              description TEXT,
              requirements TEXT,
              filiere TEXT,
              niveau TEXT,
              universite TEXT NOT NULL,
              university_name TEXT,
              contact_email TEXT,
              apply_url TEXT,
              deadline TEXT,
              published INTEGER NOT NULL DEFAULT 1,
              author_id TEXT,
              author_role TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS career_applications (
              id TEXT PRIMARY KEY,
              career_id TEXT NOT NULL,
              student_email TEXT NOT NULL,
              student_name TEXT,
              matricule TEXT,
              universite TEXT NOT NULL,
              message TEXT,
              status TEXT NOT NULL DEFAULT 'pending',
              applied_at TEXT NOT NULL,
              UNIQUE(career_id, student_email)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_careers_scope ON career_offers(scope, published, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_careers_campus ON career_offers(universite, published, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_career_apps ON career_applications(career_id, applied_at DESC)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_social_posts_table(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS social_posts (
                  id VARCHAR(80) PRIMARY KEY,
                  universite VARCHAR(80) NOT NULL,
                  author_email VARCHAR(255) NOT NULL,
                  author_name VARCHAR(200) NULL,
                  author_role VARCHAR(20) NULL,
                  content TEXT NOT NULL,
                  audience VARCHAR(20) NOT NULL DEFAULT 'campus',
                  filiere VARCHAR(120) NULL,
                  likes_json TEXT NULL,
                  hidden TINYINT(1) NOT NULL DEFAULT 0,
                  created_at VARCHAR(40) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        for idx_sql in (
            "CREATE INDEX idx_social_campus ON social_posts(universite, created_at)",
            "CREATE INDEX idx_social_author ON social_posts(author_email, created_at)",
        ):
            try:
                cur.execute(idx_sql)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                if exc.args[0] not in (1061, 1060):
                    raise
        cur.close()
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_posts (
              id TEXT PRIMARY KEY,
              universite TEXT NOT NULL,
              author_email TEXT NOT NULL,
              author_name TEXT,
              author_role TEXT,
              content TEXT NOT NULL,
              audience TEXT NOT NULL DEFAULT 'campus',
              filiere TEXT,
              likes_json TEXT DEFAULT '[]',
              hidden INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_campus ON social_posts(universite, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_social_author ON social_posts(author_email, created_at DESC)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_mobile_money_table(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mobile_money_transactions (
                  id VARCHAR(80) PRIMARY KEY,
                  provider VARCHAR(20) NOT NULL,
                  payer_phone VARCHAR(30) NOT NULL,
                  amount_cdf INT NOT NULL,
                  amount_usd DECIMAL(12,2) NOT NULL DEFAULT 0,
                  currency VARCHAR(10) NOT NULL DEFAULT 'CDF',
                  purpose VARCHAR(40) NOT NULL,
                  status VARCHAR(20) NOT NULL DEFAULT 'pending',
                  reference_external VARCHAR(120) NULL,
                  metadata_json TEXT NULL,
                  user_email VARCHAR(255) NULL,
                  universite VARCHAR(80) NULL,
                  academic_payment_id VARCHAR(80) NULL,
                  error_message VARCHAR(300) NULL,
                  created_at VARCHAR(40) NOT NULL,
                  updated_at VARCHAR(40) NOT NULL,
                  completed_at VARCHAR(40) NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        for idx_sql in (
            "CREATE INDEX idx_mm_status ON mobile_money_transactions(status, created_at)",
            "CREATE INDEX idx_mm_email ON mobile_money_transactions(user_email, created_at)",
        ):
            try:
                cur.execute(idx_sql)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                if exc.args[0] not in (1061, 1060):
                    raise
        cur.close()
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_money_transactions (
              id TEXT PRIMARY KEY,
              provider TEXT NOT NULL,
              payer_phone TEXT NOT NULL,
              amount_cdf INTEGER NOT NULL,
              amount_usd REAL NOT NULL DEFAULT 0,
              currency TEXT NOT NULL DEFAULT 'CDF',
              purpose TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              reference_external TEXT,
              metadata_json TEXT,
              user_email TEXT,
              universite TEXT,
              academic_payment_id TEXT,
              error_message TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              completed_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mm_status ON mobile_money_transactions(status, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mm_email ON mobile_money_transactions(user_email, created_at DESC)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_live_sessions_table(conn, backend: str) -> None:
    if backend == "mysql":
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS live_sessions (
                  id VARCHAR(80) PRIMARY KEY,
                  universite VARCHAR(80) NOT NULL,
                  professor_email VARCHAR(255) NOT NULL,
                  professor_name VARCHAR(200) NULL,
                  title VARCHAR(200) NOT NULL,
                  description TEXT NULL,
                  course_code VARCHAR(40) NULL,
                  filiere VARCHAR(120) NULL,
                  niveau VARCHAR(40) NULL,
                  room_name VARCHAR(80) NULL,
                  status VARCHAR(20) NOT NULL DEFAULT 'scheduled',
                  scheduled_at VARCHAR(40) NULL,
                  started_at VARCHAR(40) NULL,
                  ended_at VARCHAR(40) NULL,
                  recording_url VARCHAR(500) NULL,
                  transcript TEXT NULL,
                  ai_summary TEXT NULL,
                  ai_key_points_json TEXT NULL,
                  documents_json TEXT NULL,
                  attendance_json TEXT NULL,
                  questions_json TEXT NULL,
                  participation_report_json TEXT NULL,
                  created_at VARCHAR(40) NOT NULL,
                  updated_at VARCHAR(40) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            conn.commit()
        except pymysql.err.OperationalError:
            pass
        for idx_sql in (
            "CREATE INDEX idx_live_campus ON live_sessions(universite, updated_at)",
            "CREATE INDEX idx_live_status ON live_sessions(status, scheduled_at)",
            "CREATE INDEX idx_live_prof ON live_sessions(professor_email, created_at)",
        ):
            try:
                cur.execute(idx_sql)
                conn.commit()
            except pymysql.err.OperationalError as exc:
                if exc.args[0] not in (1061, 1060):
                    raise
        cur.close()
        return

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS live_sessions (
              id TEXT PRIMARY KEY,
              universite TEXT NOT NULL,
              professor_email TEXT NOT NULL,
              professor_name TEXT,
              title TEXT NOT NULL,
              description TEXT,
              course_code TEXT,
              filiere TEXT,
              niveau TEXT,
              room_name TEXT,
              status TEXT NOT NULL DEFAULT 'scheduled',
              scheduled_at TEXT,
              started_at TEXT,
              ended_at TEXT,
              recording_url TEXT,
              transcript TEXT,
              ai_summary TEXT,
              ai_key_points_json TEXT,
              documents_json TEXT,
              attendance_json TEXT,
              questions_json TEXT,
              participation_report_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_campus ON live_sessions(universite, updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_status ON live_sessions(status, scheduled_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_prof ON live_sessions(professor_email, created_at DESC)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


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
    _migrate_reset_code_column(conn, "mysql")
    _migrate_users_classe_column(conn, "mysql")
    _migrate_users_logo_url_column(conn, "mysql")
    _migrate_home_news_table(conn, "mysql")
    _migrate_home_news_media_columns(conn, "mysql")
    _migrate_home_news_views(conn, "mysql")
    _migrate_document_views(conn, "mysql")
    _migrate_platform_tariffs_table(conn, "mysql")
    _migrate_campus_academic_fees_columns(conn, "mysql")
    _migrate_academic_payments_table(conn, "mysql")
    _migrate_digital_library_table(conn, "mysql")
    _migrate_diplomas_table(conn, "mysql")
    _migrate_platform_courses_table(conn, "mysql")
    _migrate_career_offers_table(conn, "mysql")
    _migrate_social_posts_table(conn, "mysql")
    _migrate_mobile_money_table(conn, "mysql")
    _migrate_live_sessions_table(conn, "mysql")
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


def _migrate_users_admin_roles_sqlite(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    ddl = row["sql"] if row else ""
    if "'ministere'" in ddl and "'superadmin'" in ddl:
        return

    cols = conn.execute("PRAGMA table_info(users)").fetchall()
    if not cols:
        return

    parts: list[str] = []
    for col in cols:
        _cid, name, ctype, notnull, default, pk = col
        if name == "role":
            parts.append(
                "role TEXT NOT NULL CHECK (role IN "
                "('etudiant','professeur','assistant','universite','section','ministere','superadmin'))"
            )
            continue
        col_def = f"{name} {ctype or 'TEXT'}"
        if pk:
            col_def += " PRIMARY KEY"
        elif notnull:
            col_def += " NOT NULL"
        if default is not None:
            col_def += f" DEFAULT {default}"
        parts.append(col_def)

    col_names = ", ".join(c[1] for c in cols)
    conn.execute(f"CREATE TABLE users_new ({', '.join(parts)})")
    conn.execute(
        f"INSERT INTO users_new ({col_names}) SELECT {col_names} FROM users"
    )
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_new RENAME TO users")


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
        "ALTER TABLE users ADD COLUMN classe TEXT",
        "ALTER TABLE users ADD COLUMN logo_url TEXT",
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
        "ALTER TABLE password_reset_tokens ADD COLUMN code_hash TEXT",
        "CREATE INDEX IF NOT EXISTS idx_reset_code ON password_reset_tokens(code_hash)",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    _migrate_users_section_role_sqlite(conn)
    _migrate_users_admin_roles_sqlite(conn)
    _migrate_users_logo_url_column(conn, "sqlite")
    _migrate_home_news_table(conn, "sqlite")
    _migrate_home_news_media_columns(conn, "sqlite")
    _migrate_home_news_views(conn, "sqlite")
    _migrate_document_views(conn, "sqlite")
    _migrate_platform_tariffs_table(conn, "sqlite")
    _migrate_campus_academic_fees_columns(conn, "sqlite")
    _migrate_academic_payments_table(conn, "sqlite")
    _migrate_digital_library_table(conn, "sqlite")
    _migrate_diplomas_table(conn, "sqlite")
    _migrate_platform_courses_table(conn, "sqlite")
    _migrate_career_offers_table(conn, "sqlite")
    _migrate_social_posts_table(conn, "sqlite")
    _migrate_mobile_money_table(conn, "sqlite")
    _migrate_live_sessions_table(conn, "sqlite")
    _migrate_reset_code_column(conn, "sqlite")
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
        "campusAcademicFees": _json_load(row["campus_academic_fees"], None)
        if "campus_academic_fees" in keys
        else None,
        "universityFees": _json_load(row["university_fees"], None)
        if "university_fees" in keys
        else None,
        "campusPartnerBank": _json_load(row["campus_partner_bank"], None)
        if "campus_partner_bank" in keys
        else None,
        "sectionId": row["section_id"] if "section_id" in keys else None,
        "classe": row["classe"] if "classe" in keys else None,
        "nomination": row["nomination"] if "nomination" in keys else None,
        "logoUrl": row["logo_url"] if "logo_url" in keys else None,
        "createdAt": row["created_at"],
    }


def row_to_document(row: Any | None) -> dict[str, Any] | None:
    if not row:
        return None
    keys = row.keys() if hasattr(row, "keys") else []
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
        "viewCount": int(row["view_count"] or 0) if "view_count" in keys else 0,
        "uniqueViewCount": 0,
    }
