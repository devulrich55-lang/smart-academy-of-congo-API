# Persistance des données sur Render

## Base de données (MySQL)

Les comptes, notes, publications et réclamations sont stockés dans **MySQL** (service externe).

Configurez sur l’API :

```env
DATABASE_BACKEND=mysql
DATABASE_URL=mysql://user:pass@host:3306/smart_academy
```

Voir `MYSQL-SETUP.md` pour créer la base et choisir un hébergeur.

Les redéploiements Render **ne suppriment pas** les données MySQL tant que la base externe reste active.

## Fichiers uploadés (disque Render)

Sans disque persistant, **chaque redéploiement efface** les PDF, images et médias uploadés.

### Disque obligatoire

Render → **smart-academy-of-congo-API-1** → **Disks**

| Champ | Valeur |
|-------|--------|
| Name | `sac-uploads` |
| Mount path | `/data` |
| Size | 10 GB |

### Variable

| Variable | Valeur |
|----------|--------|
| `UPLOAD_DIR` | `/data/uploads` |

L’API **refuse de démarrer** sur Render si `UPLOAD_DIR` n’est pas sous `/data`.

## Vérification

`GET https://smart-academy-of-congo-api-1.onrender.com/api/health`

```json
{
  "ok": true,
  "database": "up",
  "storage": {
    "backend": "mysql",
    "mysqlHost": "your-host.com",
    "uploadsOnRenderDisk": true,
    "userCount": 5
  }
}
```

## Test

1. Créez un compte sur le site
2. Uploadez un fichier
3. Redéployez l’API
4. Le compte doit toujours exister (MySQL) et le fichier accessible (disque `/data`)
