# API Python — Evo-smartUni

Backend **FastAPI** — authentification, documents, notes, publications, plateforme.

## Démarrage local

```powershell
cd backend-python
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

Comptes démo : `etu.demo@unikin.cd` / `Demo2025!`

---

## Render — persistance (obligatoire)

**Sans disque `/data`, les comptes et fichiers sont effacés à chaque redéploiement.**

1. Disque persistant : mount **`/data`**, 1 Go
2. Variables :
   - `DATABASE_PATH=/data/EvoSU.db`
   - `UPLOAD_DIR=/data/uploads`

Guide complet : **`RENDER-PERSISTENCE.md`**

Vérification : `GET /api/health` → `"persistentOnRenderDisk": true`

---

## Variables production

Voir `render.env.production.example`

