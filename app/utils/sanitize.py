import re
import unicodedata

import bleach
from email_validator import EmailNotValidError, validate_email

DISPOSABLE_DOMAINS = {
    "mailinator.com",
    "guerrillamail.com",
    "tempmail.com",
    "yopmail.com",
    "10minutemail.com",
}
FAKE_EMAIL_LOCAL = re.compile(r"^(test|fake|faux|demo|noreply|xxx|null|asdf|qwerty|123)$", re.I)


def clean_text(value: str | None, max_len: int = 5000) -> str:
    if not isinstance(value, str):
        return ""
    return bleach.clean(value.strip()[:max_len], tags=[], strip=True)


def clean_email(email: str | None) -> str | None:
    if not email:
        return None
    try:
        result = validate_email(email.strip(), check_deliverability=False)
        return result.normalized.lower()
    except EmailNotValidError:
        return None


def clean_role(role: str | None) -> str | None:
    allowed = {
        "etudiant",
        "professeur",
        "assistant",
        "universite",
        "section",
        "ministere",
        "superadmin",
        "developpeur",
        "techmanager",
    }
    return role if role in allowed else None


def clean_institutional_role(role: str | None) -> str | None:
    allowed = {"superadmin", "ministere", "universite", "developpeur", "techmanager"}
    return role if role in allowed else None


def clean_niveau(n: str | None) -> str | None:
    allowed = {"l1", "l2", "l3", "master1", "master2", "doctorat"}
    return n if n in allowed else None


def clean_media_category(m: str | None) -> str:
    allowed = {"info", "document", "image", "audio", "video"}
    return m if m in allowed else "document"


def clean_reaction_type(t: str | None) -> str | None:
    return t if t in {"useful", "question", "thanks"} else None


def validate_password(password: str | None) -> bool:
    if not isinstance(password, str):
        return False
    if len(password) < 8 or len(password) > 128:
        return False
    if not re.search(r"[a-zA-Z]", password) or not re.search(r"[0-9]", password):
        return False
    return " " not in password


def clean_phone(phone: str | None) -> str | None:
    d = re.sub(r"\D", "", str(phone or ""))
    if d.startswith("243") and len(d) >= 12:
        d = d[:12]
    elif d.startswith("00243"):
        d = d[2:14]
    elif d.startswith("0") and len(d) >= 10:
        d = "243" + d[1:10]
    elif len(d) == 9:
        d = "243" + d
    if len(d) != 12 or not d.startswith("243"):
        return None
    local = d[3:]
    if not re.match(r"^[89][0-9]{8}$", local):
        return None
    if re.match(r"^(\d)\1{8}$", local):
        return None
    return d


def validate_email_strict(email: str | None) -> str | None:
    e = clean_email(email)
    if not e:
        return None
    local, domain = e.split("@", 1)
    if FAKE_EMAIL_LOCAL.match(local) or domain in DISPOSABLE_DOMAINS:
        return None
    return e


def format_full_name(prenom: str | None, nom: str | None) -> str:
    p = (prenom or "").strip()
    n = (nom or "").strip()
    if not p and not n:
        return ""
    if not p:
        return n
    if not n:
        return p
    if n.lower() == p.lower() or n.lower().startswith(p.lower() + " "):
        return n
    return f"{p} {n}"


def get_display_name(user: dict | None) -> str:
    if not user:
        return ""
    if user.get("nomUniversite"):
        return user["nomUniversite"]
    return format_full_name(user.get("prenom"), user.get("nom")) or user.get("email", "")


def norm_person_key(prenom: str | None, nom: str | None) -> str:
    def n(s: str | None) -> str:
        s = (s or "").strip().lower()
        s = unicodedata.normalize("NFD", s)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        return re.sub(r"\s+", " ", s)

    return f"{n(prenom)}|{n(nom)}"


def validate_person_name_text(name: str | None, min_len: int = 2) -> bool:
    v = (name or "").strip()
    if len(v) < min_len or len(v) > 80:
        return False
    if re.search(r"[0-9@<>]", v):
        return False
    compact = re.sub(r"\s", "", v)
    if compact and re.match(r"^(.)\1{4,}$", compact, re.I):
        return False
    return True
