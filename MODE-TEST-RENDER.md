# Mode test Render — garder les comptes sans payer MySQL

Pour les **tests** (30 à 5 000 utilisateurs) **sans budget** PlanetScale/MySQL, utilisez **SQLite sur le disque persistant Render**. Les comptes survivent aux redéploiements.

| Mode | Coût estimé | Comptes | Quand l'utiliser |
|------|-------------|---------|------------------|
| **SQLite + disque `/data`** | ~7–25 $/mois (Render + disque 1 Go) | jusqu'à ~5 000 | Tests, démo, pré-financement |
| **MySQL externe** | ~180–350 $/mois+ | 50 000+ | Plateforme financée, forte charge |

---

## Configuration Render (mode test — à faire maintenant)

### 1. Disque persistant

Render → **smart-academy-of-congo-API-1** → **Disks** → Add disk

| Champ | Valeur |
|-------|--------|
| Name | `sac-data` |
| Mount path | `/data` |
| Size | **1 Go** suffit pour ~30–500 comptes |

### 2. Variables Environment

**Supprimez** (si présentes) :
- `DATABASE_URL`
- `MYSQL_HOST`, `MYSQL_PASSWORD`, etc.

**Ajoutez / modifiez** :

```env
NODE_ENV=production
DATABASE_BACKEND=sqlite
DATABASE_PATH=/data/sac.db
UPLOAD_DIR=/data/uploads
COOKIE_SECURE=true
CROSS_ORIGIN_AUTH=true
ALLOWED_ORIGINS=https://smart-academy-of-congo-dbfm.onrender.com
FRONTEND_URL=https://smart-academy-of-congo-dbfm.onrender.com
GMAIL_USER=votre-compte@gmail.com
GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
```

### 3. Manual Deploy

Attendez **Live**, puis vérifiez :

`https://smart-academy-of-congo-api-1.onrender.com/api/health`

Réponse attendue :

```json
{
  "ok": true,
  "database": "up",
  "storage": {
    "backend": "sqlite",
    "mode": "sqlite-test",
    "databasePath": "/data/sac.db",
    "dbOnRenderDisk": true,
    "uploadsOnRenderDisk": true,
    "persistentOnRenderDisk": true,
    "userCount": 30
  }
}
```

`persistentOnRenderDisk: true` = **vos comptes ne seront pas effacés** au prochain déploiement.

---

## Test de persistance

1. Notez `userCount` dans `/api/health`
2. Créez un compte test sur le site
3. `userCount` augmente de 1
4. **Manual Deploy** sur l'API
5. `userCount` doit être **identique** — le compte doit toujours pouvoir se connecter

---

## Passer à MySQL plus tard (quand le projet est financé)

1. Créez une base MySQL (PlanetScale, Aiven…)
2. Sur Render, remplacez :

```env
DATABASE_BACKEND=mysql
DATABASE_URL=mysql://user:pass@host:3306/smart_academy
```

3. Supprimez `DATABASE_PATH` (optionnel, ignoré en mode MySQL)
4. Exportez les comptes SQLite → import MySQL si besoin (voir `MYSQL-SETUP.md`)

---

## Limites du mode test

- ~30–100 utilisateurs **simultanés** : OK
- Pics massifs (examens nationaux) : migrer vers MySQL
- Ne **supprimez jamais** le disque `/data` sur Render — vous perdriez tous les comptes
