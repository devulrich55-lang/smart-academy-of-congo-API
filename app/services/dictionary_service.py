import json
import re
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.config import settings
from app.utils.sanitize import clean_text

_WORD_RE = re.compile(r"^[\w' -]{1,80}$", re.UNICODE)
_WIKI_UA = "SmartAcademyCongo/1.0 (https://smart-academy-of-congo.onrender.com; dictionnaire)"

LANGUAGES = {
    "fr": {"id": "fr", "label": "Français", "native": "Français"},
    "en": {"id": "en", "label": "Anglais", "native": "English"},
    "es": {"id": "es", "label": "Espagnol", "native": "Español"},
    "ln": {"id": "ln", "label": "Lingala", "native": "Lingála"},
    "lua": {"id": "lua", "label": "Tshiluba", "native": "Tshiluba"},
}

_WIKI_SITE = {
    "fr": ("fr", "fr"),
    "en": ("en", "en"),
    "es": ("es", "es"),
    "ln": ("fr", "ln"),
    "lua": ("fr", "lua"),
}

_PONS_LANG = {
    "fr": ("fren", "fr"),
    "en": ("fren", "en"),
    "es": ("esfr", "es"),
}

LOCAL_ENTRIES: dict[tuple[str, str], dict] = {
    ("ln", "mbote"): {
        "phonetic": "",
        "meanings": [
            {
                "partOfSpeech": "interjection",
                "definitions": [
                    {
                        "text": "Salutation : bonjour, bonsoir.",
                        "example": "Mbote mingi !",
                    }
                ],
            }
        ],
        "synonyms": ["malamu"],
    },
    ("ln", "eteyi"): {
        "phonetic": "",
        "meanings": [
            {
                "partOfSpeech": "nom",
                "definitions": [
                    {
                        "text": "Lieu où l'on enseigne ; école.",
                        "example": "Eteyi ya université.",
                    }
                ],
            }
        ],
        "synonyms": ["kelasi"],
    },
    ("lua", "moyo"): {
        "phonetic": "",
        "meanings": [
            {
                "partOfSpeech": "nom",
                "definitions": [
                    {"text": "Vie, existence.", "example": "Moyo wa ngwej."}
                ],
            }
        ],
        "synonyms": [],
    },
    ("lua", "diaku"): {
        "phonetic": "",
        "meanings": [
            {
                "partOfSpeech": "nom",
                "definitions": [
                    {"text": "Ami, compagnon.", "example": "Diaku dianyi."}
                ],
            }
        ],
        "synonyms": [],
    },
}


def list_languages() -> list[dict]:
    return list(LANGUAGES.values())


