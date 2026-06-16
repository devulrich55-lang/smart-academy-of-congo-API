"""Nettoyage périodique — évite l'enflure de la base sur le long terme."""

from datetime import datetime, timezone

from app.database import get_db


def run_maintenance() -> dict:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    before_refresh = db.execute("SELECT COUNT(*) AS c FROM refresh_tokens").fetchone()["c"]
    before_reset = db.execute("SELECT COUNT(*) AS c FROM password_reset_tokens").fetchone()["c"]

    db.execute("DELETE FROM refresh_tokens WHERE expires_at < ?", (now,))
    db.execute(
        "DELETE FROM password_reset_tokens WHERE expires_at < ? OR used_at IS NOT NULL",
        (now,),
    )
    db.commit()

    after_refresh = db.execute("SELECT COUNT(*) AS c FROM refresh_tokens").fetchone()["c"]
    after_reset = db.execute("SELECT COUNT(*) AS c FROM password_reset_tokens").fetchone()["c"]
    return {
        "refreshTokensPurged": before_refresh - after_refresh,
        "resetTokensPurged": before_reset - after_reset,
    }
