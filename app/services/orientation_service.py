from app.utils.sanitize import clean_text

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


def advise(actor: dict, interests: str = "") -> dict:
    if actor.get("role") != "etudiant":
        raise ValueError("FORBIDDEN")

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
            f"Conseil personnalisé pour {filiere or 'votre filière'}"
            + (f" ({campus})" if campus else "")
            + (f" — {prenom}" if prenom else "")
            + "."
        ),
        "disclaimer": (
            "Conseil indicatif généré par l'assistant SAC — "
            "validation par le service orientation de votre université requise."
        ),
    }
