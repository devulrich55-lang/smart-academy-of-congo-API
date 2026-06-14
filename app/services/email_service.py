import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger("sac.email")


def _smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.email_from)


def send_password_reset_email(to_email: str, reset_url: str, display_name: str) -> None:
    subject = "Réinitialisation de votre mot de passe — Smart Academy of Congo"
    greeting = display_name or "Utilisateur"
    text_body = (
        f"Bonjour {greeting},\n\n"
        "Vous avez demandé la réinitialisation de votre mot de passe sur Smart Academy of Congo.\n"
        f"Cliquez sur ce lien (valide {settings.reset_token_hours} h) :\n{reset_url}\n\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.\n\n"
        "— Smart Academy of Congo"
    )
    html_body = f"""<!DOCTYPE html>
<html lang="fr">
<body style="font-family:Arial,sans-serif;line-height:1.6;color:#1a2b3c;max-width:560px;margin:0 auto;padding:24px;">
  <h2 style="color:#0c3d6e;">Réinitialisation du mot de passe</h2>
  <p>Bonjour <strong>{greeting}</strong>,</p>
  <p>Vous avez demandé la réinitialisation de votre mot de passe sur <strong>Smart Academy of Congo</strong>.</p>
  <p style="margin:28px 0;">
    <a href="{reset_url}" style="background:#0c3d6e;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;display:inline-block;">
      Choisir un nouveau mot de passe
    </a>
  </p>
  <p style="font-size:14px;color:#5a6d7e;">Ce lien expire dans {settings.reset_token_hours} heure(s). Si le bouton ne fonctionne pas, copiez ce lien :<br>{reset_url}</p>
  <p style="font-size:14px;color:#5a6d7e;">Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.</p>
  <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0;">
  <p style="font-size:12px;color:#5a6d7e;">Smart Academy of Congo</p>
</body>
</html>"""

    if not _smtp_configured():
        logger.warning(
            "SMTP non configuré — lien de réinitialisation pour %s : %s",
            to_email,
            reset_url,
        )
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        if settings.smtp_user and settings.smtp_pass:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_pass)
        server.sendmail(settings.email_from, [to_email], msg.as_string())
