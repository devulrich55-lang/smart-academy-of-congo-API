import json
import uuid
from datetime import datetime, timezone

from passlib.context import CryptContext

from app.database import get_db

DEMO_PASSWORD = "Demo2025!"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _seed_demo_sections(db, now: str) -> None:
    count = db.execute("SELECT COUNT(*) as c FROM faculty_sections").fetchone()["c"]
    if count > 0:
        return
    sections = [
        {
            "id": "sec-demo-eco",
            "university_id": "uni-demo",
            "universite": "unkin",
            "name": "Section — Sciences économiques & Gestion",
            "filiere": "Sciences économiques — Gestion",
            "responsable_nom": "M. Kabila",
            "email": "section.gestion@unikin.cd",
            "telephone": "+243 81 000 0001",
        },
        {
            "id": "sec-demo-info",
            "university_id": "uni-demo",
            "universite": "unkin",
            "name": "Section — Informatique",
            "filiere": "Informatique",
            "responsable_nom": "Mme. Mwamba",
            "email": "section.info@unikin.cd",
            "telephone": "+243 81 000 0002",
        },
    ]
    for s in sections:
        db.execute(
            """INSERT INTO faculty_sections (
               id, university_id, universite, name, filiere, responsable_nom,
               email, telephone, active, created_at, updated_at
             ) VALUES (?,?,?,?,?,?,?,?,1,?,?)""",
            (
                s["id"],
                s["university_id"],
                s["universite"],
                s["name"],
                s["filiere"],
                s["responsable_nom"],
                s["email"],
                s["telephone"],
                now,
                now,
            ),
        )
    db.commit()


def seed_if_empty() -> None:
    db = get_db()
    count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    if count > 0:
        return

    password_hash = pwd_context.hash(DEMO_PASSWORD)
    now = datetime.now(timezone.utc).isoformat()

    users = [
        {
            "id": str(uuid.uuid4()),
            "email": "etu.demo@unikin.cd",
            "role": "etudiant",
            "prenom": "Marie",
            "nom": "Kabongo",
            "universite": "unkin",
            "filiere": "Sciences économiques — Gestion",
            "niveau": "l2",
            "matricule": "ETU-2024-08452",
        },
        {
            "id": str(uuid.uuid4()),
            "email": "prof.demo@unikin.cd",
            "role": "professeur",
            "prenom": "Jean",
            "nom": "Mukendi",
            "universite": "unkin",
            "departement": "Faculté des Sciences",
            "cours_classes": json.dumps(
                [
                    {
                        "courseCode": "ECO101",
                        "courseName": "Introduction à l'économie",
                        "filiere": "Sciences économiques — Gestion",
                        "niveau": "l2",
                        "classe": "L2 Gestion — Groupe A",
                        "universite": "unkin",
                    }
                ]
            ),
        },
        {
            "id": str(uuid.uuid4()),
            "email": "assist.demo@unikin.cd",
            "role": "assistant",
            "prenom": "Grace",
            "nom": "Ilunga",
            "universite": "unkin",
            "service": "scolarite",
            "cours_classes": json.dumps(
                [
                    {
                        "courseCode": "ADM-SCO",
                        "courseName": "Scolarité L2",
                        "filiere": "Toutes filières",
                        "niveau": "l2",
                        "classe": "L2 — Toutes classes",
                        "universite": "unkin",
                    }
                ]
            ),
        },
    ]

    for u in users:
        db.execute(
            """INSERT INTO users (id, email, password_hash, role, prenom, nom, universite,
               filiere, niveau, matricule, departement, service, cours_classes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                u["id"],
                u["email"],
                password_hash,
                u["role"],
                u.get("prenom"),
                u.get("nom"),
                u.get("universite"),
                u.get("filiere"),
                u.get("niveau"),
                u.get("matricule"),
                u.get("departement"),
                u.get("service"),
                u.get("cours_classes", "[]"),
                now,
                now,
            ),
        )

    prof = next(u for u in users if u["role"] == "professeur")
    docs = [
        {
            "id": str(uuid.uuid4()),
            "title": "Syllabus — Introduction à l'économie",
            "description": "Programme ECO101 — L2 Gestion",
            "source": "professeur",
            "author": "Dr. Mukendi",
            "author_id": prof["id"],
            "media_category": "document",
            "type": "PDF",
            "audience_type": "ma_classe",
            "universite": "unkin",
            "filiere": "Sciences économiques — Gestion",
            "niveau": "l2",
            "course_code": "ECO101",
            "course_name": "Introduction à l'économie",
            "classe": "L2 Gestion — Groupe A",
            "allow_reactions": 1,
        },
        {
            "id": str(uuid.uuid4()),
            "title": "Calendrier examens — Campus",
            "description": "Tous les étudiants UNIKIN",
            "source": "administration",
            "author": "Secrétariat",
            "author_id": prof["id"],
            "media_category": "document",
            "type": "PDF",
            "audience_type": "campus",
            "universite": "unkin",
            "allow_reactions": 0,
        },
    ]

    for d in docs:
        db.execute(
            """INSERT INTO documents (id, title, description, source, author, author_id, date,
               media_category, type, audience_type, universite, filiere, niveau, course_code,
               course_name, classe, allow_reactions, reactions, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d["id"],
                d["title"],
                d["description"],
                d["source"],
                d["author"],
                d["author_id"],
                now[:10],
                d["media_category"],
                d["type"],
                d["audience_type"],
                d["universite"],
                d.get("filiere"),
                d.get("niveau"),
                d.get("course_code"),
                d.get("course_name"),
                d.get("classe"),
                d["allow_reactions"],
                "{}",
                now,
                now,
            ),
        )

    db.commit()
    _seed_demo_sections(db, now)
    print(
        "[SAC] Base démo initialisée. Comptes : etu.demo@unikin.cd / prof.demo@unikin.cd — mot de passe:",
        DEMO_PASSWORD,
    )