def _fetch_json(url: str, timeout: float = 12.0, headers: dict | None = None) -> dict | list | None:
    base_headers = {"User-Agent": _WIKI_UA, "Accept": "application/json"}
    if headers:
        base_headers.update(headers)
    req = Request(url, headers=base_headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return clean_text(text, 500)


def _parse_pons_response(data: list | dict, source_lang: str) -> tuple[str, list[dict], list[str]]:
    blocks = data if isinstance(data, list) else []
    meanings_out: list[dict] = []
    synonyms: list[str] = []
    phonetic = ""

    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("lang") and block.get("lang") != source_lang:
            continue
        for hit in block.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            for rom in hit.get("roms") or []:
                if not isinstance(rom, dict):
                    continue
                part = _strip_html(rom.get("wordclass") or "définition")
                full = rom.get("headword_full") or ""
                phon_match = re.search(r"\[([^\]]+)\]", full)
                if phon_match and not phonetic:
                    phonetic = clean_text(phon_match.group(1), 40)
                defs: list[dict] = []
                for arab in rom.get("arabs") or []:
                    if not isinstance(arab, dict):
                        continue
                    header = _strip_html(arab.get("header") or "")
                    for tr in arab.get("translations") or []:
                        if not isinstance(tr, dict):
                            continue
                        target = _strip_html(tr.get("target") or "")
                        source = _strip_html(tr.get("source") or "")
                        text = header or source or target
                        if target and header:
                            text = header
                            example = target
                        elif target:
                            text = target
                            example = ""
                        else:
                            example = ""
                        if text:
                            defs.append({"text": text, "example": example})
                        if len(defs) >= 6:
                            break
                    if len(defs) >= 6:
                        break
                if defs:
                    meanings_out.append({"partOfSpeech": part, "definitions": defs})
                if len(meanings_out) >= 5:
                    break
            if len(meanings_out) >= 5:
                break
        if meanings_out:
            break

    return phonetic, meanings_out, synonyms[:8]


def _lookup_pons(word: str, lang: str) -> tuple[str, list[dict], list[str], str]:
    secret = settings.pons_api_secret
    if not secret or lang not in _PONS_LANG:
        return "", [], [], ""

    pair, source_lang = _PONS_LANG[lang]
    url = (
        "https://api.pons.com/v1/dictionary?q="
        + quote(word)
        + f"&l={pair}&in={source_lang}&ref=true&language={source_lang}"
    )
    data = _fetch_json(url, headers={"X-Secret": secret})
    phonetic, meanings, synonyms = _parse_pons_response(data or [], source_lang)
    if meanings:
        return phonetic, meanings, synonyms, "pons"

    url_broad = (
        "https://api.pons.com/v1/dictionary?q="
        + quote(word)
        + f"&l={pair}&ref=true&language={source_lang}"
    )
    data = _fetch_json(url_broad, headers={"X-Secret": secret})
    phonetic, meanings, synonyms = _parse_pons_response(data or [], source_lang)
    if meanings:
        return phonetic, meanings, synonyms, "pons"
    return "", [], [], ""


def _normalize_key(word: str) -> str:
    return (
        unicodedata.normalize("NFD", word.strip().lower())
        .encode("ascii", "ignore")
        .decode("ascii")
    )


def _resolve_lang(code: str | None) -> str:
    clean = clean_text(code or "fr", 8).lower()
    if clean not in LANGUAGES:
        raise ValueError("INVALID_LANG")
    return clean


def _strip_wiki(text: str) -> str:
    text = re.sub(r"==+[^=]+==+", " ", text)
    text = re.sub(r"\{\{[^}]+\}\}", " ", text)
    text = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return clean_text(text, 500)


def _parse_wiktionary_rest(data: dict, section_lang: str) -> tuple[str, list[dict], list[str]]:
    if not isinstance(data, dict):
        return "", [], []

    blocks = data.get(section_lang)
    if not blocks and section_lang in {"ln", "lua"}:
        for key in ("fr", "en", "es"):
            if data.get(key):
                blocks = data[key]
                break
    if not blocks and data:
        first_key = next(iter(data.keys()), None)
        if first_key:
            blocks = data.get(first_key)

    if not isinstance(blocks, list):
        return "", [], []

    meanings_out: list[dict] = []
    synonyms: list[str] = []

    for block in blocks:
        if not isinstance(block, dict):
            continue
        part = clean_text(block.get("partOfSpeech") or block.get("language"), 60)
        defs: list[dict] = []
        for item in block.get("definitions") or []:
            if isinstance(item, str):
                text = _strip_wiki(item)
            elif isinstance(item, dict):
                text = _strip_wiki(item.get("definition") or "")
                examples = item.get("examples") or []
                example = ""
                if examples:
                    example = _strip_wiki(
                        examples[0] if isinstance(examples[0], str) else str(examples[0])
                    )
                if text:
                    defs.append({"text": text, "example": example})
                continue
            else:
                continue
            if text:
                defs.append({"text": text, "example": ""})
            if len(defs) >= 5:
                break
        if defs:
            meanings_out.append({"partOfSpeech": part or "définition", "definitions": defs})
        if len(meanings_out) >= 6:
            break

    return "", meanings_out, synonyms[:8]


def _lookup_wiktionary_rest(word: str, lang: str) -> tuple[str, list[dict], list[str], str]:
    site_lang, section_lang = _WIKI_SITE.get(lang, ("fr", "fr"))
    candidates = [word.strip(), word.strip().lower(), word.strip().capitalize()]
    seen: set[str] = set()

    for candidate in candidates:
        key = candidate.lower()
        if not candidate or key in seen:
            continue
        seen.add(key)

        url = (
            f"https://{site_lang}.wiktionary.org/api/rest_v1/page/definition/"
            + quote(candidate, safe="")
        )
        data = _fetch_json(url)
        phonetic, meanings, synonyms = _parse_wiktionary_rest(data or {}, section_lang)
        if meanings:
            return phonetic, meanings, synonyms, "wiktionary"

    return "", [], [], ""


def _lookup_wiktionary_extract(word: str, lang: str) -> tuple[str, list[dict], list[str], str]:
    site_lang, _ = _WIKI_SITE.get(lang, ("fr", "fr"))
    url = (
        f"https://{site_lang}.wiktionary.org/w/api.php?action=query&prop=extracts"
        f"&exintro&explaintext&redirects=1&titles={quote(word)}&format=json&origin=*"
    )
    data = _fetch_json(url)
    if not isinstance(data, dict):
        return "", [], [], ""

    pages = (data.get("query") or {}).get("pages") or {}
    extract = ""
    for page in pages.values():
        if isinstance(page, dict) and page.get("extract"):
            extract = page["extract"]
            break

    extract = _strip_wiki(extract)
    if len(extract) < 12:
        return "", [], [], ""

    sentences = re.split(r"(?<=[.!?])\s+", extract)
    defs = []
    for sentence in sentences[:5]:
        text = _strip_wiki(sentence)
        if len(text) > 8:
            defs.append({"text": text, "example": ""})
    if not defs:
        return "", [], [], ""

    return "", [{"partOfSpeech": "définition", "definitions": defs}], [], "wiktionary-extract"


def _parse_dictionaryapi(entries: list) -> tuple[str, list[dict], list[str]]:
    if not entries or not isinstance(entries[0], dict):
        return "", [], []

    entry = entries[0]
    phonetic = clean_text(entry.get("phonetic"), 40)
    if not phonetic:
        for item in entry.get("phonetics") or []:
            if isinstance(item, dict) and item.get("text"):
                phonetic = clean_text(item["text"], 40)
                break

    meanings_out: list[dict] = []
    synonyms: list[str] = []

    for meaning in entry.get("meanings") or []:
        if not isinstance(meaning, dict):
            continue
        part = clean_text(meaning.get("partOfSpeech"), 40)
        defs: list[dict] = []
        for definition in meaning.get("definitions") or []:
            if not isinstance(definition, dict):
                continue
            text = clean_text(definition.get("definition"), 400)
            if not text:
                continue
            example = clean_text(definition.get("example"), 200)
            defs.append({"text": text, "example": example})
            for syn in definition.get("synonyms") or []:
                syn_clean = clean_text(syn, 60)
                if syn_clean and syn_clean not in synonyms:
                    synonyms.append(syn_clean)
            if len(defs) >= 4:
                break
        if defs:
            meanings_out.append({"partOfSpeech": part, "definitions": defs})
        if len(meanings_out) >= 5:
            break

    return phonetic, meanings_out, synonyms[:8]


def _lookup_dictionaryapi(word: str, lang: str) -> tuple[str, list[dict], list[str], str]:
    if lang not in {"en", "es"}:
        return "", [], [], ""
    url = f"https://api.dictionaryapi.dev/api/v2/entries/{lang}/{quote(word.lower())}"
    data = _fetch_json(url)
    if not isinstance(data, list):
        return "", [], [], ""
    phonetic, meanings, synonyms = _parse_dictionaryapi(data)
    if meanings:
        return phonetic, meanings, synonyms, "dictionaryapi"
    return "", [], [], ""


def _lookup_local(word: str, lang: str) -> tuple[str, list[dict], list[str]]:
    key = _normalize_key(word)
    entry = LOCAL_ENTRIES.get((lang, key))
    if not entry:
        return "", [], []
    return (
        clean_text(entry.get("phonetic"), 40),
        entry.get("meanings") or [],
        entry.get("synonyms") or [],
    )


def lookup(word: str, lang: str | None = None) -> dict:
    clean_word = clean_text(word, 80).strip()
    if not clean_word:
        raise ValueError("INVALID_INPUT")
    if not _WORD_RE.match(clean_word):
        raise ValueError("INVALID_INPUT")

    language = _resolve_lang(lang)
    phonetic = ""
    meanings: list[dict] = []
    synonyms: list[str] = []
    provider = ""

    providers = (
        _lookup_pons,
        _lookup_wiktionary_rest,
        _lookup_wiktionary_extract,
        _lookup_dictionaryapi,
    )
    for fn in providers:
        phonetic, meanings, synonyms, provider = fn(clean_word, language)
        if meanings:
            break

    if not meanings:
        phonetic, meanings, synonyms = _lookup_local(clean_word, language)
        provider = "local" if meanings else ""

    if not meanings:
        raise ValueError("NOT_FOUND")

    return {
        "ok": True,
        "query": clean_word,
        "word": clean_word,
        "lang": language,
        "langLabel": LANGUAGES[language]["label"],
        "phonetic": phonetic,
        "meanings": meanings,
        "synonyms": synonyms,
        "provider": provider,
    }
