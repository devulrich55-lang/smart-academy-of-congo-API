# MySQL — Smart Academy of Congo

L’API utilise **MySQL** en production pour supporter un très grand nombre d’utilisateurs. SQLite reste disponible **uniquement en développement local** si MySQL n’est pas configuré.

## 1. Créer une base MySQL

Choisissez un hébergeur MySQL 8.0+ :

| Service | Note |
|---------|------|
| [PlanetScale](https://planetscale.com) | Serverless, facile à déployer |
| [Aiven MySQL](https://aiven.io/mysql) | Managed, bon pour l’Afrique/Europe |
| [AWS RDS MySQL](https://aws.amazon.com/rds/mysql/) | Production entreprise |
| MySQL local (XAMPP, Docker) | Développement |

Créez une base nommée par exemple `smart_academy` avec encodage **utf8mb4**.

```sql
CREATE DATABASE smart_academy CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'sac_user'@'%' IDENTIFIED BY 'mot_de_passe_fort';
GRANT ALL PRIVILEGES ON smart_academy.* TO 'sac_user'@'%';
FLUSH PRIVILEGES;
```

## 2. Variables d’environnement

### Production (Render)

Dans **smart-academy-of-congo-API-1 → Environment** :

```env
DATABASE_BACKEND=mysql
DATABASE_URL=mysql://sac_user:MOT_DE_PASSE@host:3306/smart_academy
UPLOAD_DIR=/data/uploads
```

Ou sans URL :

```env
MYSQL_HOST=your-host.com
MYSQL_PORT=3306
MYSQL_USER=sac_user
MYSQL_PASSWORD=MOT_DE_PASSE
MYSQL_DATABASE=smart_academy
```

### Développement local

Copiez `.env.example` vers `.env` et configurez MySQL, **ou** laissez SQLite (aucune variable MySQL) :

```env
# MySQL local
DATABASE_BACKEND=mysql
MYSQL_HOST=127.0.0.1
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DATABASE=smart_academy
```

## 3. Schéma automatique

Au premier démarrage, l’API exécute `db/schema-mysql.sql` et crée toutes les tables (~24 tables).

Aucune migration manuelle n’est nécessaire pour une installation neuve.

## 4. Migrer depuis SQLite (ancienne version)

Si vous aviez des données dans `sac.db` :

1. Exportez les tables principales (`users`, `documents`, `grades`, …) en CSV ou SQL.
2. Importez dans MySQL (phpMyAdmin, MySQL Workbench, ou `mysqlimport`).
3. Vérifiez `/api/health` → `"backend": "mysql"` et `"userCount"` correct.

## 5. Vérification

```http
GET https://smart-academy-of-congo-api-1.onrender.com/api/health
```

Réponse attendue :

```json
{
  "ok": true,
  "database": "up",
  "storage": {
    "backend": "mysql",
    "mysqlHost": "your-host.com",
    "mysqlDatabase": "smart_academy",
    "uploadsOnRenderDisk": true,
    "userCount": 0
  }
}
```

## 6. Capacité

MySQL + plan Render **Standard** convient pour **des dizaines de milliers de comptes** et une forte concurrence de lectures/écritures. Pour 50 000+ utilisateurs actifs simultanément, augmentez le plan MySQL et l’API (voir `SCALING.md`).
