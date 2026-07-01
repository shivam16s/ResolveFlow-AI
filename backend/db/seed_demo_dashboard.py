from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from .init_db import DEFAULT_DB_PATH
    from .reset import reset_to_initial_state
except ImportError:  # pragma: no cover - keeps direct script execution working.
    from init_db import DEFAULT_DB_PATH
    from reset import reset_to_initial_state


DEMO_CASE_ID = "#1029"
DEMO_SESSION_ID = "demo-rahul-1029"
DEMO_CUSTOMER_ID = "CUST-1001"

ISSUE_SETS = [
    ["billing", "outage", "cancellation"],
    ["billing", "refund"],
    ["outage", "technician_request"],
    ["plan_change"],
    ["cancellation", "retention"],
    ["billing", "duplicate_charge"],
    ["outage"],
    ["refund_exception"],
    ["router_diagnostic", "outage"],
    ["billing"],
]

TOOL_LIBRARY = {
    "billing": ["lookup_customer", "get_invoice_history", "check_duplicate_charge", "retrieve_policy"],
    "outage": ["lookup_customer", "check_outage_status", "run_router_diagnostic", "retrieve_policy"],
    "cancellation": ["lookup_customer", "retrieve_policy", "change_plan"],
    "refund": ["lookup_customer", "get_invoice_history", "retrieve_policy", "apply_credit"],
    "plan_change": ["lookup_customer", "retrieve_policy", "change_plan"],
    "technician_request": ["lookup_customer", "check_outage_status", "schedule_technician"],
}


