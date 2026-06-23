import json
import unicodedata
from datetime import datetime, timezone

from app.database import get_db
from app.services.user_service import _is_section_head_actor, _section_head_section_id
from app.utils.platform_security import assert_campus_access, uid
from app.utils.sanitize import clean_text

VALID_CATEGORIES = {
    "academique",
    "finance",
    "administration",
    "stage",
    "horaire",
    "document",
    "stage_memoire",
    "autre",
    # anciennes catégories (réclamations déjà en base)
    "scolarite",
    "notes",
    "frais",
    "documents",
    "bourse",
    "emploi_du_temps",
    "stage_emploi",
    "discipline",
    "bibliotheque",
    "infrastructure",
    "enseignement",
    "inscription",
    "technique",
    "infrastructures_services",
}
VALID_STATUTS = {"ouverte", "en_cours", "resolue", "fermee"}
MAX_ATTACHMENTS = 3
MAX_ATTACHMENT_SIZE = 800_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(s: str | None) -> str:
    if not s:
        return ""
    t = unicodedata.normalize("NFD", s.strip().lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _row_to_section(row) -> dict:
    return {
        "id": row["id"],
        "universityId": row["university_id"],
        "universite": row["universite"],
        "name": row["name"],
        "filiere": row["filiere"],
        "responsableNom": row["responsable_nom"] or "",
        "email": row["email"] or "",
        "telephone": row["telephone"] or "",
        "active": bool(row["active"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _row_to_reclamation(row) -> dict:
    return {
        "id": row["id"],
        "sectionId": row["section_id"],
        "sectionName": row["section_name"] or "",
        "studentId": row["student_id"],
        "studentEmail": row["student_email"],
        "studentNom": row["student_nom"] or "",
        "matricule": row["matricule"] or "—",
        "universite": row["universite"],
        "filiere": row["filiere"] or "",
        "niveau": row["niveau"] or "",
        "sujet": row["sujet"],
        "message": row["message"],
        "categorie": row["categorie"],
        "categorieDetail": row["categorie_detail"] or "",
        "statut": row["statut"],
        "reponse": row["reponse"] or "",
        "traitePar": row["traite_par"] or "",
        "attachments": json.loads(row["attachments"] or "[]"),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _sanitize_attachments(raw) -> list:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:MAX_ATTACHMENTS]:
        if not isinstance(item, dict):
            continue
        size = int(item.get("size") or 0)
        if size > MAX_ATTACHMENT_SIZE:
            raise ValueError("ATTACHMENT_TOO_LARGE")
        data_url = item.get("dataUrl") or item.get("data_url") or ""
        if data_url and not str(data_url).startswith("data:"):
            raise ValueError("INVALID_ATTACHMENT")
        out.append(
            {
                "id": clean_text(item.get("id"), 80) or uid("att"),
                "name": clean_text(item.get("name"), 200) or "piece-jointe",
                "type": clean_text(item.get("type"), 120) or "application/octet-stream",
                "size": size,
                "dataUrl": str(data_url)[:MAX_ATTACHMENT_SIZE * 2] if data_url else "",
            }
        )
    return out


def _campus_for_actor(actor: dict) -> str:
    return (
        actor.get("universite")
        or actor.get("codeUni")
        or actor.get("sigle")
        or ""
    )


def list_sections_for_actor(actor: dict) -> list[dict]:
    role = actor.get("role")
    db = get_db()
    if role == "universite":
        campus = _campus_for_actor(actor)
        rows = db.execute(
            """SELECT * FROM faculty_sections
               WHERE universite = ? ORDER BY name""",
            (campus,),
        ).fetchall()
        return [_row_to_section(r) for r in rows]
    if role == "assistant":
        campus = _campus_for_actor(actor)
        rows = db.execute(
            """SELECT * FROM faculty_sections
               WHERE universite = ? AND active = 1 ORDER BY name""",
            (campus,),
        ).fetchall()
        return [_row_to_section(r) for r in rows]
    if role == "section" or (role == "professeur" and _is_section_head_actor(actor)):
        section_id = _section_head_section_id(actor)
        if not section_id:
            return []
        row = db.execute(
            "SELECT * FROM faculty_sections WHERE id = ?", (section_id,)
        ).fetchone()
        return [_row_to_section(row)] if row else []
    if role == "etudiant":
        campus = _campus_for_actor(actor)
        rows = db.execute(
            """SELECT * FROM faculty_sections
               WHERE universite = ? AND active = 1 ORDER BY name""",
            (campus,),
        ).fetchall()
        return [_row_to_section(r) for r in rows]
    return []


def list_campus_sections_public(universite: str) -> list[dict]:
    from app.utils.campus_catalog import resolve_campus_id, same_campus

    raw = str(universite or "").strip()
    campus = resolve_campus_id(raw)
    if not campus or campus == "autre":
        return []
    db = get_db()
    rows = db.execute(
        """SELECT * FROM faculty_sections
           WHERE active = 1 ORDER BY filiere, name"""
    ).fetchall()
    return [
        _row_to_section(row)
        for row in rows
        if same_campus(campus, row["universite"])
    ]


def seed_faculty_sections_for_university(
    university_id: str, campus: str, rows: list
) -> list[dict]:
    from app.utils.campus_catalog import resolve_campus_id

    campus_id = resolve_campus_id(campus) or campus
    if not campus_id or not rows:
        return []
    uid_key = clean_text(university_id, 120) or ""
    created: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = clean_text(row.get("name"), 200)
        filiere = clean_text(row.get("filiere"), 200)
        if not name or not filiere:
            continue
        responsable = clean_text(
            row.get("responsableNom")
            or row.get("responsable_nom")
            or row.get("responsable"),
            200,
        ) or "Responsable"
        created.append(
            _upsert_section_record(
                uid_key,
                campus_id,
                {
                    "id": row.get("id"),
                    "name": name,
                    "filiere": filiere,
                    "responsableNom": responsable,
                    "email": row.get("email") or "",
                    "telephone": row.get("telephone") or "",
                    "active": True,
                },
            )
        )
    return created


def _upsert_section_record(university_id: str, campus: str, data: dict) -> dict:
    name = clean_text(data.get("name"), 200)
    filiere = clean_text(data.get("filiere"), 200)
    if not name or not filiere:
        raise ValueError("INVALID_INPUT")
    responsable = clean_text(
        data.get("responsableNom") or data.get("responsable_nom"), 200
    ) or "Responsable"

    section_id = clean_text(data.get("id"), 80)
    now = _now()
    db = get_db()
    if not section_id:
        dup = db.execute(
            """SELECT id FROM faculty_sections
               WHERE universite = ? AND lower(name) = lower(?) AND lower(filiere) = lower(?)""",
            (campus, name, filiere),
        ).fetchone()
        section_id = dup["id"] if dup else uid("sec")
    existing = db.execute(
        "SELECT id FROM faculty_sections WHERE id = ?", (section_id,)
    ).fetchone()
    core = (
        university_id,
        campus,
        name,
        filiere,
        responsable,
        clean_text(data.get("email"), 200).lower(),
        clean_text(data.get("telephone"), 40),
        1 if data.get("active", True) is not False else 0,
    )
    if existing:
        db.execute(
            """UPDATE faculty_sections SET
               university_id = ?, universite = ?, name = ?, filiere = ?,
               responsable_nom = ?, email = ?, telephone = ?, active = ?, updated_at = ?
               WHERE id = ?""",
            (*core, now, section_id),
        )
    else:
        db.execute(
            """INSERT INTO faculty_sections (
               id, university_id, universite, name, filiere, responsable_nom,
               email, telephone, active, created_at, updated_at
             ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (section_id, *core, now, now),
        )
    db.commit()
    row = db.execute(
        "SELECT * FROM faculty_sections WHERE id = ?", (section_id,)
    ).fetchone()
    return _row_to_section(row)


def find_section_for_student(universite: str, filiere: str | None) -> dict | None:
    db = get_db()
    rows = db.execute(
        """SELECT * FROM faculty_sections
           WHERE universite = ? AND active = 1""",
        (universite,),
    ).fetchall()
    sf = _norm(filiere)
    for row in rows:
        nf = _norm(row["filiere"])
        if sf and (nf == sf or sf in nf or nf in sf):
            return _row_to_section(row)
    for row in rows:
        if _norm(row["filiere"]) == "toutes filieres":
            return _row_to_section(row)
    return None


def upsert_section(actor: dict, data: dict) -> dict:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    campus = assert_campus_access(actor, data.get("universite") or _campus_for_actor(actor))
    if not clean_text(data.get("responsableNom") or data.get("responsable_nom"), 200):
        raise ValueError("INVALID_INPUT")
    university_id = actor.get("id") or actor.get("userId") or actor.get("email")
    return _upsert_section_record(university_id, campus, data)


def update_section(actor: dict, section_id: str, data: dict) -> dict:
    if actor.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    db = get_db()
    row = db.execute(
        "SELECT * FROM faculty_sections WHERE id = ?", (section_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    assert_campus_access(actor, row["universite"])
    fields = {}
    for src, dst in (
        ("name", "name"),
        ("filiere", "filiere"),
        ("responsableNom", "responsable_nom"),
        ("responsable_nom", "responsable_nom"),
        ("email", "email"),
        ("telephone", "telephone"),
    ):
        if data.get(src) is not None:
            fields[dst] = clean_text(data[src], 200)
    if "active" in data:
        fields["active"] = 0 if data["active"] is False else 1
    if not fields:
        return _row_to_section(row)
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE faculty_sections SET {sets}, updated_at = ? WHERE id = ?",
        (*fields.values(), _now(), section_id),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM faculty_sections WHERE id = ?", (section_id,)
    ).fetchone()
    return _row_to_section(row)


def list_reclamations_for_actor(
    actor: dict, limit: int = 50, offset: int = 0
) -> list[dict]:
    role = actor.get("role")
    db = get_db()
    if role == "etudiant":
        rows = db.execute(
            """SELECT * FROM reclamations WHERE student_email = ? COLLATE NOCASE
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (actor["email"], limit, offset),
        ).fetchall()
        return [_row_to_reclamation(r) for r in rows]
    if role == "section" or (role == "professeur" and _is_section_head_actor(actor)):
        section_id = _section_head_section_id(actor)
        if not section_id:
            return []
        rows = db.execute(
            """SELECT * FROM reclamations WHERE section_id = ?
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (section_id, limit, offset),
        ).fetchall()
        return [_row_to_reclamation(r) for r in rows]
    if role == "universite":
        campus = _campus_for_actor(actor)
        rows = db.execute(
            """SELECT r.* FROM reclamations r
               INNER JOIN faculty_sections s ON s.id = r.section_id
               WHERE s.universite = ?
               ORDER BY r.created_at DESC LIMIT ? OFFSET ?""",
            (campus, limit, offset),
        ).fetchall()
        return [_row_to_reclamation(r) for r in rows]
    if role == "assistant":
        campus = _campus_for_actor(actor)
        rows = db.execute(
            """SELECT * FROM reclamations WHERE universite = ?
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (campus, limit, offset),
        ).fetchall()
        return [_row_to_reclamation(r) for r in rows]
    return []


def _assert_reclamation_access(actor: dict, rec: dict) -> None:
    role = actor.get("role")
    if role == "section" or (role == "professeur" and _is_section_head_actor(actor)):
        if rec["sectionId"] != _section_head_section_id(actor):
            raise ValueError("FORBIDDEN")
        return
    if role == "universite":
        db = get_db()
        row = db.execute(
            "SELECT universite FROM faculty_sections WHERE id = ?",
            (rec["sectionId"],),
        ).fetchone()
        if not row:
            raise ValueError("NOT_FOUND")
        assert_campus_access(actor, row["universite"])
        return
    if role == "assistant":
        assert_campus_access(actor, rec["universite"])
        return
    raise ValueError("FORBIDDEN")


def create_reclamation(actor: dict, data: dict) -> dict:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")
    sujet = clean_text(data.get("sujet"), 300)
    message = clean_text(data.get("message"), 8000)
    if not sujet or not message:
        raise ValueError("INVALID_INPUT")
    categorie = clean_text(data.get("categorie"), 40) or "autre"
    if categorie not in VALID_CATEGORIES:
        raise ValueError("INVALID_INPUT")
    categorie_detail = clean_text(
        data.get("categorieDetail") or data.get("categorie_detail"), 300
    )
    if categorie == "autre" and len(categorie_detail) < 3:
        raise ValueError("INVALID_INPUT")

    universite = actor.get("universite") or ""
    section = find_section_for_student(universite, actor.get("filiere"))
    if not section:
        raise ValueError("NO_SECTION")

    student_nom = " ".join(
        filter(None, [actor.get("prenom"), actor.get("nom")])
    ).strip() or actor["email"]
    attachments = _sanitize_attachments(data.get("attachments") or [])
    rec_id = clean_text(data.get("id"), 80) or uid("rec")
    now = _now()

    db = get_db()
    db.execute(
        """INSERT INTO reclamations (
           id, section_id, section_name, student_id, student_email, student_nom,
           matricule, universite, filiere, niveau, sujet, message, categorie,
           categorie_detail, statut, reponse, traite_par, attachments,
           created_at, updated_at
         ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            rec_id,
            section["id"],
            section["name"],
            actor.get("id") or actor["email"],
            actor["email"],
            student_nom,
            actor.get("matricule") or "—",
            universite,
            actor.get("filiere") or "",
            actor.get("niveau") or "",
            sujet,
            message,
            categorie,
            categorie_detail,
            "ouverte",
            "",
            "",
            json.dumps(attachments),
            now,
            now,
        ),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM reclamations WHERE id = ?", (rec_id,)
    ).fetchone()
    return _row_to_reclamation(row)


def update_reclamation(actor: dict, rec_id: str, data: dict) -> dict:
    db = get_db()
    row = db.execute(
        "SELECT * FROM reclamations WHERE id = ?", (rec_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    rec = _row_to_reclamation(row)
    _assert_reclamation_access(actor, rec)

    statut = data.get("statut", rec["statut"])
    if statut not in VALID_STATUTS:
        raise ValueError("INVALID_INPUT")
    reponse = data.get("reponse")
    if reponse is None:
        reponse = rec["reponse"]
    else:
        reponse = clean_text(reponse, 8000)

    role = actor.get("role")
    traite_par = clean_text(data.get("traitePar") or data.get("traite_par"), 200)
    if not traite_par:
        if role == "universite":
            traite_par = (
                (actor.get("nom") or "Administration université")
                + (f" — {rec['sectionName']}" if rec.get("sectionName") else "")
            )
        elif role == "assistant":
            traite_par = (
                " ".join(filter(None, [actor.get("prenom"), actor.get("nom")])).strip()
                or actor["email"]
            ) + " (Assistant)"
        elif role == "professeur" and _is_section_head_actor(actor):
            traite_par = (
                " ".join(filter(None, [actor.get("prenom"), actor.get("nom")])).strip()
                or actor["email"]
            ) + " (Chef de section)"
        else:
            traite_par = actor.get("nom") or actor["email"]

    now = _now()
    db.execute(
        """UPDATE reclamations SET statut = ?, reponse = ?, traite_par = ?, updated_at = ?
           WHERE id = ?""",
        (statut, reponse, traite_par, now, rec_id),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM reclamations WHERE id = ?", (rec_id,)
    ).fetchone()
    return _row_to_reclamation(row)
