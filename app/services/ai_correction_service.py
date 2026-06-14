import json
import re
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.database import get_db
from app.services import platform_service
from app.utils.guards import assert_same_campus
from app.utils.platform_security import uid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_submission(row) -> dict:
    if not row:
        return None
    rubric = json.loads(row["rubric_scores"] or "{}")
    used_reference = bool(rubric.pop("_usedReference", False))
    return {
        "id": row["id"],
        "studentEmail": row["student_email"],
        "studentName": row["student_name"],
        "studentMatricule": row["student_matricule"],
        "professorEmail": row["professor_email"],
        "universite": row["universite"],
        "filiere": row["filiere"],
        "niveau": row["niveau"],
        "courseCode": row["course_code"],
        "courseName": row["course_name"],
        "classe": row["classe"],
        "semester": row["semester"],
        "assignmentTitle": row["assignment_title"],
        "fileUrl": row["file_url"],
        "fileType": row["file_type"],
        "textContent": (row["text_content"] or "")[:500],
        "status": row["status"],
        "provisionalGrade": row["provisional_grade"],
        "finalGrade": row["final_grade"],
        "originalityScore": row["originality_score"],
        "aiComments": json.loads(row["ai_comments"] or "[]"),
        "aiStrengths": json.loads(row["ai_strengths"] or "[]"),
        "aiWeaknesses": json.loads(row["ai_weaknesses"] or "[]"),
        "rubricScores": rubric,
        "usedReference": used_reference,
        "professorComment": row["professor_comment"],
        "validatedBy": row["validated_by"],
        "validatedAt": row["validated_at"],
        "aiProgress": row["ai_progress"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _notify(email: str, role: str, ntype: str, title: str, message: str, submission_id: str, universite: str):
    get_db().execute(
        """INSERT INTO correction_notifications
           (id, recipient_email, recipient_role, type, title, message, submission_id, universite, read, created_at)
           VALUES (?,?,?,?,?,?,?,?,0,?)""",
        (uid("ntf"), email, role, ntype, title, message, submission_id, universite, _now()),
    )


def _norm_title(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _titles_match(student_title: str, ref_title: str) -> bool:
    a = _norm_title(student_title)
    b = _norm_title(ref_title)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _row_to_reference(row) -> dict | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "professorEmail": row["professor_email"],
        "universite": row["universite"],
        "courseCode": row["course_code"],
        "courseName": row["course_name"],
        "assignmentTitle": row["assignment_title"],
        "semester": row["semester"],
        "referenceText": (row["reference_text"] or "")[:800],
        "criteriaNotes": row["criteria_notes"],
        "fileUrl": row["file_url"],
        "fileName": row["file_name"],
        "fileType": row["file_type"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def find_reference(
    universite: str,
    course_code: str,
    assignment_title: str,
    professor_email: str | None = None,
    semester: str | None = None,
) -> dict | None:
    q = """SELECT * FROM correction_references WHERE universite = ? AND course_code = ?"""
    params: list = [universite, course_code]
    if semester:
        q += " AND semester = ?"
        params.append(semester)
    q += " ORDER BY updated_at DESC"
    rows = get_db().execute(q, params).fetchall()
    prof_lower = (professor_email or "").lower()
    for row in rows:
        if not _titles_match(assignment_title, row["assignment_title"]):
            continue
        row_prof = (row["professor_email"] or "").lower()
        if prof_lower and row_prof and row_prof != prof_lower:
            continue
        return _row_to_reference(row)
    return None


def save_reference(
    professor: dict,
    data: dict,
    file_path: str | None = None,
    file_url: str | None = None,
    file_name: str | None = None,
) -> dict:
    if professor.get("role") not in ("professeur", "universite"):
        raise ValueError("FORBIDDEN")
    ref_text = (data.get("referenceText") or data.get("textContent") or "").strip()
    if not ref_text and not file_path:
        raise ValueError("REFERENCE_REQUIRED")
    ref_id = uid("ref")
    now = _now()
    get_db().execute(
        """INSERT INTO correction_references (
          id, professor_email, universite, course_code, course_name, assignment_title,
          semester, reference_text, criteria_notes, file_url, file_path, file_name, file_type,
          created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ref_id,
            professor["email"],
            professor.get("universite"),
            data["courseCode"],
            data["courseName"],
            data["assignmentTitle"],
            data.get("semester", "s1-2025"),
            ref_text,
            data.get("criteriaNotes") or "",
            file_url,
            file_path,
            file_name,
            data.get("fileType", "text"),
            now,
            now,
        ),
    )
    get_db().commit()
    return _row_to_reference(
        get_db().execute("SELECT * FROM correction_references WHERE id = ?", (ref_id,)).fetchone()
    )


def list_references(professor_email: str, universite: str) -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM correction_references WHERE professor_email = ? COLLATE NOCASE
           AND universite = ? ORDER BY updated_at DESC""",
        (professor_email, universite),
    ).fetchall()
    return [_row_to_reference(r) for r in rows]


def delete_reference(professor: dict, reference_id: str) -> bool:
    if professor.get("role") not in ("professeur", "universite"):
        raise ValueError("FORBIDDEN")
    row = get_db().execute(
        "SELECT * FROM correction_references WHERE id = ?", (reference_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    assert_same_campus(professor, row["universite"])
    if (row["professor_email"] or "").lower() != (professor.get("email") or "").lower():
        if professor.get("role") != "universite":
            raise ValueError("FORBIDDEN")
    get_db().execute("DELETE FROM correction_references WHERE id = ?", (reference_id,))
    get_db().commit()
    return True


def _analyze_content(
    text: str,
    course_name: str,
    assignment_title: str,
    reference_text: str | None = None,
    criteria_notes: str | None = None,
) -> dict:
    """Moteur IA SAC (analyse heuristique + critères pédagogique + copie de référence)."""
    text = (text or "").strip()
    words = re.findall(r"\w+", text, re.UNICODE)
    word_count = len(words)
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    course_kw = set(re.findall(r"\w{4,}", (course_name + " " + assignment_title).lower()))
    content_lower = text.lower()
    keyword_hits = sum(1 for kw in course_kw if kw in content_lower and len(kw) > 4)
    keyword_ratio = min(1.0, keyword_hits / max(len(course_kw), 1))

    length_score = min(1.0, word_count / 400) if word_count else 0
    structure_score = min(1.0, (len(paragraphs) * 0.2 + len(sentences) * 0.05))
    relevance_score = keyword_ratio * 0.6 + 0.4 * min(1.0, keyword_hits / 5)

    rubric = {
        "contenu": round(min(20, 8 + relevance_score * 12), 1),
        "structure": round(min(20, 6 + structure_score * 14), 1),
        "argumentation": round(min(20, 7 + length_score * 10 + (0.1 if len(sentences) > 8 else 0)), 1),
        "originalite": round(min(20, 10 + min(1.0, word_count / 500) * 8), 1),
        "presentation": round(min(20, 8 + (0.15 * len(paragraphs))), 1),
    }
    avg_rubric = sum(rubric.values()) / len(rubric)
    provisional = round(min(20.0, max(0.0, avg_rubric)), 1)

    unique_ratio = len(set(words)) / max(word_count, 1)
    originality = round(min(99.0, max(55.0, 60 + unique_ratio * 35 + (10 if word_count > 200 else 0))), 1)

    strengths = []
    weaknesses = []
    comments = []

    if word_count >= 300:
        strengths.append("Volume de travail satisfaisant")
        comments.append("Le travail présente une longueur adaptée aux attentes académiques.")
    else:
        weaknesses.append("Développement insuffisant")
        comments.append("Le contenu gagnerait à être plus développé (introduction, corps, conclusion).")

    if len(paragraphs) >= 3:
        strengths.append("Bonne structuration en paragraphes")
    else:
        weaknesses.append("Structure à améliorer")
        comments.append("Organisez le travail en sections claires (introduction, développement, conclusion).")

    if keyword_ratio >= 0.4:
        strengths.append("Bonne compréhension du sujet")
        comments.append(f"Le vocabulaire lié à « {course_name} » est bien mobilisé.")
    else:
        weaknesses.append("Lien avec le cours à renforcer")
        comments.append("Approfondissez les concepts vus en cours et citez les notions clés.")

    if originality >= 85:
        strengths.append("Originalité élevée")
    elif originality < 70:
        weaknesses.append("Risque de similarité détecté")
        comments.append("Vérifiez la reformulation et les sources pour éviter le plagiat.")

    if provisional >= 14:
        comments.append("Travail de bonne qualité — prêt pour validation professorale.")
    elif provisional >= 10:
        comments.append("Travail acceptable avec des axes d'amélioration identifiés.")
    else:
        comments.append("Travail en dessous des attentes — révision recommandée avant validation.")

    if reference_text:
        ref = reference_text.strip()
        ref_words = set(re.findall(r"\w{4,}", ref.lower(), re.UNICODE))
        stu_words = set(re.findall(r"\w{4,}", content_lower, re.UNICODE))
        alignment = len(ref_words & stu_words) / max(len(ref_words), 1)
        ref_paras = len([p for p in ref.split("\n\n") if p.strip()])
        para_ratio = min(len(paragraphs), ref_paras) / max(ref_paras, 1) if ref_paras else 0.5

        ref_bonus = alignment * 3 + para_ratio * 2
        provisional = round(min(20.0, max(0.0, provisional + ref_bonus - 1.5)), 1)
        rubric["contenu"] = round(min(20, rubric["contenu"] + alignment * 4), 1)
        rubric["structure"] = round(min(20, rubric["structure"] + para_ratio * 3), 1)

        comments.insert(
            0,
            f"Évaluation alignée sur la copie de correction du professeur (similarité vocabulaire : {round(alignment * 100)} %).",
        )
        if alignment >= 0.35:
            strengths.append("Bon alignement avec le modèle de correction")
        else:
            weaknesses.append("Écart notable par rapport à la copie de référence")
            comments.append("Reprenez les éléments attendus dans la copie corrigée fournie par votre professeur.")

        expected_sections = []
        for label, pattern in (
            ("introduction", r"\bintroduction\b"),
            ("développement", r"\bdéveloppement\b|\bpartie\b"),
            ("conclusion", r"\bconclusion\b"),
        ):
            if re.search(pattern, ref.lower()):
                expected_sections.append(label)
        missing = [s for s in expected_sections if not re.search(
            r"\b" + s + r"\b", content_lower
        )]
        if missing:
            weaknesses.append(f"Sections attendues manquantes : {', '.join(missing)}")
        elif expected_sections:
            strengths.append("Structure conforme au modèle de correction")

    if criteria_notes:
        notes_lower = criteria_notes.lower()
        comments.append(f"Critères professeur pris en compte : {criteria_notes[:200]}{'…' if len(criteria_notes) > 200 else ''}")
        if "plagiat" in notes_lower and originality < 75:
            weaknesses.append("Attention au plagiat (critère professeur)")
        if "bibliographie" in notes_lower and "bibliograph" not in content_lower:
            weaknesses.append("Bibliographie absente ou insuffisante")

    return {
        "provisionalGrade": provisional,
        "originalityScore": originality,
        "aiComments": comments,
        "aiStrengths": strengths or ["Effort de rédaction constaté"],
        "aiWeaknesses": weaknesses or ["Aucun point critique majeur"],
        "rubricScores": rubric,
        "aiProgress": 100,
    }


def submit_work(student: dict, data: dict, file_path: str | None = None, file_url: str | None = None) -> dict:
    if student.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")
    sub_id = uid("wrk")
    now = _now()
    text = data.get("textContent") or data.get("text") or ""
    get_db().execute(
        """INSERT INTO work_submissions (
          id, student_email, student_name, student_matricule, professor_email, universite,
          filiere, niveau, course_code, course_name, classe, semester, assignment_title,
          file_url, file_path, file_type, text_content, status, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sub_id,
            student["email"],
            f"{student.get('prenom', '')} {student.get('nom', '')}".strip(),
            student.get("matricule"),
            data.get("professorEmail"),
            student.get("universite"),
            student.get("filiere"),
            student.get("niveau"),
            data["courseCode"],
            data["courseName"],
            data.get("classe"),
            data.get("semester", "s1-2025"),
            data["assignmentTitle"],
            file_url,
            file_path,
            data.get("fileType", "text"),
            text,
            "correction_ia",
            now,
            now,
        ),
    )
    get_db().commit()
    return run_ai_analysis(sub_id)


def run_ai_analysis(submission_id: str) -> dict:
    row = get_db().execute(
        "SELECT * FROM work_submissions WHERE id = ?", (submission_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")

    get_db().execute(
        "UPDATE work_submissions SET status='correction_ia', ai_progress=50, updated_at=? WHERE id=?",
        (_now(), submission_id),
    )
    get_db().commit()

    reference = find_reference(
        row["universite"],
        row["course_code"],
        row["assignment_title"],
        row["professor_email"],
        row["semester"],
    )
    analysis = _analyze_content(
        row["text_content"] or "",
        row["course_name"],
        row["assignment_title"],
        reference["referenceText"] if reference else None,
        reference.get("criteriaNotes") if reference else None,
    )
    rubric_payload = dict(analysis["rubricScores"])
    if reference:
        rubric_payload["_usedReference"] = True

    now = _now()
    get_db().execute(
        """UPDATE work_submissions SET status='note_provisoire', provisional_grade=?, originality_score=?,
           ai_comments=?, ai_strengths=?, ai_weaknesses=?, rubric_scores=?, ai_progress=100, updated_at=?
           WHERE id=?""",
        (
            analysis["provisionalGrade"],
            analysis["originalityScore"],
            json.dumps(analysis["aiComments"]),
            json.dumps(analysis["aiStrengths"]),
            json.dumps(analysis["aiWeaknesses"]),
            json.dumps(rubric_payload),
            now,
            submission_id,
        ),
    )
    if row["professor_email"]:
        _notify(
            row["professor_email"],
            "professeur",
            "correction_ready",
            "Travail à valider",
            f"Note provisoire {analysis['provisionalGrade']}/20 — {row['assignment_title']} ({row['student_name']})",
            submission_id,
            row["universite"],
        )
    get_db().commit()
    return get_submission(submission_id)


def list_for_student(email: str) -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM work_submissions WHERE student_email = ? COLLATE NOCASE ORDER BY created_at DESC",
        (email,),
    ).fetchall()
    return [_row_to_submission(r) for r in rows]


def list_pending_for_professor(email: str, universite: str) -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM work_submissions WHERE universite = ?
           AND status = 'note_provisoire'
           AND (professor_email = ? COLLATE NOCASE OR professor_email IS NULL OR professor_email = '')
           ORDER BY created_at DESC""",
        (universite, email),
    ).fetchall()
    return [_row_to_submission(r) for r in rows]


def list_pending_for_campus(universite: str) -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM work_submissions WHERE universite = ?
           AND status = 'note_provisoire'
           ORDER BY created_at DESC""",
        (universite,),
    ).fetchall()
    return [_row_to_submission(r) for r in rows]


def list_for_professor(email: str, universite: str) -> list[dict]:
    rows = get_db().execute(
        """SELECT * FROM work_submissions WHERE universite = ?
           AND (professor_email = ? COLLATE NOCASE OR professor_email IS NULL)
           ORDER BY updated_at DESC LIMIT 100""",
        (universite, email),
    ).fetchall()
    return [_row_to_submission(r) for r in rows]


def get_submission(submission_id: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM work_submissions WHERE id = ?", (submission_id,)
    ).fetchone()
    return _row_to_submission(row)


def validate_submission(actor: dict, submission_id: str, data: dict) -> dict:
    role = actor.get("role")
    if role not in ("professeur", "universite", "assistant"):
        raise ValueError("FORBIDDEN")
    row = get_db().execute(
        "SELECT * FROM work_submissions WHERE id = ?", (submission_id,)
    ).fetchone()
    if not row:
        raise ValueError("NOT_FOUND")
    assert_same_campus(actor, row["universite"])
    if role == "professeur":
        prof_email = (row["professor_email"] or "").lower()
        if prof_email and prof_email != (actor.get("email") or "").lower():
            raise ValueError("FORBIDDEN")
    if row["status"] != "note_provisoire":
        raise ValueError("INVALID_STATUS")

    action = data.get("action", "validate")
    if action == "reject":
        get_db().execute(
            """UPDATE work_submissions SET status='rejete', professor_comment=?, validated_by=?,
               validated_at=?, updated_at=? WHERE id=?""",
            (data.get("comment", ""), actor["email"], _now(), _now(), submission_id),
        )
        _notify(
            row["student_email"],
            "etudiant",
            "work_rejected",
            "Travail rejeté",
            f"Votre travail « {row['assignment_title']} » a été rejeté. Consultez les commentaires.",
            submission_id,
            row["universite"],
        )
        get_db().commit()
        return get_submission(submission_id)

    final_grade = float(data.get("finalGrade", row["provisional_grade"] or 0))
    final_grade = max(0, min(20, round(final_grade, 1)))
    now = _now()

    get_db().execute(
        """UPDATE work_submissions SET status='valide', final_grade=?, professor_comment=?,
           validated_by=?, validated_at=?, updated_at=? WHERE id=?""",
        (final_grade, data.get("comment", ""), actor["email"], now, now, submission_id),
    )

    grade_row = platform_service.upsert_grade_from_ai_validation(
        dict(row), final_grade, actor["email"]
    )

    _notify(
        row["student_email"],
        "etudiant",
        "grade_validated",
        "Note finale validée",
        f"Note {final_grade}/20 pour « {row['assignment_title']} » — relevé mis à jour (moyenne {grade_row['avg']}/20).",
        submission_id,
        row["universite"],
    )
    validator_label = "assistant" if role == "assistant" else "professeur"
    _notify(
        row["professor_email"] or actor["email"],
        "professeur",
        "grade_recorded",
        "Cote enregistrée",
        f"Fiche de cote mise à jour par {validator_label} — {row['course_name']} — {row['student_name']}: {final_grade}/20",
        submission_id,
        row["universite"],
    )
    if role == "assistant":
        _notify(
            actor["email"],
            "assistant",
            "grade_validated",
            "Validation enregistrée",
            f"Travail « {row['assignment_title']} » validé — cote transmise au relevé.",
            submission_id,
            row["universite"],
        )
    get_db().commit()
    return get_submission(submission_id)


def get_course_sheet(universite: str, course_code: str, semester: str) -> dict:
    rows = get_db().execute(
        """SELECT * FROM work_submissions WHERE universite=? AND course_code=? AND semester=?
           AND status='valide' ORDER BY student_name""",
        (universite, course_code, semester),
    ).fetchall()
    students = [
        {
            "matricule": r["student_matricule"],
            "name": r["student_name"],
            "provisionalGrade": r["provisional_grade"],
            "finalGrade": r["final_grade"],
            "assignmentTitle": r["assignment_title"],
        }
        for r in rows
    ]
    grades = [s["finalGrade"] for s in students if s["finalGrade"] is not None]
    avg = round(sum(grades) / len(grades), 1) if grades else 0
    return {"courseCode": course_code, "semester": semester, "students": students, "classAverage": avg, "count": len(students)}


def get_class_stats(universite: str, classe: str | None = None) -> dict:
    q = "SELECT final_grade FROM work_submissions WHERE universite=? AND status='valide'"
    params: list = [universite]
    if classe:
        q += " AND classe=?"
        params.append(classe)
    rows = get_db().execute(q, params).fetchall()
    grades = [r["final_grade"] for r in rows if r["final_grade"] is not None]
    if not grades:
        return {"totalStudents": 0, "average": 0, "passRate": 0, "distribution": []}

    avg = round(sum(grades) / len(grades), 1)
    pass_rate = round(100 * sum(1 for g in grades if g >= 10) / len(grades), 1)
    buckets = {"0-9": 0, "10-11": 0, "12-13": 0, "14-15": 0, "16-20": 0}
    for g in grades:
        if g < 10:
            buckets["0-9"] += 1
        elif g < 12:
            buckets["10-11"] += 1
        elif g < 14:
            buckets["12-13"] += 1
        elif g < 16:
            buckets["14-15"] += 1
        else:
            buckets["16-20"] += 1
    distribution = [
        {"range": k, "count": v, "percent": round(100 * v / len(grades), 1)}
        for k, v in buckets.items()
        if v > 0
    ]
    return {
        "totalStudents": len(grades),
        "average": avg,
        "passRate": pass_rate,
        "distribution": distribution,
    }


def get_notifications(email: str) -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM correction_notifications WHERE recipient_email = ? COLLATE NOCASE ORDER BY created_at DESC LIMIT 50",
        (email,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "title": r["title"],
            "message": r["message"],
            "submissionId": r["submission_id"],
            "read": bool(r["read"]),
            "createdAt": r["created_at"],
        }
        for r in rows
    ]
