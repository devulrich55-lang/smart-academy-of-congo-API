"""Smoke test CI — import app + health + réclamations schema."""
from fastapi.testclient import TestClient

from app.main import app


def main() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    if response.status_code != 200:
        raise SystemExit(f"health status {response.status_code}: {response.text}")
    data = response.json()
    if not data.get("ok"):
        raise SystemExit(f"health not ok: {data}")
    if data.get("database") != "up":
        raise SystemExit(f"database down: {data}")

    from app.database import get_db

    tables = {
        row[0]
        for row in get_db().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for name in ("faculty_sections", "reclamations"):
        if name not in tables:
            raise SystemExit(f"missing table: {name}")

    print("OK", data)


if __name__ == "__main__":
    main()
