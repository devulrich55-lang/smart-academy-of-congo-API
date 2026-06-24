import unicodedata

from app.database import get_db
from app.utils.campus_catalog import same_campus


def _norm(s: str | None) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _norm_niveau(n: str | None) -> str:
    x = _norm(n)
    if not x:
        return ""
    if x == "l1" or "licence 1" in x or "premiere licence" in x or "1ere licence" in x:
        return "l1"
    if x == "l2" or "licence 2" in x or "deuxieme licence" in x:
        return "l2"
    if x == "l3" or "licence 3" in x or "troisieme licence" in x:
        return "l3"
    if "master 1" in x or x in ("master1", "m1"):
        return "master1"
    if "master 2" in x or x in ("master2", "m2"):
        return "master2"
    if "doctorat" in x or x == "phd":
        return "doctorat"
    return x.replace(" ", "")


def _niveau_match(student_niveau: str | None, doc_niveau: str | None) -> bool:
    a = _norm_niveau(student_niveau)
    b = _norm_niveau(doc_niveau)
    if not a or not b:
        return True
    return a == b


def _universite_match(student_uni: str | None, doc_uni: str | None) -> bool:
    if not doc_uni:
        return True
    if not student_uni:
        return False
    return same_campus(student_uni, doc_uni)


def _filiere_match(student_filiere: str | None, doc_filiere: str | None) -> bool:
    sf, df = _norm(student_filiere), _norm(doc_filiere)
    if not df:
        return True
    if not sf:
        return False
    if sf == df or sf in df or df in sf:
        return True
    sf_tokens = [t for t in sf.replace("—", " ").replace("-", " ").split() if len(t) > 3]
    df_tokens = [t for t in df.replace("—", " ").replace("-", " ").split() if len(t) > 3]
    return any(t in df for t in sf_tokens) or any(t in sf for t in df_tokens)


def _classe_match(student_classe: str | None, doc_classe: str | None) -> bool:
    sc, dc = _norm(student_classe), _norm(doc_classe)
    if not dc:
        return True
    if not sc:
        return True
    return sc == dc or sc in dc or dc in sc


def _student_section_id(student: dict) -> str | None:
    return student.get("sectionId") or student.get("section_id")


def _section_filiere(section_id: str) -> str | None:
    row = get_db().execute(
        "SELECT filiere FROM faculty_sections WHERE id = ?", (section_id,)
    ).fetchone()
    return row["filiere"] if row else None


def _teaching_audience(doc: dict) -> bool:
    audience = _norm(doc.get("audienceType") or "ma_classe")
    return not audience or audience in ("ma_classe", "class", "classe")


def student_sees_document(student: dict | None, doc: dict | None) -> bool:
    if not student or not doc:
        return False

    if doc.get("source") == "administration":
        if doc.get("universite") and not _universite_match(
            student.get("universite"), doc["universite"]
        ):
            return False
        audience = _norm(doc.get("audienceType") or "campus")
        if audience == "section":
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
    if not _teaching_audience(doc):
        return False
    if doc.get("universite") and not _universite_match(
        student.get("universite"), doc["universite"]
    ):
        return False
    if not _niveau_match(student.get("niveau"), doc.get("niveau")):
        return False
    if not _filiere_match(student.get("filiere"), doc.get("filiere")):
        return False
    return _classe_match(student.get("classe"), doc.get("classe"))


SOURCE_BY_ROLE = {
    "professeur": "professeur",
    "assistant": "assistant",
    "universite": "administration",
    "section": "administration",
}
