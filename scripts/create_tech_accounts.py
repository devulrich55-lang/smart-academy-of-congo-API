#!/usr/bin/env python3
"""Crée les comptes Jean (dev) et Tech Manager via l'API Render."""
import json
import sys
import urllib.error
import urllib.request

API = "https://smart-academy-of-congo-api-1.onrender.com"
SUPER_EMAIL = "ulrichcibamba55@gmail.com"
SUPER_PASSWORD = "Ulrich11+"
TEAM_PASSWORD = "EvoSU2026!"

ACCOUNTS = [
    {
        "email": "jean.mukendi@evosmartuni.com",
        "role": "developpeur",
        "prenom": "Jean",
        "nom": "Mukendi",
        "telephone": "+243 81 500 0001",
        "fonction": "Développeur Backend Python",
    },
    {
        "email": "tech.manager@evosmartuni.com",
        "role": "techmanager",
        "prenom": "Patrick",
        "nom": "Kabila",
        "telephone": "+243 81 500 0002",
        "fonction": "Responsable technique EvoSU",
    },
]


def req(method, path, body=None, token=None):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read().decode())


def main():
    print("Connexion Super Admin…")
    login = req(
        "POST",
        "/api/auth/login",
        {
            "identifier": SUPER_EMAIL,
            "password": SUPER_PASSWORD,
            "role": "superadmin",
            "adminPortal": True,
        },
    )
    token = login.get("accessToken")
    if not token:
        print("Erreur: pas de jeton d'accès", login, file=sys.stderr)
        sys.exit(1)

    for acc in ACCOUNTS:
        payload = {**acc, "password": TEAM_PASSWORD}
        try:
            out = req("POST", "/api/admin/institutional", payload, token)
            print("Créé:", acc["email"], "→", out.get("admin", {}).get("role"))
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code == 409:
                print("Existe déjà:", acc["email"])
            else:
                print("Erreur", acc["email"], e.code, body, file=sys.stderr)
                sys.exit(1)

    print("\nComptes prêts — mot de passe:", TEAM_PASSWORD)
    print("Jean  → /devcenter/")
    print("Tech  → /techmanager/")


if __name__ == "__main__":
    main()
