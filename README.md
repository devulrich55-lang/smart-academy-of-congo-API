<<<<<<< HEAD
# API Python — Smart Academy of Congo (backend principal)

Backend **FastAPI** — c'est **celui-ci** que vous devez déployer en production.

Inclut : authentification, documents, tarifs, plateforme, **agent IA de correction**, diplômes, notes.

## Démarrage local (Windows)

```powershell
cd backend-python
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

Tests :
- `http://localhost:8000/api/health`
- Frontend local : ouvrez les pages HTML via un serveur (port 5500) — `js/sac-api.js` cible automatiquement le port 8000.

Comptes démo : `etu.demo@unikin.cd` / `Demo2025!`

---

## Déploiement sur Render

Utilisez le Blueprint `render.yaml` (disque `/data`, secrets JWT auto-générés, health check).

### Variables à personnaliser après déploiement

```
ALLOWED_ORIGINS=https://VOTRE-APP.vercel.app
FRONTEND_URL=https://VOTRE-APP.vercel.app
```

Voir aussi [../DEPLOYMENT.md](../DEPLOYMENT.md) et [../vercel.env.example](../vercel.env.example).

### Vérification

`GET https://VOTRE-SERVICE.onrender.com/api/health`

Réponse attendue :

```json
{"ok": true, "database": "up", "runtime": "python"}
```

---

## Endpoints principaux

| Route | Description |
|-------|-------------|
| `POST /api/auth/login` | Connexion |
| `POST /api/auth/register` | Inscription |
| `GET /api/documents` | Documents |
| `GET /api/platform/grades/me` | Notes |
| `POST /api/platform/corrections/submit` | Dépôt travail + IA |
| `GET /api/platform/corrections/pending` | Travaux à valider (prof) |
| `POST /api/platform/corrections/{id}/validate` | Validation professeur |
| `POST /api/platform/diplomas/verify` | Vérification diplôme (public) |

---

## Vous n'avez pas besoin de

- `backend/` (Node.js) — legacy, peut être ignoré
- `backend-php/` — optionnel, pas nécessaire si vous utilisez Python
=======
# smart-academy-of-congo-API
>>>>>>> dc835eef4153c6a72906b3f3f845b8663d4d5d1e
