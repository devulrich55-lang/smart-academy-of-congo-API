import unicodedata

from app.database import get_db


def _norm(s: str | None) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _filiere_match(student_filiere: str | None, doc_filiere: str | None) -> bool:
    sf, df = _norm(student_filiere), _norm(doc_filiere)
    if not df or not sf:
        return False
    return sf == df or sf in df or df in sf


def _classe_match(student_classe: str | None, doc_classe: str | None) -> bool:
    sc, dc = _norm(student_classe), _norm(doc_classe)
    if not dc:
        return True
    if not sc:
        return False
    return sc == dc or sc in dc or dc in sc


def _student_section_id(student: dict) -> str | None:
    return student.get("sectionId") or student.get("section_id")


def _section_filiere(section_id: str) -> str | None:
    row = get_db().execute(
        "SELECT filiere FROM faculty_sections WHERE id = ?", (section_id,)
    ).fetchone()
    return row["filiere"] if row else None


def student_sees_document(student: dict | None, doc: dict | None) -> bool:
    if not student or not doc:
        return False

    if doc.get("source") == "administration":
        if doc.get("universite") and doc["universite"] != student.get("universite"):
            return False
        if doc.get("audienceType") == "section":
            doc_sid = doc.get("sectionId")
            student_sid = _student_section_id(student)
            if doc_sid:
                return bool(student_sid and student_sid == doc_sid)
            if student_sid:
                sec_filiere = _section_filiere(student_sid)
                if sec_filiere and doc.get("filiere"):
                    return _filiere_match(sec_filiere, doc["filiere"])
            return _filiere_match(student.get("filiere"), doc.get("filiere"))
        return True

    if doc.get("source") not in ("professeur", "assistant"):
        return False
    if doc.get("audienceType") and doc["audienceType"] != "ma_classe":
        return False
    if doc.get("universite") and doc["universite"] != student.get("universite"):
        return False
    if not doc.get("niveau") or not student.get("niveau"):
        return False
    if doc["niveau"] != student["niveau"]:
        return False
    if not _filiere_match(student.get("filiere"), doc.get("filiere")):
        return False
    return _classe_match(student.get("classe"), doc.get("classe"))


SOURCE_BY_ROLE = {
    "professeur": "professeur",
    "assistant": "assistant",
    "universite": "administration",
}
