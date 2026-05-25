from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import (  # noqa: E402
    REQUIRED_SLOTS,
    SLOT_SCHEMA,
    IntentSlotSchema,
    MissingSlot,
    SlotDefinition,
    TargetedQuestion,
    build_issue_queue,
    detect_missing_required_slots,
    generate_targeted_question,
    get_slot_schema,
    missing_required_slot_names,
    prioritize_slot,
    slot_schema_as_dict,
    validate_slot_schema,
)
from backend.agent.intent_classifier import ALLOWED_INTENTS  # noqa: E402


def assert_schema_covers_all_intents() -> None:
    validate_slot_schema()
    if set(SLOT_SCHEMA) != set(ALLOWED_INTENTS):
        raise AssertionError(f"slot schema must match intents: {set(SLOT_SCHEMA)} vs {set(ALLOWED_INTENTS)}")
    for intent in ALLOWED_INTENTS:
        schema = get_slot_schema(intent)
        if not isinstance(schema, IntentSlotSchema):
            raise AssertionError(f"schema for {intent} is wrong type: {schema}")
        if not schema.required_slots:
            raise AssertionError(f"{intent} should have at least one required slot")
        if schema.required_slots[0] != "customer_id":
            raise AssertionError(f"{intent} must start with customer_id: {schema.required_slots}")
        if REQUIRED_SLOTS[intent] != schema.required_slots:
            raise AssertionError(f"REQUIRED_SLOTS drift for {intent}: {REQUIRED_SLOTS[intent]} {schema.required_slots}")


def assert_core_intent_slot_contracts() -> None:
    expected_required = {
        "billing_dispute": ["customer_id"],
        "duplicate_charge": ["customer_id", "invoice_id"],
        "service_outage": ["customer_id", "location"],
        "router_issue": ["customer_id"],
        "plan_change": ["customer_id", "requested_plan_id"],
        "cancellation_intent": ["customer_id"],
        "refund_request": ["customer_id", "amount", "reason"],
        "technician_request": ["customer_id", "time_slot"],
        "general_query": ["customer_id"],
    }
    for intent, required_slots in expected_required.items():
        if get_slot_schema(intent).required_slots != required_slots:
            raise AssertionError(f"required slots wrong for {intent}: {get_slot_schema(intent).required_slots}")

    refund_slots = {slot.name: slot for slot in get_slot_schema("refund_request").slots}
    if refund_slots["amount"].value_type != "money":
        raise AssertionError("refund amount should be typed as money")
    if refund_slots["reason"].prompt.lower().count("refund") == 0:
        raise AssertionError("refund reason prompt should be targeted")

    technician_slots = {slot.name: slot for slot in get_slot_schema("technician_request").slots}
    if technician_slots["time_slot"].value_type != "datetime":
        raise AssertionError("technician time_slot should be datetime")

    plan_slots = {slot.name: slot for slot in get_slot_schema("plan_change").slots}
    if plan_slots["requested_plan_id"].aliases != ("new_plan_id", "target_plan_id"):
        raise AssertionError(f"plan aliases wrong: {plan_slots['requested_plan_id']}")


def assert_schema_serialization_is_stable() -> None:
    payload = slot_schema_as_dict()
    duplicate = payload["duplicate_charge"]
    if duplicate["description"] != "Customer reports being charged more than once for the same invoice or service period.":
        raise AssertionError(f"duplicate description wrong: {duplicate}")
    if duplicate["required_slots"] != ["customer_id", "invoice_id"]:
        raise AssertionError(f"duplicate required slots wrong: {duplicate}")
    if duplicate["slots"][0]["name"] != "customer_id" or duplicate["slots"][1]["name"] != "invoice_id":
        raise AssertionError(f"serialized slots should be priority ordered: {duplicate}")
    if "Which invoice" not in duplicate["slots"][1]["prompt"]:
        raise AssertionError(f"duplicate invoice prompt should be targeted: {duplicate}")


def assert_issue_queue_uses_slot_schema() -> None:
    queue = build_issue_queue(["refund_request", "service_outage", "general_query"])
    by_intent = {issue.intent: issue for issue in queue}
    for intent, issue in by_intent.items():
        if issue.required_slots != get_slot_schema(intent).required_slots:
            raise AssertionError(f"issue queue is not using slot schema for {intent}: {issue}")


