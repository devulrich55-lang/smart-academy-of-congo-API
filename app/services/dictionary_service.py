import json
import re
import unicodedata
from urllib.error import URLError
from urllib.parse import quote
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

_API_LANGS = frozenset({"fr", "en", "es"})

LOCAL_ENTRIES: dict[tuple[str, str], dict] = {
    ("fr", "livre"): {
        "phonetic": "/livʁ/",
        "meanings": [
            {
                "partOfSpeech": "nom masculin",
                "definitions": [
                    {
                        "text": "Ouvrage composé de feuilles imprimées ou manuscrites, réunies sous une couverture.",
                        "example": "Un livre d'histoire.",
                    },
                    {
                        "text": "Ensemble des ouvrages traitant d'une discipline.",
                        "example": "Le livre de droit.",
                    },
                ],
            }
        ],
        "synonyms": ["ouvrage", "volume", "manuel"],
    },
    ("fr", "ecole"): {
        "phonetic": "/ekɔl/",
        "meanings": [
            {
                "partOfSpeech": "nom féminin",
                "definitions": [
                    {
                        "text": "Établissement où l'on dispense un enseignement organisé.",
                        "example": "Aller à l'école.",
                    }
                ],
            }
        ],
        "synonyms": ["établissement scolaire", "institution"],
    },
    ("fr", "etudiant"): {
        "phonetic": "/etydjɑ̃/",
        "meanings": [
            {
                "partOfSpeech": "nom",
                "definitions": [
                    {
                        "text": "Personne qui suit des études dans un établissement d'enseignement supérieur ou secondaire.",
                        "example": "Un étudiant en médecine.",
                    }
                ],
            }
        ],
        "synonyms": ["élève", "apprenant"],
    },
    ("en", "book"): {
        "phonetic": "/bʊk/",
        "meanings": [
            {
                "partOfSpeech": "noun",
                "definitions": [
                    {
                        "text": "A written or printed work consisting of pages bound together.",
                        "example": "She borrowed a book from the library.",
                    },
                    {
                        "text": "A set of blank sheets for writing or keeping records.",
                        "example": "an exercise book",
                    },
                ],
            }
        ],
        "synonyms": ["volume", "publication", "tome"],
    },
    ("en", "school"): {
        "phonetic": "/skuːl/",
        "meanings": [
            {
                "partOfSpeech": "noun",
                "definitions": [
                    {
                        "text": "An institution for educating children or providing specialized instruction.",
                        "example": "He walks to school every morning.",
                    }
                ],
            }
        ],
        "synonyms": ["academy", "college", "institution"],
    },
    ("es", "libro"): {
        "phonetic": "/ˈliβɾo/",
        "meanings": [
            {
                "partOfSpeech": "sustantivo masculino",
                "definitions": [
                    {
                        "text": "Conjunto de hojas impresas o manuscritas encuadernadas.",
                        "example": "Leí un libro de historia.",
                    }
                ],
            }
        ],
        "synonyms": ["obra", "volumen", "manual"],
    },
    ("es", "escuela"): {
        "phonetic": "/esˈkwela/",
        "meanings": [
            {
                "partOfSpeech": "sustantivo femenino",
                "definitions": [
                    {
                        "text": "Establecimiento donde se imparte enseñanza.",
                        "example": "Los niños van a la escuela.",
                    }
                ],
            }
        ],
        "synonyms": ["colegio", "instituto"],
    },
    ("ln", "mbote"): {
        "phonetic": "",
        "meanings": [
            {
                "partOfSpeech": "interjection",
                "definitions": [
                    {
                        "text": "Maloba ya kozwa moko to koleka na mboka. (Salutation pour dire bonjour ou bonsoir.)",
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
                        "text": "Esika oyo bato bandakisi maboko na boyekoli.",
                        "example": "Eteyi ya université.",
                    }
                ],
            }
        ],
        "synonyms": ["kelasi"],
    },
    ("ln", "ndako"): {
        "phonetic": "",
        "meanings": [
            {
                "partOfSpeech": "nom",
                "definitions": [
                    {
                        "text": "Esika ya kolala to ya kofanda na libota.",
                        "example": "Ndako ya moninga.",
                    }
                ],
            }
        ],
        "synonyms": ["ndako ya mboka"],
    },
    ("ln", "moninga"): {
        "phonetic": "",
        "meanings": [
            {
                "partOfSpeech": "nom",
                "definitions": [
                    {
                        "text": "Moto oyo ozali na boyokani malamu na ye.",
                        "example": "Moninga na ngai.",
                    }
                ],
            }
        ],
        "synonyms": ["ndeko"],
    },
    ("lua", "moyo"): {
        "phonetic": "",
        "meanings": [
            {
                "partOfSpeech": "tshibusa",
                "definitions": [
                    {
                        "text": "Bukole bwa kufwala bwa muntu.",
                        "example": "Moyo wa ngwej.",
                    }
                ],
            }
        ],
        "synonyms": [],
    },
    ("lua", "diaku"): {
        "phonetic": "",
        "meanings": [
            {
                "partOfSpeech": "tshibusa",
                "definitions": [
                    {
                        "text": "Muntu udi mukaji wa bungi ne wenze.",
                        "example": "Diaku dianyi.",
                    }
                ],
            }
        ],
        "synonyms": ["mukaji"],
    },
    ("lua", "dibuku"): {
        "phonetic": "",
        "meanings": [
            {
                "partOfSpeech": "tshibusa",
                "definitions": [
                    {
                        "text": "Dijadika dia makanda adibu bua kutanga.",
                        "example": "Dibuku dia bena kudia.",
                    }
                ],
            }
        ],
        "synonyms": [],
    },
    ("lua", "tshikondo"): {
        "phonetic": "",
        "meanings": [
            {
                "partOfSpeech": "tshibusa",
                "definitions": [
                    {
                        "text": "Tshitupa tshia bena kuela.",
                        "example": "Tshikondo tshia université.",
                    }
                ],
            }
        ],
        "synonyms": [],
    },
}


