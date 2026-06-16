# Montée en charge — Smart Academy API

Ce guide décrit la configuration pour garder la plateforme **stable**.

## Mode test (sans budget MySQL)

Pour **30 à 5 000 comptes** en phase de test : **SQLite + disque `/data` sur Render**.  
Voir **`MODE-TEST-RENDER.md`** — coût ~disque 1 Go seulement, pas de PlanetScale.

## Architecture production (projet financé)

## Architecture actuelle

| Composant | Technologie |
|-----------|-------------|
| **Base de données** | **MySQL 8.0+** (production) |
| **Fichiers uploadés** | Disque persistant Render `/data/uploads` |
| **API** | FastAPI + PyMySQL |
| **Frontend** | Site statique Render (CDN) |

SQLite n’est plus utilisé en production — voir `MYSQL-SETUP.md`.

## Optimisations intégrées

| Optimisation | Effet |
|---|---|
| **MySQL + utf8mb4** | Concurrence, index, millions de lignes |
| **Pagination** (`limit` / `offset`) | Documents, notes, réclamations |
| **Requêtes SQL ciblées** | Pas de chargement complet en mémoire |
| **Nettoyage tokens au démarrage** | Base allégée sur le long terme |
| **GZip** | Réponses JSON compressées |
| **Rate limiting** | Protection contre abus |

## Configuration Render recommandée

### API (`smart-academy-of-congo-API-1`)

| Paramètre | Valeur |
|---|---|
| **Plan API** | Standard |
| **MySQL** | Externe (PlanetScale, Aiven, RDS…) |
| **Disque `/data`** | 10 Go — **uploads uniquement** |
| **DATABASE_URL** | `mysql://user:pass@host:3306/smart_academy` |

### Vérification

```text
GET /api/health
→ "backend": "mysql", "database": "up"
```

## Limites et évolution

| Charge | Recommandation |
|---|---|
| 30–500 comptes test | SQLite + disque Render 1 Go ✅ (`MODE-TEST-RENDER.md`) |
| 5k comptes, usage normal | MySQL entrée de gamme + API Render Standard |
| 50k comptes, usage normal | MySQL Standard + API Render Standard ✅ |
| Pics examens / notes | MySQL plan supérieur, index OK |
| 50k connexions simultanées | MySQL cluster + cache Redis + CDN |
| Fichiers volumineux | S3 / Cloudflare R2 pour les uploads |

## Endpoints paginés

`?limit=50&offset=0` sur :

- `GET /api/documents`
- `GET /api/platform/grades/me`
- `GET /api/reclamations/me`