def assert_detects_missing_required_slots_with_metadata() -> None:
    missing = detect_missing_required_slots(
        "duplicate_charge",
        {"customer_id": "CUST-1001", "invoice_id": "   ", "payment_id": "PAY-1001-A"},
    )
    if len(missing) != 1 or not isinstance(missing[0], MissingSlot):
        raise AssertionError(f"expected one missing slot object: {missing}")
    invoice = missing[0]
    if invoice.to_dict() != {
        "intent": "duplicate_charge",
        "slot": "invoice_id",
        "value_type": "string",
        "priority": 2,
        "prompt": "Which invoice shows the duplicate charge?",
        "aliases": ["bill_id"],
    }:
        raise AssertionError(f"missing invoice metadata wrong: {invoice.to_dict()}")

    refund_missing = detect_missing_required_slots("refund_request", {"customer_id": "CUST-1001", "amount": None})
    if [slot.slot for slot in refund_missing] != ["amount", "reason"]:
        raise AssertionError(f"refund missing slots should be priority ordered: {refund_missing}")

    if missing_required_slot_names("service_outage", {"customer_id": "CUST-1001", "location": "Chennai Zone-04"}):
        raise AssertionError("complete outage slots should not report missing slots")

    if missing_required_slot_names("technician_request", {"customer_id": "CUST-1001", "time_slot": []}) != ["time_slot"]:
        raise AssertionError("empty list should count as missing")


def assert_prioritizes_highest_priority_missing_slot() -> None:
    refund = prioritize_slot("refund_request", {"reason": "duplicate payment"})
    if refund is None:
        raise AssertionError("refund request should have missing slots")
    if refund.to_dict() != {
        "intent": "refund_request",
        "slot": "customer_id",
        "value_type": "string",
        "priority": 1,
        "prompt": "Please share your customer ID so I can check the account.",
        "aliases": ["account_id", "subscriber_id"],
    }:
        raise AssertionError(f"customer_id should be prioritized first: {refund.to_dict()}")

    next_refund = prioritize_slot("refund_request", {"customer_id": "CUST-1001", "amount": "", "reason": ""})
    if next_refund is None or next_refund.slot != "amount":
        raise AssertionError(f"amount should be prioritized before reason: {next_refund}")

    complete = prioritize_slot(
        "duplicate_charge",
        {"customer_id": "CUST-1001", "invoice_id": "INV-8821"},
    )
    if complete is not None:
        raise AssertionError(f"complete required slots should not return a prioritized slot: {complete}")


def assert_generates_one_targeted_question_for_prioritized_slot() -> None:
    question = generate_targeted_question("service_outage", {"customer_id": "CUST-1001", "location": ""})
    if not isinstance(question, TargetedQuestion):
        raise AssertionError(f"expected TargetedQuestion: {question}")
    if question.to_dict() != {
        "intent": "service_outage",
        "slot": "location",
        "question": "Which service location or area is affected?",
        "value_type": "string",
        "priority": 2,
    }:
        raise AssertionError(f"outage targeted question wrong: {question.to_dict()}")

    refund = generate_targeted_question("refund_request", {"customer_id": "CUST-1001", "amount": "", "reason": ""})
    if refund is None or refund.slot != "amount":
        raise AssertionError(f"refund should ask amount first: {refund}")
    if " and " in refund.question.lower():
        raise AssertionError(f"question should ask for one slot only: {refund.question}")

    complete = generate_targeted_question("technician_request", {"customer_id": "CUST-1001", "time_slot": "tomorrow"})
    if complete is not None:
        raise AssertionError(f"complete slots should not generate question: {complete}")


def assert_missing_slot_detection_validates_inputs() -> None:
    for args in (
        ("missing_intent", {}),
        ("duplicate_charge", []),
    ):
        try:
            detect_missing_required_slots(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad missing-slot args were accepted: {args}")


def assert_schema_validation_rejects_bad_definitions() -> None:
    try:
        SlotDefinition(name="", value_type="string", required=True, priority=1, prompt="x")
    except ValueError:
        pass
    else:
        raise AssertionError("empty slot name should be rejected")

    try:
        SlotDefinition(name="x", value_type="integer", required=True, priority=1, prompt="x")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown value_type should be rejected")

    try:
        IntentSlotSchema(
            intent="missing_intent",
            description="bad",
            slots=(SlotDefinition(name="customer_id", value_type="string", required=True, priority=1, prompt="x"),),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unknown intent schema should be rejected")

    try:
        get_slot_schema("missing_intent")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown get_slot_schema intent should be rejected")


def main() -> None:
    assert_schema_covers_all_intents()
    assert_core_intent_slot_contracts()
    assert_schema_serialization_is_stable()
    assert_issue_queue_uses_slot_schema()
    assert_detects_missing_required_slots_with_metadata()
    assert_prioritizes_highest_priority_missing_slot()
    assert_generates_one_targeted_question_for_prioritized_slot()
    assert_missing_slot_detection_validates_inputs()
    assert_schema_validation_rejects_bad_definitions()
    print("slot schema tests passed")


if __name__ == "__main__":
    main()
