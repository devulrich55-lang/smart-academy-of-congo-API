import json
import re
import unicodedata
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.utils.sanitize import clean_text

_WORD_RE = re.compile(r"^[\w' -]{1,80}$", re.UNICODE)

LANGUAGES = {
    "fr": {"id": "fr", "label": "Français", "native": "Français"},
    "en": {"id": "en", "label": "Anglais", "native": "English"},
    "es": {"id": "es", "label": "Espagnol", "native": "Español"},
    "ln": {"id": "ln", "label": "Lingala", "native": "Lingála"},
    "lua": {"id": "lua", "label": "Tshiluba", "native": "Tshiluba"},
}

_FRENCH_HINTS = frozenset(
    {
        "le", "la", "les", "un", "une", "des", "et", "ou", "de", "du", "au", "aux",
        "je", "tu", "il", "elle", "nous", "vous", "avec", "sans", "pour", "dans",
        "école", "livre", "étudiant", "professeur", "université", "bibliothèque", "bonjour",
    }
)
_SPANISH_HINTS = frozenset(
    {
        "el", "la", "los", "las", "un", "una", "y", "o", "de", "del", "hola", "gracias",
        "escuela", "libro", "estudiante", "profesor", "universidad", "biblioteca",
    }
)
_LINGALA_HINTS = frozenset(
    {"mbote", "malamu", "libota", "mobali", "mwasi", "eteyi", "ndako", "moninga", "bolingo"}
)
_TSHILUBA_HINTS = frozenset(
    {"moyo", "diaku", "tshimuna", "mutekela", "dibuku", "tshikondo", "muaku"}
)

LOCAL_GLOSSARY: list[tuple[str, str, str, str]] = [
    ("livre", "fr", "en", "book"),
    ("book", "en", "fr", "livre"),
    ("école", "fr", "en", "school"),
    ("school", "en", "fr", "école"),
    ("bonjour", "fr", "en", "hello"),
    ("hello", "en", "fr", "bonjour"),
    ("libro", "es", "fr", "livre"),
    ("livre", "fr", "es", "libro"),
    ("escuela", "es", "fr", "école"),
    ("école", "fr", "es", "escuela"),
    ("hola", "es", "fr", "bonjour"),
    ("bonjour", "fr", "es", "hola"),
    ("mbote", "ln", "fr", "bonjour"),
    ("bonjour", "fr", "ln", "mbote"),
    ("malamu", "ln", "fr", "bien"),
    ("bien", "fr", "ln", "malamu"),
    ("eteyi", "ln", "fr", "école"),
    ("école", "fr", "ln", "eteyi"),
    ("ndako", "ln", "fr", "maison"),
    ("maison", "fr", "ln", "ndako"),
    ("moninga", "ln", "fr", "ami"),
    ("ami", "fr", "ln", "moninga"),
    ("moyo", "lua", "fr", "vie"),
    ("vie", "fr", "lua", "moyo"),
    ("diaku", "lua", "fr", "ami"),
    ("ami", "fr", "lua", "diaku"),
    ("dibuku", "lua", "fr", "livre"),
    ("livre", "fr", "lua", "dibuku"),
    ("tshikondo", "lua", "fr", "école"),
    ("école", "fr", "lua", "tshikondo"),
]


def list_languages() -> list[dict]:
    return list(LANGUAGES.values())


