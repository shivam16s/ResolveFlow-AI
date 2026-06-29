from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .intent_classifier import ALLOWED_INTENTS


@dataclass(frozen=True)
class SlotDefinition:
    name: str
    value_type: str
    required: bool
    priority: int
    prompt: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    examples: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        name = self.name.strip()
        value_type = self.value_type.strip()
        prompt = self.prompt.strip()
        aliases = tuple(alias.strip()
                        for alias in self.aliases if alias.strip())
        examples = tuple(example.strip()
                         for example in self.examples if example.strip())
        if not name:
            raise ValueError("slot name must not be empty")
        if value_type not in {"string", "money", "datetime", "boolean"}:
            raise ValueError(
                "slot value_type must be one of string, money, datetime, boolean")
        if self.priority < 1:
            raise ValueError("slot priority must be at least 1")
        if not prompt:
            raise ValueError("slot prompt must not be empty")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "value_type", value_type)
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "examples", examples)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["aliases"] = list(self.aliases)
        payload["examples"] = list(self.examples)
        return payload


@dataclass(frozen=True)
class IntentSlotSchema:
    intent: str
    description: str
    slots: tuple[SlotDefinition, ...]

    def __post_init__(self) -> None:
        intent = self.intent.strip()
        description = self.description.strip()
        if intent not in ALLOWED_INTENTS:
            raise ValueError(f"unknown intent for slot schema: {intent}")
        if not description:
            raise ValueError(
                "intent slot schema description must not be empty")
        if not self.slots:
            raise ValueError(
                "intent slot schema must define at least one slot")
        slot_names = [slot.name for slot in self.slots]
        if len(slot_names) != len(set(slot_names)):
            raise ValueError(f"duplicate slot names for intent {intent}")
        object.__setattr__(self, "intent", intent)
        object.__setattr__(self, "description", description)

    @property
    def required_slots(self) -> list[str]:
        return [slot.name for slot in sorted(self.slots, key=lambda item: item.priority) if slot.required]

    @property
    def optional_slots(self) -> list[str]:
        return [slot.name for slot in sorted(self.slots, key=lambda item: item.priority) if not slot.required]

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "description": self.description,
            "required_slots": self.required_slots,
            "optional_slots": self.optional_slots,
            "slots": [slot.to_dict() for slot in sorted(self.slots, key=lambda item: item.priority)],
        }


@dataclass(frozen=True)
class MissingSlot:
    intent: str
    slot: str
    value_type: str
    priority: int
    prompt: str
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "slot": self.slot,
            "value_type": self.value_type,
            "priority": self.priority,
            "prompt": self.prompt,
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True)
class TargetedQuestion:
    intent: str
    slot: str
    question: str
    value_type: str
    priority: int

    def to_dict(self) -> dict:
        return asdict(self)


CUSTOMER_ID = SlotDefinition(
    name="customer_id",
    value_type="string",
    required=True,
    priority=1,
    prompt="Please share your customer ID so I can check the account.",
    aliases=("account_id", "subscriber_id"),
    examples=("CUST-1001",),
)


