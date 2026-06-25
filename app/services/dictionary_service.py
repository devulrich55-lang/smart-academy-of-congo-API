import json
import re
import unicodedata
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.utils.sanitize import clean_text

_WORD_RE = re.compile(r"^[a-zA-ZÀ-ÿ' -]{1,64}$")
_FRENCH_HINTS = frozenset(
    {
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "et",
        "ou",
        "de",
        "du",
        "au",
        "aux",
        "je",
        "tu",
        "il",
        "elle",
        "nous",
        "vous",
        "ils",
        "elles",
        "avec",
        "sans",
        "pour",
        "dans",
        "sur",
        "sous",
        "école",
        "livre",
        "étudiant",
        "professeur",
        "université",
        "bibliothèque",
    }
)


def _fetch_json(url: str, timeout: float = 8.0) -> dict | list | None:
    req = Request(url, headers={"User-Agent": "SmartAcademyCongo/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def _normalize_key(word: str) -> str:
    return (
        unicodedata.normalize("NFD", word.strip().lower())
        .encode("ascii", "ignore")
        .decode("ascii")
    )


def _detect_source_lang(word: str) -> str:
    if re.search(r"[àâäéèêëïîôùûüçœæ]", word, re.I):
        return "fr"
    key = _normalize_key(word)
    if key in _FRENCH_HINTS:
        return "fr"
    if word.strip().endswith(("tion", "ment", "eux", "euse", "ique")):
        return "fr"
    return "en"


def _translate(word: str, source: str, target: str) -> tuple[str, list[str]]:
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


def lookup(word: str) -> dict:
    clean_word = clean_text(word, 80).strip()
    if not clean_word:
        raise ValueError("INVALID_INPUT")
    if not _WORD_RE.match(clean_word):
        raise ValueError("INVALID_INPUT")

    source = _detect_source_lang(clean_word)
    target = "fr" if source == "en" else "en"
    translation, alternatives = _translate(clean_word, source, target)

    phonetic = ""
    meanings: list[dict] = []
    if source == "en":
        phonetic, meanings = _english_details(clean_word)

    if not translation:
        reverse = "en" if source == "fr" else "fr"
        translation, alternatives = _translate(clean_word, reverse, source)
        if translation:
            source, target = reverse, source

    if not translation:
        raise ValueError("NOT_FOUND")

    return {
        "ok": True,
        "query": clean_word,
        "sourceLang": source,
        "targetLang": target,
        "translation": translation,
        "phonetic": phonetic,
        "meanings": meanings,
        "alternatives": alternatives,
    }
