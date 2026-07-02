# Gmail — envoi des codes de réinitialisation par e-mail

**Important :** Gmail **envoie** le code par e-mail ; il ne le génère pas.  
L'API Smart Academy crée un code sécurisé à 6 chiffres, puis l'envoie **uniquement** via votre compte Gmail (SMTP). L'utilisateur le reçoit dans sa boîte mail — nulle part ailleurs.

## 1. Créer un mot de passe d'application Gmail

Google n'accepte plus le mot de passe du compte pour SMTP. Il faut un **mot de passe d'application** :

1. Compte Google → [Sécurité](https://myaccount.google.com/security)
2. Activez la **validation en 2 étapes** (obligatoire)
3. **Mots de passe des applications** → Créer → nom : `Smart Academy API`
4. Copiez le code à **16 caractères** (ex. `abcd efgh ijkl mnop`)

## 2. Variables sur Render (recommandé)

Dans **Evo-smartUni-API-1 → Environment** :

```env
GMAIL_USER=votre-compte@gmail.com
GMAIL_APP_PASSWORD=abcdefghijklmnop
FRONTEND_URL=https://evosmartuni.com
RESET_TOKEN_HOURS=1
```

`GMAIL_APP_PASSWORD` : collez les 16 caractères **sans espaces** ou avec espaces (les deux fonctionnent).

Alternative (SMTP explicite) :

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USER=votre-compte@gmail.com
SMTP_PASS=abcdefghijklmnop
EMAIL_FROM=votre-compte@gmail.com
```

Pour le port **465** (SSL) :

```env
SMTP_PORT=465
SMTP_USE_SSL=true
SMTP_USE_TLS=false
```

## 3. Développement local

Dans `backend-python/.env` :

```env
GMAIL_USER=votre-compte@gmail.com
GMAIL_APP_PASSWORD=abcdefghijklmnop
FRONTEND_URL=http://localhost:5500
```

Sans Gmail configuré, le code et le lien s'affichent dans les **logs** du serveur (mode dev).

## 4. Vérification

```http
GET https://Evo-smartUni-api-1.onrender.com/api/health
```

Réponse attendue :

```json
"emailConfigured": true
```

Test manuel :

1. Ouvrez `mot-de-passe-oublie.html`
2. Saisissez un e-mail de compte existant
3. Vérifiez Gmail → code **6 chiffres** + lien
4. Sur `reinitialisation.html` → onglet **Via le code e-mail**

## 5. Google Workspace (domaine pro)

Si vous avez `@smartacademy.cd` sur Google Workspace :

```env
GMAIL_USER=noreply@smartacademy.cd
GMAIL_APP_PASSWORD=...
EMAIL_FROM=noreply@smartacademy.cd
```

Même procédure : mot de passe d'application pour ce compte.

## 6. Dépannage

| Problème | Solution |
|----------|----------|
| `emailConfigured: false` | Ajoutez `GMAIL_USER` + `GMAIL_APP_PASSWORD` sur Render |
| Authentification refusée | Nouveau mot de passe d'application, 2FA activée |
| `EMAIL_SEND_FAILED` / envoi impossible | **Ne pas** mettre `EMAIL_FROM=noreply@…` si vous utilisez Gmail perso — l'expéditeur doit être = `GMAIL_USER` |
| E-mail en spam | Marquez comme « Non spam », utilisez un expéditeur fixe |
| Code expiré | Demandez un nouveau code (valide `RESET_TOKEN_HOURS` h) |
