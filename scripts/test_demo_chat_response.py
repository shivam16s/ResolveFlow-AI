from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api.test_routes import (
    _demo_response,
    _effective_emotion,
    _empathy_mode_for,
    _health_score_for,
    _relationship_end,
)


def test_angry_response_uses_repair_language() -> None:
    response = _demo_response(
        {
            "classification": {"emotion": "angry"},
            "customer": {"name": "Rahul Sharma", "churn_score": 0.76},
            "intents": ["duplicate_charge", "refund_request"],
            "tool_results": [
                {
                    "tool_name": "check_duplicate_charge",
                    "result": {
                        "duplicate_confirmed": True,
                        "invoice_id": "INV-8821",
                        "duplicate_amount": 1199,
                    },
                },
                {
                    "tool_name": "apply_credit",
                    "result": {
                        "mode": "already_taken",
                        "matched_action": {"summary": "credit DEMO-CREDIT-001 for INR 599"},
                    },
                },
            ],
        }
    )

    assert "I hear how frustrating this is" in response
    assert "already been taken" in response
    assert "I will not run it again" in response


def test_angry_health_and_relationship_are_dynamic() -> None:
    classification = {"emotion": "angry"}
    assert _health_score_for(classification, ["duplicate_charge", "refund_request"]) == 54
    assert _relationship_end({"churn_score": 0.76}, ["duplicate_charge", "refund_request"], classification) == 40
    assert _empathy_mode_for(classification, 35) == "ANGER_REPAIR"
    assert _effective_emotion("This is ridiculous and I am angry", "frustrated") == "angry"


if __name__ == "__main__":
    test_angry_response_uses_repair_language()
    test_angry_health_and_relationship_are_dynamic()
    print("demo chat response tests passed")