SLOT_SCHEMA: dict[str, IntentSlotSchema] = {
    "billing_dispute": IntentSlotSchema(
        intent="billing_dispute",
        description="Customer has a bill, invoice, charge, or payment question that needs account context.",
        slots=(
            CUSTOMER_ID,
            SlotDefinition(
                name="invoice_id",
                value_type="string",
                required=False,
                priority=2,
                prompt="Which invoice or bill are you asking about?",
                aliases=("bill_id",),
                examples=("INV-8821",),
            ),
            SlotDefinition(
                name="billing_period",
                value_type="string",
                required=False,
                priority=3,
                prompt="Which billing month or period should I check?",
                aliases=("bill_month",),
                examples=("May 2026",),
            ),
        ),
    ),
    "duplicate_charge": IntentSlotSchema(
        intent="duplicate_charge",
        description="Customer reports being charged more than once for the same invoice or service period.",
        slots=(
            CUSTOMER_ID,
            SlotDefinition(
                name="invoice_id",
                value_type="string",
                required=True,
                priority=2,
                prompt="Which invoice shows the duplicate charge?",
                aliases=("bill_id",),
                examples=("INV-8821",),
            ),
            SlotDefinition(
                name="payment_id",
                value_type="string",
                required=False,
                priority=3,
                prompt="Do you have either payment transaction ID?",
                aliases=("transaction_id", "txn_id"),
                examples=("PAY-1001-A",),
            ),
        ),
    ),
    "service_outage": IntentSlotSchema(
        intent="service_outage",
        description="Customer reports broadband, Wi-Fi, or service connectivity outage.",
        slots=(
            CUSTOMER_ID,
            SlotDefinition(
                name="location",
                value_type="string",
                required=True,
                priority=2,
                prompt="Which service location or area is affected?",
                aliases=("area", "service_address"),
                examples=("Chennai Zone-04",),
            ),
            SlotDefinition(
                name="started_at",
                value_type="datetime",
                required=False,
                priority=3,
                prompt="When did the outage start?",
                aliases=("outage_start",),
                examples=("today morning",),
            ),
        ),
    ),
    "router_issue": IntentSlotSchema(
        intent="router_issue",
        description="Customer reports modem/router signal, device, or diagnostic issue.",
        slots=(
            CUSTOMER_ID,
            SlotDefinition(
                name="router_symptom",
                value_type="string",
                required=False,
                priority=2,
                prompt="What lights or error symptoms do you see on the router?",
                aliases=("symptom",),
                examples=("red light blinking",),
            ),
        ),
    ),
    "plan_change": IntentSlotSchema(
        intent="plan_change",
        description="Customer requests upgrade, downgrade, bundle, or other subscription plan change.",
        slots=(
            CUSTOMER_ID,
            SlotDefinition(
                name="requested_plan_id",
                value_type="string",
                required=True,
                priority=2,
                prompt="Which plan would you like to switch to?",
                aliases=("new_plan_id", "target_plan_id"),
                examples=("fiber_starter_100",),
            ),
            SlotDefinition(
                name="price_speed_confirmed",
                value_type="boolean",
                required=False,
                priority=3,
                prompt="Please confirm you accept the new monthly price and speed.",
                aliases=("confirmed_price_speed",),
                examples=("yes",),
            ),
        ),
    ),
    "cancellation_intent": IntentSlotSchema(
        intent="cancellation_intent",
        description="Customer says they want to cancel, disconnect, or close the account.",
        slots=(
            CUSTOMER_ID,
            SlotDefinition(
                name="cancellation_reason",
                value_type="string",
                required=False,
                priority=2,
                prompt="What is the main reason you want to cancel?",
                aliases=("reason",),
                examples=("outage not resolved",),
            ),
        ),
    ),
    "refund_request": IntentSlotSchema(
        intent="refund_request",
        description="Customer requests a refund, reimbursement, service credit, or exception.",
        slots=(
            CUSTOMER_ID,
            SlotDefinition(
                name="amount",
                value_type="money",
                required=True,
                priority=2,
                prompt="What refund or credit amount are you requesting?",
                aliases=("refund_amount", "credit_amount"),
                examples=("300",),
            ),
            SlotDefinition(
                name="reason",
                value_type="string",
                required=True,
                priority=3,
                prompt="What is the reason for the refund request?",
                aliases=("refund_reason",),
                examples=("duplicate payment",),
            ),
            SlotDefinition(
                name="payment_id",
                value_type="string",
                required=False,
                priority=4,
                prompt="Which payment should the refund be linked to?",
                aliases=("transaction_id",),
                examples=("PAY-1001-A",),
            ),
        ),
    ),
    "technician_request": IntentSlotSchema(
        intent="technician_request",
        description="Customer asks for a technician visit, engineer dispatch, or appointment.",
        slots=(
            CUSTOMER_ID,
            SlotDefinition(
                name="time_slot",
                value_type="datetime",
                required=True,
                priority=2,
                prompt="Which technician visit time slot works for you?",
                aliases=("appointment_slot",),
                examples=("2026-05-24 10:00-13:00",),
            ),
        ),
    ),
    "general_query": IntentSlotSchema(
        intent="general_query",
        description="General account or support question with no specific operational action yet.",
        slots=(
            CUSTOMER_ID,
        ),
    ),
}


REQUIRED_SLOTS = {
    intent: schema.required_slots
    for intent, schema in SLOT_SCHEMA.items()
}


def get_slot_schema(intent: str) -> IntentSlotSchema:
    normalized = intent.strip()
    if normalized not in SLOT_SCHEMA:
        raise ValueError(f"unknown intent: {intent}")
    return SLOT_SCHEMA[normalized]


def slot_schema_as_dict() -> dict[str, dict]:
    return {intent: schema.to_dict() for intent, schema in SLOT_SCHEMA.items()}


def detect_missing_required_slots(intent: str, slots: dict[str, object] | None = None) -> list[MissingSlot]:
    if slots is not None and not isinstance(slots, dict):
        raise ValueError("slots must be a dict when provided")
    provided_slots = slots or {}
    schema = get_slot_schema(intent)
    missing = []
    for slot in sorted(schema.slots, key=lambda item: item.priority):
        if not slot.required:
            continue
        if _slot_value_present(provided_slots.get(slot.name)):
            continue
        missing.append(
            MissingSlot(
                intent=schema.intent,
                slot=slot.name,
                value_type=slot.value_type,
                priority=slot.priority,
                prompt=slot.prompt,
                aliases=slot.aliases,
            )
        )
    return missing


def missing_required_slot_names(intent: str, slots: dict[str, object] | None = None) -> list[str]:
    return [missing.slot for missing in detect_missing_required_slots(intent, slots)]


def prioritize_slot(intent: str, slots: dict[str, object] | None = None) -> MissingSlot | None:
    missing_slots = detect_missing_required_slots(intent, slots)
    if not missing_slots:
        return None
    return min(missing_slots, key=lambda missing: missing.priority)


def generate_targeted_question(intent: str, slots: dict[str, object] | None = None) -> TargetedQuestion | None:
    missing = prioritize_slot(intent, slots)
    if missing is None:
        return None
    return TargetedQuestion(
        intent=missing.intent,
        slot=missing.slot,
        question=missing.prompt,
        value_type=missing.value_type,
        priority=missing.priority,
    )


def validate_slot_schema() -> None:
    missing = set(ALLOWED_INTENTS) - set(SLOT_SCHEMA)
    extra = set(SLOT_SCHEMA) - set(ALLOWED_INTENTS)
    if missing or extra:
        raise ValueError(
            f"slot schema mismatch; missing={sorted(missing)}, extra={sorted(extra)}")
    for schema in SLOT_SCHEMA.values():
        required_priorities = [
            slot.priority for slot in schema.slots if slot.required]
        if required_priorities != sorted(required_priorities):
            raise ValueError(
                f"required slots are not priority ordered for {schema.intent}")


def _slot_value_present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True