def _fetch_json(url: str, timeout: float = 8.0) -> dict | list | None:
    req = Request(url, headers={"User-Agent": "SmartAcademyCongo/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def _post_json(url: str, payload: dict, timeout: float = 8.0) -> dict | None:
    body = urlencode(payload).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "User-Agent": "SmartAcademyCongo/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def _normalize_key(word: str) -> str:
    return (
        unicodedata.normalize("NFD", word.strip().lower())
        .encode("ascii", "ignore")
        .decode("ascii")
    )


def _resolve_lang(code: str | None) -> str | None:
    if not code:
        return None
    clean = clean_text(code, 8).lower()
    if clean in ("auto", "detect", "any"):
        return None
    if clean not in LANGUAGES:
        raise ValueError("INVALID_LANG")
    return clean


def _detect_source_lang(word: str) -> str:
    key = _normalize_key(word)
    if key in _LINGALA_HINTS:
        return "ln"
    if key in _TSHILUBA_HINTS:
        return "lua"
    if re.search(r"[àâäéèêëïîôùûüçœæ]", word, re.I):
        return "fr"
    if re.search(r"[ñáéíóúü]", word, re.I) or key in _SPANISH_HINTS:
        return "es"
    if key in _FRENCH_HINTS or word.strip().endswith(("tion", "ment", "eux", "euse", "ique")):
        return "fr"
    if word.strip().endswith(("ción", "ado", "ada", "mente")):
        return "es"
    return "en"


def _default_target(source: str) -> str:
    order = ["fr", "en", "es", "ln", "lua"]
    for lang in order:
        if lang != source:
            return lang
    return "en"


def _lookup_local(word: str, source: str, target: str) -> tuple[str, list[str]]:
    key = _normalize_key(word)
    for entry, src, tgt, translation in LOCAL_GLOSSARY:
        if _normalize_key(entry) == key and src == source and tgt == target:
            return translation, []
    for entry, src, tgt, translation in LOCAL_GLOSSARY:
        if _normalize_key(entry) == key and src == target and tgt == source:
            return translation, []
    return "", []


def _translate_mymemory(word: str, source: str, target: str) -> tuple[str, list[str]]:
    pair = f"{source}|{target}"
    url = (
        "https://api.mymemory.translated.net/get?q="
        + quote(word)
        + "&langpair="
        + quote(pair)
    )
    data = _fetch_json(url)
    if not isinstance(data, dict):
        return "", []

    response = data.get("responseData") or {}
    translation = clean_text(response.get("translatedText"), 200)
    if not translation or translation.upper() == word.upper():
        translation = ""
    if translation and "INVALID LANGUAGE PAIR" in translation.upper():
        return "", []

    alternatives: list[str] = []
    for match in data.get("matches") or []:
        if not isinstance(match, dict):
            continue
        alt = clean_text(match.get("translation"), 120)
        if alt and alt.lower() != translation.lower() and alt not in alternatives:
            alternatives.append(alt)
        if len(alternatives) >= 4:
            break
    return translation, alternatives


def _translate_libre(word: str, source: str, target: str) -> str:
    if source not in {"fr", "en", "es"} or target not in {"fr", "en", "es"}:
        return ""
    data = _post_json(
        "https://libretranslate.com/translate",
        {"q": word, "source": source, "target": target, "format": "text"},
    )
    if not data:
        return ""
    return clean_text(data.get("translatedText"), 200)


def _translate(word: str, source: str, target: str) -> tuple[str, list[str], str]:
    translation, alternatives = _translate_mymemory(word, source, target)
    provider = "mymemory" if translation else ""
    if not translation:
        translation = _translate_libre(word, source, target)
        provider = "libretranslate" if translation else ""
    if not translation:
        translation, alternatives = _lookup_local(word, source, target)
        provider = "local" if translation else ""
    return translation, alternatives, provider


def _english_details(word: str) -> tuple[str, list[dict]]:
    data = _fetch_json(
        "https://api.dictionaryapi.dev/api/v2/entries/en/" + quote(word.lower())
    )
    if not isinstance(data, list) or not data:
        return "", []

    entry = data[0] if isinstance(data[0], dict) else {}
    phonetic = clean_text(entry.get("phonetic"), 40)
    meanings_out: list[dict] = []

    for meaning in entry.get("meanings") or []:
        if not isinstance(meaning, dict):
            continue
        part = clean_text(meaning.get("partOfSpeech"), 40)
        defs: list[str] = []
        for definition in meaning.get("definitions") or []:
            if not isinstance(definition, dict):
                continue
            text = clean_text(definition.get("definition"), 280)
            if text:
                defs.append(text)
            if len(defs) >= 2:
                break
        if defs:
            meanings_out.append({"partOfSpeech": part, "definitions": defs})
        if len(meanings_out) >= 3:
            break

    return phonetic, meanings_out


def lookup(word: str, source_lang: str | None = None, target_lang: str | None = None) -> dict:
    clean_word = clean_text(word, 80).strip()
    if not clean_word:
        raise ValueError("INVALID_INPUT")
    if not _WORD_RE.match(clean_word):
        raise ValueError("INVALID_INPUT")

    source = _resolve_lang(source_lang)
    target = _resolve_lang(target_lang)

    if source is None:
        source = _detect_source_lang(clean_word)
    if target is None:
        target = _default_target(source)
    if source == target:
        raise ValueError("INVALID_LANG")

    translation, alternatives, provider = _translate(clean_word, source, target)

    phonetic = ""
    meanings: list[dict] = []
    if source == "en":
        phonetic, meanings = _english_details(clean_word)

    if not translation:
        raise ValueError("NOT_FOUND")

    return {
        "ok": True,
        "query": clean_word,
        "sourceLang": source,
        "targetLang": target,
        "sourceLabel": LANGUAGES[source]["label"],
        "targetLabel": LANGUAGES[target]["label"],
        "translation": translation,
        "phonetic": phonetic,
        "meanings": meanings,
        "alternatives": alternatives,
        "provider": provider,
    }
