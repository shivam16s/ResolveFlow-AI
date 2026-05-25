from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import IntentClassifier, intent_confidence_component  # noqa: E402


def test_local_classifier_detects_structured_multi_issue_output() -> None:
    classifier = IntentClassifier()
    result = classifier.classify(
        "I was charged twice this month and my internet is still not working. I want to cancel now."
    )

    assert result.intents == [
        "duplicate_charge",
        "billing_dispute",
        "service_outage",
        "cancellation_intent",
    ]
    assert result.primary_intent == "duplicate_charge"
    assert result.cancellation_risk is True
    assert result.urgency == "high"
    assert 0 <= result.confidence <= 1
    assert result.intent_confidence == result.confidence
    assert set(result.intent_probabilities) == {
        "billing_dispute",
        "duplicate_charge",
        "service_outage",
        "router_issue",
        "plan_change",
        "cancellation_intent",
        "refund_request",
        "technician_request",
        "general_query",
    }
    assert abs(sum(result.intent_probabilities.values()) - 1.0) < 0.00001

    output = json.loads(result.to_json())
    assert output["intents"] == result.intents
    assert output["primary_intent"] == "duplicate_charge"
    assert output["intent_probabilities"]["duplicate_charge"] > output["intent_probabilities"]["general_query"]

    component = intent_confidence_component(result)
    assert component.value == result.intent_confidence
    assert component.primary_intent == "duplicate_charge"
    assert component.source == "classifier_softmax"


def test_llm_json_parser_accepts_fenced_json() -> None:
    def fake_llm(_: str) -> str:
        return """```json
{
  "intents": ["service_outage", "technician_request"],
  "primary_intent": "service_outage",
  "cancellation_risk": false,
  "urgency": "medium",
  "confidence": 0.88,
  "intent_probabilities": {
    "billing_dispute": 0.01,
    "duplicate_charge": 0.01,
    "service_outage": 0.72,
    "router_issue": 0.01,
    "plan_change": 0.01,
    "cancellation_intent": 0.01,
    "refund_request": 0.01,
    "technician_request": 0.21,
    "general_query": 0.01
  },
  "emotion": "calm",
  "evidence_terms": ["internet is down", "technician"]
}
```"""

    classifier = IntentClassifier(llm_client=fake_llm)
    result = classifier.classify("My internet is down. Can you send a technician?")

    assert result.intents == ["service_outage", "technician_request"]
    assert result.primary_intent == "service_outage"
    assert result.urgency == "medium"
    assert result.confidence == 0.88
    assert result.intent_confidence == 0.93
    assert intent_confidence_component(result).value == 0.93


def test_llm_json_parser_rejects_unknown_intent() -> None:
    classifier = IntentClassifier(llm_client=lambda _: '{"intents": ["magic"], "primary_intent": "magic"}')

    try:
        classifier.classify("Please do magic.")
    except ValueError as exc:
        assert "unknown intents" in str(exc)
    else:
        raise AssertionError("unknown LLM intent was accepted")


def test_local_classifier_detects_multiple_scenario_intents() -> None:
    classifier = IntentClassifier()
    cases = {
        "case_04_duplicate_outage_cancel.json": {
            "duplicate_charge",
            "billing_dispute",
            "service_outage",
            "cancellation_intent",
        },
        "case_05_pending_cancellation_save.json": {"cancellation_intent", "service_outage"},
        "case_06_technician_slot_after_router_issue.json": {"service_outage", "router_issue"},
        "case_07_vague_angry_customer.json": {"billing_dispute", "service_outage"},
        "case_08_refund_policy_exception.json": {"refund_request", "service_outage"},
        "case_10_plan_downgrade_lockin.json": {"plan_change"},
        "case_16_suspended_account_service_request.json": {"service_outage", "technician_request"},
        "case_18_mobile_bundle_charge_confusion.json": {"billing_dispute", "plan_change"},
    }

    for filename, expected_intents in cases.items():
        scenario = json.loads((ROOT / "docs" / "scenarios" / filename).read_text(encoding="utf-8"))
        message = " ".join(scenario["customer_messages"])
        result = classifier.classify(message)

        assert len(result.intents) >= 2, f"{filename} returned only {result.intents}"
        assert expected_intents.issubset(set(result.intents)), f"{filename}: {result.intents}"


def main() -> None:
    test_local_classifier_detects_structured_multi_issue_output()
    test_llm_json_parser_accepts_fenced_json()
    test_llm_json_parser_rejects_unknown_intent()
    test_local_classifier_detects_multiple_scenario_intents()
    print("PASS intent classifier structured JSON tests")


if __name__ == "__main__":
    main()