def list_languages() -> list[dict]:
    return list(LANGUAGES.values())


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


def _resolve_lang(code: str | None) -> str:
    clean = clean_text(code or "fr", 8).lower()
    if clean not in LANGUAGES:
        raise ValueError("INVALID_LANG")
    return clean


def _parse_api_entry(entries: list) -> tuple[str, list[dict], list[str]]:
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


def _lookup_api(word: str, lang: str) -> tuple[str, list[dict], list[str], str]:
    if lang not in _API_LANGS:
        return "", [], [], ""

    url = f"https://api.dictionaryapi.dev/api/v2/entries/{lang}/{quote(word.lower())}"
    data = _fetch_json(url)
    if not isinstance(data, list):
        return "", [], [], ""

    phonetic, meanings, synonyms = _parse_api_entry(data)
    if meanings:
        return phonetic, meanings, synonyms, "dictionaryapi"
    return "", [], [], ""


def _lookup_local(word: str, lang: str) -> tuple[str, list[dict], list[str]]:
    key = _normalize_key(word)
    entry = LOCAL_ENTRIES.get((lang, key))
    if not entry:
        return "", [], []

    phonetic = clean_text(entry.get("phonetic"), 40)
    meanings = entry.get("meanings") or []
    synonyms = entry.get("synonyms") or []
    return phonetic, meanings, synonyms


def lookup(word: str, lang: str | None = None) -> dict:
    clean_word = clean_text(word, 80).strip()
    if not clean_word:
        raise ValueError("INVALID_INPUT")
    if not _WORD_RE.match(clean_word):
        raise ValueError("INVALID_INPUT")

    language = _resolve_lang(lang)
    phonetic, meanings, synonyms, provider = _lookup_api(clean_word, language)

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
