import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger("sac.email")


def _brand() -> str:
    return getattr(settings, "platform_name", None) or "Evo-smartUni"


def _brand_short() -> str:
    return getattr(settings, "platform_short", None) or "EvoSU"


def smtp_configured() -> bool:
    return bool(
        settings.smtp_host
        and settings.email_from
        and settings.smtp_user
        and settings.smtp_pass
    )


def _send_via_smtp(to_email: str, msg: MIMEMultipart) -> None:
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=20
        ) as server:
            server.login(settings.smtp_user, settings.smtp_pass)
            server.sendmail(settings.email_from, [to_email], msg.as_string())
        return

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.ehlo()
        if settings.smtp_use_tls:
            server.starttls()
            server.ehlo()
        server.login(settings.smtp_user, settings.smtp_pass)
        server.sendmail(settings.email_from, [to_email], msg.as_string())


def send_password_reset_email(
    to_email: str,
    reset_url: str,
    display_name: str,
    reset_code: str,
) -> bool:
    """Envoie le code et le lien par e-mail via Gmail/SMTP. Le code n'est visible que dans l'e-mail."""
    brand = _brand()
    subject = f"Votre code {_brand_short()} — réinitialisation du mot de passe"
    greeting = display_name or "Utilisateur"
    text_body = (
        f"Bonjour {greeting},\n\n"
        f"Vous avez demandé la réinitialisation de votre mot de passe sur {brand}.\n\n"
        f"Votre code de réinitialisation : {reset_code}\n"
        f"(valide {settings.reset_token_hours} h)\n\n"
        "Ou cliquez sur ce lien :\n"
        f"{reset_url}\n\n"
        "Sur la page de réinitialisation, entrez ce code avec votre e-mail "
        "ou utilisez directement le lien.\n\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.\n\n"
        f"— {brand}"
    )
    html_body = f"""<!DOCTYPE html>
<html lang="fr">
<body style="font-family:Arial,sans-serif;line-height:1.6;color:#1a2b3c;max-width:560px;margin:0 auto;padding:24px;">
  <h2 style="color:#0c3d6e;">Réinitialisation du mot de passe</h2>
  <p>Bonjour <strong>{greeting}</strong>,</p>
  <p>Vous avez demandé la réinitialisation de votre mot de passe sur <strong>{brand}</strong>.</p>
  <p style="font-size:15px;color:#1a2b3c;">Votre code de réinitialisation :</p>
  <p style="font-size:32px;font-weight:700;letter-spacing:6px;color:#0c3d6e;margin:16px 0;">{reset_code}</p>
  <p style="font-size:14px;color:#5a6d7e;">Ce code expire dans {settings.reset_token_hours} heure(s).</p>
  <p style="margin:28px 0;">
    <a href="{reset_url}" style="background:#0c3d6e;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;display:inline-block;">
      Choisir un nouveau mot de passe
    </a>
  </p>
  <p style="font-size:14px;color:#5a6d7e;">Si le bouton ne fonctionne pas, copiez ce lien :<br>{reset_url}</p>
  <p style="font-size:14px;color:#5a6d7e;">Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.</p>
  <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0;">
  <p style="font-size:12px;color:#5a6d7e;">{brand}</p>
</body>
</html>"""

    if not smtp_configured():
        if settings.is_prod:
            logger.error(
                "Gmail non configuré en production — impossible d'envoyer le code à %s",
                to_email,
            )
        else:
            logger.warning(
                "Gmail non configuré (dev) — configurez GMAIL_USER + GMAIL_APP_PASSWORD. "
                "Destinataire : %s",
                to_email,
            )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        _send_via_smtp(to_email, msg)
        logger.info("E-mail de réinitialisation envoyé à %s", to_email)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "Échec authentification Gmail/SMTP pour %s — vérifiez GMAIL_APP_PASSWORD (mot de passe d'application, pas le mot de passe du compte)",
            settings.smtp_user or settings.gmail_user,
        )
        return False
    except smtplib.SMTPRecipientsRefused:
        logger.error("Destinataire refusé par Gmail : %s", to_email)
        return False
    except Exception as exc:
        logger.error(
            "Échec envoi e-mail à %s (from=%s host=%s:%s ssl=%s tls=%s) : %s",
            to_email,
            settings.email_from,
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_use_ssl,
            settings.smtp_use_tls,
            exc,
        )
        return False


def send_platform_notification_email(
    to_email: str,
    title: str,
    message: str,
    action_url: str = "",
) -> bool:
    """Notification campus (réseau social, activité plateforme)."""
    if not smtp_configured() or not to_email:
        return False

    brand = _brand()
    short = _brand_short()
    subject = f"{short} — {title}"
    text_body = (
        f"{title}\n\n{message}\n\n"
        + (f"Ouvrir : {action_url}\n\n" if action_url else "")
        + f"— {brand}\n"
        f"Vous recevez cet e-mail car vous êtes inscrit sur la plateforme {brand}."
    )
    link_html = (
        f'<p style="margin:20px 0;"><a href="{action_url}" '
        'style="background:#0084ff;color:#fff;padding:12px 22px;border-radius:8px;'
        f'text-decoration:none;display:inline-block;">Voir sur {short}</a></p>'
        if action_url
        else ""
    )
    html_body = f"""<!DOCTYPE html>
<html lang="fr"><body style="font-family:Arial,sans-serif;line-height:1.6;color:#1a2b3c;max-width:560px;margin:0 auto;padding:24px;">
  <h2 style="color:#0c3d6e;">{title}</h2>
  <p>{message}</p>
  {link_html}
  <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0;">
  <p style="font-size:12px;color:#5a6d7e;">{brand} — notification campus</p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        _send_via_smtp(to_email, msg)
        return True
    except Exception as exc:
        logger.error("Échec notification e-mail à %s : %s", to_email, exc)
        return False


def send_staff_login_code_email(
    to_email: str,
    display_name: str,
    code: str,
    valid_minutes: int = 10,
) -> bool:
    brand = _brand()
    subject = f"Code de connexion {_brand_short()} — portail staff"
    greeting = display_name or "Utilisateur"
    text_body = (
        f"Bonjour {greeting},\n\n"
        f"Votre code de connexion {brand} : {code}\n"
        f"(valide {valid_minutes} min — ne le partagez avec personne)\n\n"
        f"Si vous n'êtes pas à l'origine de cette demande, ignorez ce message."
    )
    html_body = (
        f"<p>Bonjour {greeting},</p>"
        f"<p>Votre code de connexion <strong>{brand}</strong> :</p>"
        f"<p style='font-size:24px;letter-spacing:4px'><strong>{code}</strong></p>"
        f"<p>Valide {valid_minutes} minutes. Ne le partagez avec personne.</p>"
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        _send_via_smtp(to_email, msg)
        return True
    except Exception as exc:
        logger.error("Échec envoi code MFA staff à %s : %s", to_email, exc)
        return False
