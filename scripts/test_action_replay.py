from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent import ActionCandidate, TakenAction, confirm_action_replay, load_taken_actions


class FakeLLM:
    def __init__(self, payload: dict):
        self.payload = payload

    def generate(self, *_args, **_kwargs) -> str:
        return json.dumps(self.payload)


class FailingLLM:
    def generate(self, *_args, **_kwargs) -> str:
        raise RuntimeError("offline")


def test_deterministic_same_invoice_replay() -> None:
    candidate = ActionCandidate(
        action="apply_credit",
        customer_id="CUST-1001",
        target_id="INV-8821",
        amount=599,
        reason="duplicate_charge_credit",
    )
    taken = [
        TakenAction(
            action="apply_credit",
            customer_id="CUST-1001",
            target_id="INV-8821",
            amount=599,
            reason="duplicate_charge_credit",
            source="demo_session",
            summary="demo credit already prepared",
        )
    ]

    decision = confirm_action_replay("please refund me again", candidate, taken, llm_factory=lambda: FailingLLM())

    assert decision.already_taken is True
    assert decision.checked_with_llm is False
    assert decision.matched_action is taken[0]


def test_llm_semantic_replay_when_target_differs() -> None:
    candidate = ActionCandidate(
        action="apply_credit",
        customer_id="CUST-1001",
        target_id="INV-NEW",
        amount=599,
        reason="duplicate_charge_credit",
    )
    taken = [
        TakenAction(
            action="apply_credit",
            customer_id="CUST-1001",
            target_id="CR-OLD",
            amount=250,
            reason="billing adjustment",
            source="credits",
            summary="credit CR-OLD for billing adjustment",
        )
    ]

    decision = confirm_action_replay(
        "did you already process my refund?",
        candidate,
        taken,
        llm_factory=lambda: FakeLLM({"same_action": True, "matched_index": 0, "confidence": 0.86, "reason": "same refund action"}),
    )

    assert decision.already_taken is True
    assert decision.checked_with_llm is True
    assert decision.confidence == 0.86


def test_load_taken_actions_reads_credits() -> None:
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "actions.db"
        connection = sqlite3.connect(db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE credits (
                  credit_id TEXT PRIMARY KEY,
                  customer_id TEXT NOT NULL,
                  amount REAL NOT NULL,
                  reason TEXT NOT NULL,
                  applied_to_invoice TEXT,
                  applied_at DATETIME NOT NULL
                );
                CREATE TABLE tickets (
                  ticket_id TEXT PRIMARY KEY,
                  customer_id TEXT NOT NULL,
                  issue_type TEXT NOT NULL,
                  status TEXT NOT NULL,
                  priority TEXT NOT NULL,
                  created_at DATETIME NOT NULL
                );
                CREATE TABLE audit_logs (
                  customer_id TEXT NOT NULL,
                  action_taken TEXT NOT NULL,
                  created_at DATETIME NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT INTO credits (credit_id, customer_id, amount, reason, applied_to_invoice, applied_at)
                VALUES ('CR-1', 'CUST-1001', 599, 'duplicate_charge_credit', 'INV-8821', '2026-05-25T10:00:00')
                """
            )
            connection.commit()
        finally:
            connection.close()

        actions = load_taken_actions("CUST-1001", db_path=db_path)

    assert len(actions) == 1
    assert actions[0].action == "apply_credit"
    assert actions[0].target_id == "INV-8821"


if __name__ == "__main__":
    test_deterministic_same_invoice_replay()
    test_llm_semantic_replay_when_target_differs()
    test_load_taken_actions_reads_credits()
    print("action replay tests passed")