def seed_demo_sections_if_missing() -> None:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    _seed_demo_sections(db, now)


INSTITUTIONAL_PASSWORD = "Admin2025!"
SUPERADMIN_SEED_EMAIL = "devulrich55@gmail.com"
SUPERADMIN_SEED_PASSWORD = "Ulrich11+"
LEGACY_SUPERADMIN_EMAILS = (
    "ulrichcibamba55@gmail.com",
    "admin@superadmin.cd",
    "djemcibamba@gmail.com",
)


def _ensure_superadmin_seed() -> None:
    """Compte Super Admin principal — création ou migration depuis un ancien seed."""
    from app.database import get_db
    from app.services.user_service import create_user, find_user_by_email, update_password
    from datetime import datetime, timezone

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    target = find_user_by_email(SUPERADMIN_SEED_EMAIL)

    for legacy_email in LEGACY_SUPERADMIN_EMAILS:
        if legacy_email.lower() == SUPERADMIN_SEED_EMAIL.lower():
            continue
        legacy = find_user_by_email(legacy_email)
        if not legacy or legacy.get("role") != "superadmin":
            continue
        if target and target.get("id") != legacy.get("id"):
            db.execute("DELETE FROM users WHERE id = ?", (legacy["id"],))
            db.commit()
            print("[SAC] Super Admin doublon supprimé:", legacy_email)
            continue
        db.execute(
            "UPDATE users SET email = ?, updated_at = ? WHERE id = ?",
            (SUPERADMIN_SEED_EMAIL, now, legacy["id"]),
        )
        db.commit()
        update_password(legacy["id"], SUPERADMIN_SEED_PASSWORD)
        target = find_user_by_email(SUPERADMIN_SEED_EMAIL)
        print("[SAC] Super Admin migré:", legacy_email, "→", SUPERADMIN_SEED_EMAIL)

    target = find_user_by_email(SUPERADMIN_SEED_EMAIL)
    if target and target.get("role") == "superadmin":
        update_password(target["id"], SUPERADMIN_SEED_PASSWORD)
        return

    super_count = db.execute(
        "SELECT COUNT(*) AS c FROM users WHERE role = 'superadmin'"
    ).fetchone()["c"]
    if super_count >= 2:
        return

    create_user(
        {
            "email": SUPERADMIN_SEED_EMAIL,
            "password": SUPERADMIN_SEED_PASSWORD,
            "role": "superadmin",
            "prenom": "Ulrich",
            "nom": "Admin SAC",
            "telephone": "+243 81 100 0001",
        }
    )
    print("[SAC] Compte Super Admin créé:", SUPERADMIN_SEED_EMAIL)


def seed_institutional_admins_if_missing() -> None:
    from app.services.user_service import create_user, find_user_by_email

    _ensure_superadmin_seed()

    seeds = [
        {
            "email": "admin@ministere.cd",
            "role": "ministere",
            "prenom": "Ministere",
            "nom": "Education",
            "telephone": "+243 82 200 0002",
        },
    ]
    created = []
    for item in seeds:
        if find_user_by_email(item["email"]):
            continue
        create_user(
            {
                **item,
                "password": INSTITUTIONAL_PASSWORD,
            }
        )
        created.append(item["email"])
    if created:
        print(
            "[SAC] Comptes institutionnels créés:",
            ", ".join(created),
            "— mot de passe:",
            INSTITUTIONAL_PASSWORD,
        )


TECH_TEAM_PASSWORD = "EvoSU2026!"


def seed_tech_team_if_missing() -> None:
    """Comptes Dev Center et Tech Manager (Jean + responsable technique)."""
    from app.services.user_service import create_user, find_user_by_email

    seeds = [
        {
            "email": "jean.mukendi@evosmartuni.com",
            "role": "developpeur",
            "prenom": "Jean",
            "nom": "Mukendi",
            "telephone": "+243 81 500 0001",
            "fonction": "Développeur Backend Python",
        },
        {
            "email": "tech.manager@evosmartuni.com",
            "role": "techmanager",
            "prenom": "Patrick",
            "nom": "Kabila",
            "telephone": "+243 81 500 0002",
            "fonction": "Responsable technique EvoSU",
        },
    ]
    created = []
    for item in seeds:
        if find_user_by_email(item["email"]):
            continue
        try:
            create_user({**item, "password": TECH_TEAM_PASSWORD})
            created.append(f"{item['email']} ({item['role']})")
        except ValueError as exc:
            print(f"[SAC] Seed tech ignoré ({item['email']}): {exc}")
        except Exception as exc:
            print(f"[SAC] Seed tech erreur ({item['email']}): {exc}")
    if created:
        print(
            "[SAC] Équipe tech créée:",
            ", ".join(created),
            "— mot de passe:",
            TECH_TEAM_PASSWORD,
        )
