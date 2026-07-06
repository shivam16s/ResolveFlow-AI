from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api.chat_routes import (
    _effective_emotion,
    _empathy_mode_for,
    _evidence_response,
    _health_score_for,
    _relationship_end,
)


def test_angry_response_uses_repair_language() -> None:
    # The streaming chat path falls back to _evidence_response when the LLM is
    # unavailable or its reply fails the safety guards. It must use anger-repair
    # language and honour the credit-guard "already taken" signal.
    response = _evidence_response(
        {"name": "Rahul Sharma", "churn_score": 0.76},
        ["duplicate_charge", "refund_request"],
        "angry",
        [
            {
                "tool_name": "check_duplicate_charge",
                "result": {
                    "duplicate_confirmed": True,
                    "invoice_id": "INV-8821",
                    "duplicate_amount": 1199,
                },
            },
            {
                "tool_name": "apply_credit_guard",
                "result": {
                    "already_taken": True,
                    "matched_action": {"summary": "credit DEMO-CREDIT-001 for INR 599"},
                },
            },
        ],
    )

    assert "I hear how frustrating this is" in response
    assert "already been taken" in response
    assert "I will not run it again" in response


def test_angry_health_and_relationship_are_dynamic() -> None:
    assert _health_score_for("angry", ["duplicate_charge", "refund_request"]) == 28
    assert _relationship_end({"churn_score": 0.76}, ["duplicate_charge", "refund_request"], "angry") == 40
    assert _empathy_mode_for("angry", 35) == "ANGER_REPAIR"
    assert _effective_emotion("This is ridiculous and I am angry", "frustrated") == "angry"


if __name__ == "__main__":
    test_angry_response_uses_repair_language()
    test_angry_health_and_relationship_are_dynamic()
    print("demo chat response tests passed")
