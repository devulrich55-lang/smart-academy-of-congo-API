import re
import unicodedata


def _norm(s: str | None) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def student_sees_document(student: dict | None, doc: dict | None) -> bool:
    if not student or not doc:
        return False

    if doc.get("source") == "administration":
        if doc.get("universite") and doc["universite"] != student.get("universite"):
            return False
        if doc.get("audienceType") == "section":
            if not doc.get("filiere") or not student.get("filiere"):
                return False
            sf, df = _norm(student["filiere"]), _norm(doc["filiere"])
            return sf == df or sf in df or df in sf
        return True

    if doc.get("source") not in ("professeur", "assistant"):
        return False
    if doc.get("audienceType") and doc["audienceType"] != "ma_classe":
        return False
    if doc.get("universite") and doc["universite"] != student.get("universite"):
        return False
    if doc.get("niveau") and doc["niveau"] != student.get("niveau"):
        return False
    sf, df = _norm(student.get("filiere")), _norm(doc.get("filiere"))
    if df and sf and sf not in df and df not in sf:
        return False
    return True


SOURCE_BY_ROLE = {
    "professeur": "professeur",
    "assistant": "assistant",
    "universite": "administration",
}
