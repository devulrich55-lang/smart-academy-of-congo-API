# Persistance des données sur Render

## Mode test (recommandé sans budget)

**SQLite sur disque `/data`** — idéal pour 30 à 5 000 comptes de test.

```env
DATABASE_BACKEND=sqlite
DATABASE_PATH=/data/sac.db
UPLOAD_DIR=/data/uploads
```

Guide complet : **`MODE-TEST-RENDER.md`**

Les redéploiements **ne effacent pas** les comptes tant que le disque `/data` reste monté.

---

## Mode production (projet financé)

Les comptes sont stockés dans **MySQL** (service externe).

```env
DATABASE_BACKEND=mysql
DATABASE_URL=mysql://user:pass@host:3306/smart_academy
```

Voir `MYSQL-SETUP.md`.

---

## Disque persistant Render (obligatoire)

Render → **smart-academy-of-congo-API-1** → **Disks**

| Champ | Mode test | Mode prod |
|-------|-----------|-----------|
| Mount path | `/data` | `/data` |
| Size | **1 Go** | 10 Go |

| Variable | Valeur |
|----------|--------|
| `DATABASE_PATH` | `/data/sac.db` (mode test uniquement) |
| `UPLOAD_DIR` | `/data/uploads` |

---

## Vérification

`GET /api/health`

Mode test attendu :

```json
{
  "storage": {
    "backend": "sqlite",
    "mode": "sqlite-test",
    "persistentOnRenderDisk": true,
    "userCount": 30
  }
}
```

`persistentOnRenderDisk: true` = comptes protégés.

---

## Test

1. Créez un compte
2. Redéployez l'API
3. Le compte doit toujours exister
