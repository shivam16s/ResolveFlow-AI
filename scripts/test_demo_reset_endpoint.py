from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.api.chat_routes as chat_routes  # noqa: E402
from backend.api import create_app  # noqa: E402
from backend.db.seed_demo_dashboard import seed_demo_dashboard  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _make_db() -> Path:
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    db_path = Path(temp.name)
    temp.close()
    seed_demo_dashboard(db_path)
    return db_path


def test_demo_reset_reseeds_database_and_clears_chat_state() -> None:
    db_path = _make_db()
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM conversations")
        connection.execute("DELETE FROM credits")

    chat_routes._CHAT_STATES[("CUST-1001", "tab-reset")] = {
        "customer_id": "CUST-1001",
        "session_id": "tab-reset",
        "active_flow": "cancellation",
    }
    chat_routes._MEMORY_CANCELLATION_REQUESTS[("CUST-1001", "tab-reset")] = {
        "ticket_id": "TKT-MEM-RESET",
    }

    try:
        with TestClient(create_app(db_path=db_path)) as client:
            response = client.post("/api/demo/reset")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["reset"]["cases"] == 30
        assert payload["reset"]["demo_case_id"] == "#1029"

        with sqlite3.connect(db_path) as connection:
            conversations = connection.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()[0]
            credits = connection.execute("SELECT COUNT(*) FROM credits").fetchone()[0]
        assert conversations == 30
        assert credits > 0
        assert ("CUST-1001", "tab-reset") not in chat_routes._CHAT_STATES
        assert ("CUST-1001", "tab-reset") not in chat_routes._MEMORY_CANCELLATION_REQUESTS
    finally:
        chat_routes._CHAT_STATES.pop(("CUST-1001", "tab-reset"), None)
        chat_routes._MEMORY_CANCELLATION_REQUESTS.pop(("CUST-1001", "tab-reset"), None)


if __name__ == "__main__":
    test_demo_reset_reseeds_database_and_clears_chat_state()
    print("demo reset endpoint tests passed")
