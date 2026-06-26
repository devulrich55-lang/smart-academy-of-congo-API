"""Orientation académique — règles SAC + LLM OpenAI optionnel."""

import json
import logging
import re
import urllib.error
import urllib.request

from app.config import settings
from app.utils.sanitize import clean_text

logger = logging.getLogger("sac.orientation")

ORIENTATION = {
    "informatique": {
        "filieres": ["Informatique", "Génie logiciel", "Réseaux & télécoms"],
        "stages": ["Développeur web/mobile", "Admin système", "Cybersécurité junior"],
        "skills": ["Python/Java", "SQL & bases de données", "Anglais technique"],
    },
    "medecine": {
        "filieres": ["Médecine", "Sciences infirmières", "Santé publique"],
        "stages": ["Hôpital", "ONG santé", "Recherche clinique"],
        "skills": ["Biologie", "Éthique médicale", "Gestion du stress"],
    },
    "droit": {
        "filieres": ["Droit", "Sciences politiques"],
        "stages": ["Cabinet d'avocats", "Parquet", "ONG droits humains"],
        "skills": ["Argumentation", "Droit OHADA", "Rédaction juridique"],
    },
    "commerce": {
        "filieres": ["Gestion", "Comptabilité", "Marketing"],
        "stages": ["Banque", "Audit", "Entrepreneuriat"],
        "skills": ["Excel & analyse", "Comptabilité", "Communication professionnelle"],
    },
    "ingenierie": {
        "filieres": ["Génie civil", "Génie électrique", "Génie mécanique"],
        "stages": ["Bureau d'études", "Chantier", "Maintenance industrielle"],
        "skills": ["Mathématiques appliquées", "CAO/DAO", "Normes de sécurité"],
    },
}

NEXT_LEVEL = {
    "l1": "L2",
    "l2": "L3",
    "l3": "Master",
    "master1": "Master 2",
    "master2": "Doctorat ou insertion pro",
    "master": "Doctorat ou insertion pro",
    "doctorat": "Recherche & enseignement supérieur",
}

DISCLAIMER = (
    "Conseil indicatif — validation par le service orientation de votre université requise."
)


def _detect_domain(text: str) -> str:
    f = (text or "").lower()
    if any(k in f for k in ("info", "logiciel", "réseau", "data", "cyber", "informat")):
        return "informatique"
    if any(k in f for k in ("médec", "santé", "infirm", "pharm", "medec")):
        return "medecine"
    if any(k in f for k in ("droit", "jurid", "polit", "ohada")):
        return "droit"
    if any(k in f for k in ("génie", "civil", "électr", "mecan", "ingen")):
        return "ingenierie"
    return "commerce"


def _rule_based_advice(actor: dict, interests: str) -> dict:
    filiere = clean_text(interests or actor.get("filiere"), 120)
    niveau = clean_text(actor.get("niveau"), 40) or "L1"
    campus = clean_text(actor.get("universite") or actor.get("codeUni"), 80)
    domain = _detect_domain(filiere)
    pack = ORIENTATION[domain]
    nkey = niveau.lower().replace(" ", "")
    next_step = NEXT_LEVEL.get(nkey, "Poursuite d'études ou spécialisation")
    prenom = clean_text(actor.get("prenom"), 80)
    return {
        "domain": domain,
        "recommendedFilieres": pack["filieres"],
        "suggestedInternships": pack["stages"],
        "skillsToDevelop": pack["skills"],
        "academicPath": f"Parcours {niveau} → prochaine étape recommandée : {next_step}.",
        "message": (
            f"Conseil pour {filiere or 'votre filière'}"
            + (f" ({campus})" if campus else "")
            + (f" — {prenom}" if prenom else "")
            + "."
        ),
        "keyPoints": [
            f"Domaine détecté : {domain}",
            f"Prochaine étape : {next_step}",
        ],
        "disclaimer": DISCLAIMER,
        "source": "rules",
    }


def _llm_available() -> bool:
    return bool(
        settings.orientation_use_llm
        and settings.openai_api_key
        and settings.openai_api_key.startswith("sk-")
    )


def _parse_llm_json(content: str) -> dict | None:
    text = (content or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _llm_advice(actor: dict, interests: str) -> dict | None:
    if not _llm_available():
        return None

    filiere = clean_text(interests or actor.get("filiere"), 120) or "non précisée"
    niveau = clean_text(actor.get("niveau"), 40) or "L1"
    campus = clean_text(actor.get("universite") or actor.get("codeUni"), 80) or "RDC"
    prenom = clean_text(actor.get("prenom"), 80)
    nom = clean_text(actor.get("nom"), 80)
    name = f"{prenom} {nom}".strip() or "Étudiant"

    system = (
        "Tu es l'assistant d'orientation académique de Smart Academy of Congo (universités en RDC). "
        "Réponds UNIQUEMENT en JSON valide, sans markdown, avec les clés : "
        "message (string, 2-3 phrases en français), recommendedFilieres (array de 3 strings), "
        "suggestedInternships (array de 3 strings), skillsToDevelop (array de 3 strings), "
        "academicPath (string), keyPoints (array de 2-4 strings), domain (string court)."
    )
    user = (
        f"Étudiant : {name}. Campus : {campus}. Niveau : {niveau}. "
        f"Filière / intérêts : {filiere}. "
        "Donne un conseil personnalisé, réaliste pour le contexte congolais (Kinshasa, Lubumbashi, etc.)."
    )

    payload = json.dumps(
        {
            "model": settings.openai_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.6,
            "max_tokens": 900,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        parsed = _parse_llm_json(content)
        if not parsed:
            return None
        return {
            "domain": clean_text(parsed.get("domain"), 40) or _detect_domain(filiere),
            "recommendedFilieres": (parsed.get("recommendedFilieres") or [])[:5],
            "suggestedInternships": (parsed.get("suggestedInternships") or [])[:5],
            "skillsToDevelop": (parsed.get("skillsToDevelop") or [])[:5],
            "academicPath": clean_text(parsed.get("academicPath"), 500),
            "message": clean_text(parsed.get("message"), 1200),
            "keyPoints": (parsed.get("keyPoints") or [])[:6],
            "disclaimer": DISCLAIMER,
            "source": "llm",
        }
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError) as exc:
        logger.warning("Orientation LLM indisponible : %s", exc)
        return None


def advise(actor: dict, interests: str = "") -> dict:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")

    llm = _llm_advice(actor, interests)
    if llm:
        return llm
    return _rule_based_advice(actor, interests)


def status() -> dict:
    llm = _llm_available()
    return {
        "llmAvailable": llm,
        "model": settings.openai_model if llm else None,
        "mode": "llm" if llm else "rules",
    }