def seed_demo_dashboard(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    reset_to_initial_state(db_path)
    now = datetime.now().replace(microsecond=0)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = MEMORY")
        _seed_demo_customer_risk(connection)
        _seed_demo_billing_history(connection)
        _seed_demo_conversations(connection, now)
        _seed_demo_credits(connection, now)
        _seed_demo_tickets(connection, now)
        _seed_demo_memory(connection, now)
        connection.commit()

    return _summary(db_path)


def _seed_demo_customer_risk(connection: sqlite3.Connection) -> None:
    for index in range(1, 21):
        customer_id = f"CUST-{1000 + index}"
        if index <= 14:
            connection.execute(
                """
                UPDATE customers
                SET risk_level = ?, churn_score = ?
                WHERE customer_id = ?
                """,
                ("high", 0.72 + (index % 5) * 0.04, customer_id),
            )
        else:
            connection.execute(
                """
                UPDATE customers
                SET risk_level = ?, churn_score = ?
                WHERE customer_id = ?
                """,
                ("medium" if index % 2 == 0 else "low",
                 0.24 + (index % 5) * 0.06, customer_id),
            )


def _seed_demo_billing_history(connection: sqlite3.Connection) -> None:
    """Add ~10 months of prior invoices/payments per customer for a rich chart.

    The canonical May-2026 invoices seeded by ``seed_billing`` (including
    CUST-1001's ``INV-8821`` duplicate) are left untouched. Duplicate detection
    matches on the exact ``2026-05-18`` date, so this older history (other dates)
    can never collide with the duplicate-charge scenario. Demo-only: the test
    fixtures build their DBs from ``seed_billing`` directly and never see this.
    """
    base_amounts = {
        row[0]: float(row[1])
        for row in connection.execute(
            "SELECT customer_id, amount FROM invoices"
        ).fetchall()
    }
    months = [
        (2025, 7), (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
        (2026, 1), (2026, 2), (2026, 3), (2026, 4),
    ]
    methods = ["upi", "credit_card", "netbanking", "auto_debit", "wallet"]
    invoices = []
    payments = []
    for index in range(1, 21):
        number = 1000 + index
        customer_id = f"CUST-{number}"
        base = base_amounts.get(customer_id, 1199.0)
        for month_index, (year, month) in enumerate(months):
            # Deterministic +-8% variation so the chart reads like real usage.
            amount = round(base * (1 + ((month_index % 5) - 2) * 0.04))
            date_str = f"{year}-{month:02d}-09"
            invoice_id = f"INV-{number}-H{year}{month:02d}"
            payment_id = f"PAY-{number}-H{year}{month:02d}"
            method = methods[(index + month_index) % len(methods)]
            invoices.append((invoice_id, customer_id, amount, date_str, "paid", payment_id))
            payments.append((payment_id, customer_id, amount, f"{date_str}T10:00:00", method, 0))
    connection.executemany(
        "INSERT OR IGNORE INTO payments (payment_id, customer_id, amount, date, method, duplicate_flag) VALUES (?, ?, ?, ?, ?, ?)",
        payments,
    )
    connection.executemany(
        "INSERT OR IGNORE INTO invoices (invoice_id, customer_id, amount, date, status, payment_id) VALUES (?, ?, ?, ?, ?, ?)",
        invoices,
    )


def _seed_demo_conversations(connection: sqlite3.Connection, now: datetime) -> None:
    rows = [_rahul_case(now)]
    for index in range(1, 30):
        customer_id = f"CUST-{1001 + (index % 20)}"
        case_id = f"#{1029 + index}"
        status = "resolved" if index <= 25 else "escalated"
        rows.append(_case_record(index, case_id, customer_id, status, now))

    for row in rows:
        _insert_conversation(connection, row)
        _insert_audit_log(connection, row)
        if row["handoff_required"]:
            _insert_handoff(connection, row)


def _rahul_case(now: datetime) -> dict[str, Any]:
    created_at = now - timedelta(minutes=15)
    tools = [
        _tool("lookup_customer", {"customer_id": DEMO_CUSTOMER_ID}, {
              "risk_level": "high"}, created_at, 1),
        _tool("get_invoice_history", {"customer_id": DEMO_CUSTOMER_ID}, {
              "duplicate_charge": True}, created_at, 2),
        _tool("check_duplicate_charge", {"invoice_id": "INV-8821"}, {
              "duplicate_payment_ids": ["PAY-1001-A", "PAY-1001-B"]}, created_at, 3),
        _tool("check_outage_status", {"location": "Chennai Zone-04"},
              {"verified": True, "duration_hours": 7.0}, created_at, 4),
        _tool("retrieve_policy", {"query": "duplicate charge outage credit cancellation"}, {
              "confidence": 0.95}, created_at, 5),
        _tool("run_router_diagnostic", {"customer_id": DEMO_CUSTOMER_ID}, {
              "router_status": "degraded"}, created_at, 6),
    ]
    return {
        "session_id": DEMO_SESSION_ID,
        "case_id": DEMO_CASE_ID,
        "customer_id": DEMO_CUSTOMER_ID,
        "messages": [
            _message(
                "user", "I was charged twice this month, my internet is still down, and I want to cancel.", created_at, 1),
            _message("assistant", "I found three issues: duplicate billing, verified outage, and cancellation risk. I am checking the policy path before taking action.", created_at, 2),
            _message(
                "user", "Do not just tell me to restart the router again.", created_at, 3),
            _message("assistant", "Understood. I verified the network outage first and will only ask for the router step needed to complete dispatch eligibility.", created_at, 4),
        ],
        "intents": ["billing", "outage", "cancellation"],
        "slots": {
            "invoice_id": "INV-8821",
            "location": "Chennai Zone-04",
            "cancellation_reason": "service reliability",
        },
        "tools_called": tools,
        "health_scores": [
            {"turn": 1, "score": 29,
                "label": "Customer is angry and mentions cancellation."},
            {"turn": 2, "score": 38, "label": "All issues acknowledged."},
            {"turn": 3, "score": 42, "label": "Loop risk detected and corrected."},
            {"turn": 4, "score": 46, "label": "WAITING on one guided verification step."},
        ],
        "final_status": "active",
        "relationship_score_start": 29,
        "relationship_score_end": 58,
        "relationship_delta": 29,
        "created_at": created_at.isoformat(),
        "completed_at": None,
        "evidence_used": [
            {"type": "invoice", "id": "INV-8821",
                "finding": "duplicate payment pair detected"},
            {"type": "outage", "id": "OUT-CHN-04-20260520",
                "finding": "verified outage longer than 6 hours"},
            {"type": "policy", "id": "POL-SVC-CREDIT",
                "finding": "credit allowed after verified outage and duplicate charge validation"},
        ],
        "action_taken": [
            {"action": "held_customer_in_guided_action", "state": "WAITING"},
            {"action": "prepared_credit", "amount": 599},
        ],
        "policy_dag_path": [
            {"node_id": "outage_verified", "result": "verified"},
            {"node_id": "duration_gte_6h", "result": "7.0h"},
            {"node_id": "no_prior_credit_this_cycle", "result": True},
            {"node_id": "duplicate_charge_verified", "result": True},
            {"node_id": "credit_amount_within_limit", "result": "INR 599"},
            {"node_id": "manager_approval_not_required", "result": True},
        ],
        "ujcs": 0.96,
        "policy_status": "compliant",
        "health_score": 46,
        "handoff_required": 0,
    }


def _case_record(index: int, case_id: str, customer_id: str, status: str, now: datetime) -> dict[str, Any]:
    issue_set = ISSUE_SETS[index % len(ISSUE_SETS)]
    created_at = now - timedelta(days=index %
                                 7, hours=1 + index, minutes=index * 3)
    completed_at = created_at + timedelta(minutes=18 + index)
    start_score = 34 + (index * 7) % 36
    end_score = 72 + \
        (index * 5) % 23 if status == "resolved" else 31 + index % 12
    tools = _tools_for(issue_set, created_at)
    return {
        "session_id": f"demo-session-{index:02d}",
        "case_id": case_id,
        "customer_id": customer_id,
        "messages": [
            _message("user", _user_prompt(issue_set), created_at, 1),
            _message(
                "assistant", "I have identified the issue and am checking the required policy evidence.", created_at, 2),
            _message("assistant", "The required verification steps are complete." if status ==
                     "resolved" else "This needs a specialist because one verification step failed.", created_at, 3),
        ],
        "intents": issue_set,
        "slots": {"customer_id": customer_id},
        "tools_called": tools,
        "health_scores": [
            {"turn": 1, "score": start_score, "label": "Initial risk estimate"},
            {"turn": 2, "score": min(100, start_score + 12),
             "label": "Evidence retrieved"},
            {"turn": 3, "score": end_score, "label": "Final session health"},
        ],
        "final_status": status,
        "relationship_score_start": start_score,
        "relationship_score_end": end_score,
        "relationship_delta": end_score - start_score,
        "created_at": created_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "evidence_used": [
            {"type": "policy", "id": "resolveflow-policy",
                "finding": "required prerequisite nodes visited"},
            {"type": "tool", "name": tools[-1]["tool_name"],
                "finding": "verification completed"},
        ],
        "action_taken": [{"action": "resolved_by_ai" if status == "resolved" else "queued_for_handoff"}],
        "policy_dag_path": [
            {"node_id": "customer_verified", "result": True},
            {"node_id": "policy_retrieved", "result": True},
            {"node_id": "evidence_checked", "result": True},
            {"node_id": "action_allowed", "result": status == "resolved"},
        ],
        "ujcs": 0.91 if status == "resolved" else 0.74,
        "policy_status": "compliant" if index not in {27, 29} else "needs_review",
        "health_score": end_score,
        "handoff_required": 1 if status == "escalated" else 0,
    }


def _seed_demo_credits(connection: sqlite3.Connection, now: datetime) -> None:
    amounts = [599] + [2070] * 22 + [2061]
    for index, amount in enumerate(amounts, start=1):
        customer_id = DEMO_CUSTOMER_ID if index == 1 else f"CUST-{1001 + (index % 20)}"
        applied_at = now - timedelta(days=index % 7, hours=index)
        connection.execute(
            """
            INSERT INTO credits (credit_id, customer_id, amount, reason, policy_id, applied_at, applied_to_invoice)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"DEMO-CREDIT-{index:03d}",
                customer_id,
                amount,
                "Demo service recovery credit",
                None,
                applied_at.isoformat(),
                "INV-8821" if index == 1 else None,
            ),
        )


def _seed_demo_tickets(connection: sqlite3.Connection, now: datetime) -> None:
    for index in range(1, 19):
        status = "resolved" if index <= 12 else "in_progress" if index <= 16 else "escalated"
        created_at = now - timedelta(days=index % 7, hours=index * 2)
        connection.execute(
            """
            INSERT INTO tickets (ticket_id, customer_id, issue_type, status, priority, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"DEMO-TICKET-{index:03d}",
                f"CUST-{1001 + (index % 20)}",
                ISSUE_SETS[index % len(ISSUE_SETS)][0],
                status,
                "high" if index % 3 == 0 else "medium",
                created_at.isoformat(),
                (created_at + timedelta(hours=4)
                 ).isoformat() if status == "resolved" else None,
            ),
        )


def _seed_demo_memory(connection: sqlite3.Connection, now: datetime) -> None:
    memories = [
        ("MEM-DEMO-RAHUL-001", DEMO_CUSTOMER_ID, "stable", "Rahul Sharma has churn risk tied to repeated Chennai Zone-04 outages.",
         ["customer", "churn", "outage"], DEMO_SESSION_ID),
        ("MEM-DEMO-RAHUL-002", DEMO_CUSTOMER_ID, "episodic",
         "Rahul had duplicate payments PAY-1001-A and PAY-1001-B for invoice INV-8821.", ["billing", "duplicate_charge"], DEMO_SESSION_ID),
        ("MEM-DEMO-RAHUL-003", DEMO_CUSTOMER_ID, "session",
         "Current session is waiting on one router diagnostic verification before dispatch.", ["guided_action", "waiting"], DEMO_SESSION_ID),
    ]
    for index, (memory_id, customer_id, memory_type, content, tags, session_id) in enumerate(memories):
        timestamp = now - timedelta(minutes=10 - index)
        connection.execute(
            """
            INSERT INTO memory_store (
                memory_id, customer_id, memory_type, content, entity_tags, session_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (memory_id, customer_id, memory_type, content, _json(tags),
             session_id, timestamp.isoformat(), timestamp.isoformat()),
        )


def _insert_conversation(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO conversations (
            session_id, customer_id, messages, intents, slots, tools_called, health_scores,
            final_status, relationship_score_start, relationship_score_end, relationship_delta,
            created_at, completed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["session_id"],
            row["customer_id"],
            _json(row["messages"]),
            _json(row["intents"]),
            _json(row["slots"]),
            _json(row["tools_called"]),
            _json(row["health_scores"]),
            row["final_status"],
            row["relationship_score_start"],
            row["relationship_score_end"],
            row["relationship_delta"],
            row["created_at"],
            row["completed_at"],
        ),
    )


def _insert_audit_log(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO audit_logs (
            case_id, customer_id, session_id, tools_called, evidence_used, action_taken,
            policy_dag_path, ujcs, policy_status, health_score, handoff_required, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["case_id"],
            row["customer_id"],
            row["session_id"],
            _json(row["tools_called"]),
            _json(row["evidence_used"]),
            _json(row["action_taken"]),
            _json(row["policy_dag_path"]),
            row["ujcs"],
            row["policy_status"],
            row["health_score"],
            row["handoff_required"],
            row["created_at"],
        ),
    )


def _insert_handoff(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO human_handoff_queue (
            handoff_id, case_id, customer_id, context_card, handoff_reason, status, created_at, assigned_to
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"HANDOFF-{row['case_id'].lstrip('#')}",
            row["case_id"],
            row["customer_id"],
            _json({"case_id": row["case_id"], "issues_remaining": row["intents"],
                  "recommended_opening_line": "I have the full policy trail and can take over from here."}),
            "Verification failed or customer risk threshold crossed",
            "waiting",
            row["created_at"],
            None,
        ),
    )


def _tools_for(issue_set: list[str], created_at: datetime) -> list[dict[str, Any]]:
    names = ["lookup_customer"]
    for issue in issue_set:
        names.extend(TOOL_LIBRARY.get(issue, ["retrieve_policy"]))
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return [_tool(name, {"source": "demo_seed"}, {"status": "verified"}, created_at, index) for index, name in enumerate(seen, start=1)]


def _tool(name: str, args: dict[str, Any], result: dict[str, Any], created_at: datetime, offset: int) -> dict[str, Any]:
    return {
        "tool_name": name,
        "args": args,
        "result": result,
        "timestamp": (created_at + timedelta(minutes=offset)).isoformat(),
        "success": True,
    }


def _message(role: str, content: str, created_at: datetime, turn: int) -> dict[str, Any]:
    return {
        "role": role,
        "content": content,
        "timestamp": (created_at + timedelta(minutes=turn)).isoformat(),
        "turn": turn,
    }


def _user_prompt(issue_set: list[str]) -> str:
    labels = ", ".join(issue_set).replace("_", " ")
    return f"I need help with {labels} on my ConnectCare account."


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _summary(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        total_cases = connection.execute(
            "SELECT COUNT(*) FROM conversations").fetchone()[0]
        resolved = connection.execute(
            "SELECT COUNT(*) FROM conversations WHERE final_status = 'resolved'").fetchone()[0]
        escalated = connection.execute(
            "SELECT COUNT(*) FROM conversations WHERE final_status = 'escalated'").fetchone()[0]
        credit_total = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM credits").fetchone()[0]
        high_risk = connection.execute(
            "SELECT COUNT(*) FROM customers WHERE risk_level IN ('high', 'critical') OR churn_score >= 0.70"
        ).fetchone()[0]
    return {
        "db_path": str(db_path),
        "cases": total_cases,
        "resolved": resolved,
        "escalated": escalated,
        "credit_total_inr": credit_total,
        "high_risk_customers": high_risk,
        "demo_case_id": DEMO_CASE_ID,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed dashboard-ready ResolveFlow demo data.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path. Defaults to {DEFAULT_DB_PATH}",
    )
    args = parser.parse_args()
    summary = seed_demo_dashboard(args.db_path)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
