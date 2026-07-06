from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api import create_app  # noqa: E402
from backend.db.seed_demo_dashboard import seed_demo_dashboard  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_verified_outage_trigger_finds_customers_by_location() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-proactive-outage-")) / "resolveflow.db"
    seed_demo_dashboard(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        customer = connection.execute(
            "SELECT customer_id, location FROM customers ORDER BY customer_id LIMIT 1"
        ).fetchone()

    with TestClient(create_app(db_path=db_path)) as client:
        response = client.post(
            "/api/outages/trigger",
            json={
                "outage_id": "OUT-TEST-VERIFIED",
                "location": customer["location"],
                "duration_hours": 7,
                "verified": True,
                "credit_amount": 100,
            },
        )
        proactive_response = client.get("/api/agent-desk/proactive")
        chat_response = client.get(
            "/api/chat/session/messages",
            params={
                "customer_id": customer["customer_id"],
                "session_id": "judge-tab",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["outage_id"] == "OUT-TEST-VERIFIED"
    assert payload["verified"] is True
    assert payload["affected_customer_count"] >= 1
    assert any(
        item["customer_id"] == customer["customer_id"]
        for item in payload["affected_customers"]
    )
    assert payload["proactive_contacts"]
    contact = next(
        item for item in payload["proactive_contacts"]
        if item["customer_id"] == customer["customer_id"]
    )
    assert contact["status"] == "credited"
    assert contact["credit"]["policy_name"] == "service_credit_dag"
    assert proactive_response.status_code == 200, proactive_response.text
    proactive_contacts = proactive_response.json()["contacts"]
    assert any(
        item["session_id"] == contact["session_id"]
        and item["status"] == "credited"
        for item in proactive_contacts
    )
    assert chat_response.status_code == 200, chat_response.text
    chat_messages = chat_response.json()["messages"]
    assert any(
        item.get("proactive") is True
        and item.get("source_session_id") == contact["session_id"]
        for item in chat_messages
    )

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        outage = connection.execute(
            "SELECT affected_customers, verified FROM outages WHERE outage_id = ?",
            ("OUT-TEST-VERIFIED",),
        ).fetchone()
        credit = connection.execute(
            """
            SELECT credit_id, amount, reason
            FROM credits
            WHERE customer_id = ?
              AND reason LIKE ?
            """,
            (customer["customer_id"], "%OUT-TEST-VERIFIED%"),
        ).fetchone()
        conversation = connection.execute(
            """
            SELECT messages, tools_called
            FROM conversations
            WHERE session_id = ?
            """,
            (contact["session_id"],),
        ).fetchone()
    assert outage["verified"] == 1
    assert customer["customer_id"] in json.loads(outage["affected_customers"])
    assert credit["amount"] == 100
    messages = json.loads(conversation["messages"])
    assert messages[0]["proactive"] is True
    assert "verified outage" in messages[0]["content"].lower()
    tools_called = json.loads(conversation["tools_called"])
    assert tools_called[0]["tool_name"] == "apply_credit"


def test_verified_outage_trigger_fuzzy_matches_location_formatting() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-proactive-outage-fuzzy-")) / "resolveflow.db"
    seed_demo_dashboard(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        customer = connection.execute(
            """
            SELECT customer_id, location
            FROM customers
            WHERE lower(location) LIKE '%zone%'
            ORDER BY customer_id
            LIMIT 1
            """
        ).fetchone()

    fuzzy_location = customer["location"].lower().replace("zone 0", "z-")

    with TestClient(create_app(db_path=db_path)) as client:
        response = client.post(
            "/api/outages/trigger",
            json={
                "outage_id": "OUT-TEST-FUZZY",
                "location": fuzzy_location,
                "duration_hours": 7,
                "verified": True,
                "credit_amount": 100,
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert any(
        item["customer_id"] == customer["customer_id"]
        for item in payload["affected_customers"]
    ), payload

    with sqlite3.connect(db_path) as connection:
        affected = connection.execute(
            "SELECT affected_customers FROM outages WHERE outage_id = ?",
            ("OUT-TEST-FUZZY",),
        ).fetchone()[0]
    assert customer["customer_id"] in json.loads(affected)


if __name__ == "__main__":
    test_verified_outage_trigger_finds_customers_by_location()
    test_verified_outage_trigger_fuzzy_matches_location_formatting()
    print("proactive outage tests passed")
