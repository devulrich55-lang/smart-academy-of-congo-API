"""Contrôles d'accès — empêche la modification de données d'autrui."""

FORBIDDEN_IDENTITY_KEYS = frozenset({
    "role",
    "password",
    "password_hash",
    "is_admin",
    "admin",
    "userId",
    "authorId",
    "validated_by",
    "validatedBy",
    "hash_signature",
})


def strip_identity_fields(body: dict | None) -> dict:
    """Retire les champs d'identité / privilège falsifiables par le client."""
    if not body or not isinstance(body, dict):
        return {}
    return {k: v for k, v in body.items() if k not in FORBIDDEN_IDENTITY_KEYS}


def pick_fields(body: dict | None, *keys: str) -> dict:
    if not body or not isinstance(body, dict):
        return {}
    return {k: body[k] for k in keys if k in body}


def assert_same_campus(user: dict | None, universite: str | None) -> None:
    if not user or not universite:
        raise ValueError("FORBIDDEN_CAMPUS")
    user_uni = user.get("universite")
    if user.get("role") == "universite":
        code = user.get("universite") or user.get("codeUni") or user.get("sigle")
        if code and universite != code:
            raise ValueError("FORBIDDEN_CAMPUS")
        return
    if user_uni and user_uni != universite:
        raise ValueError("FORBIDDEN_CAMPUS")


def can_access_submission(user: dict, submission: dict | None) -> bool:
    if not user or not submission:
        return False
    role = user.get("role")
    email = (user.get("email") or "").lower()
    if role == "etudiant":
        return (submission.get("studentEmail") or "").lower() == email
    if role in ("professeur", "universite", "assistant"):
        assert_same_campus(user, submission.get("universite"))
        if role == "professeur":
            prof = (submission.get("professorEmail") or "").lower()
            return not prof or prof == email
        return True
    return False


def assert_submission_access(user: dict, submission: dict | None) -> None:
    if not can_access_submission(user, submission):
        raise ValueError("FORBIDDEN")
