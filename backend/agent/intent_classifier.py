from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable


ALLOWED_INTENTS = (
    "billing_dispute",
    "duplicate_charge",
    "service_outage",
    "router_issue",
    "plan_change",
    "cancellation_intent",
    "refund_request",
    "technician_request",
    "general_query",
)

URGENCY_LEVELS = ("low", "medium", "high")

INTENT_PRIORITY = {
    "duplicate_charge": 1,
    "billing_dispute": 2,
    "service_outage": 3,
    "router_issue": 4,
    "cancellation_intent": 5,
    "refund_request": 6,
    "technician_request": 7,
    "plan_change": 8,
    "general_query": 9,
}


@dataclass(frozen=True)
class IntentClassification:
    intents: list[str]
    primary_intent: str
    cancellation_risk: bool
    urgency: str
    confidence: float
    emotion: str
    evidence_terms: list[str]
    intent_probabilities: dict[str, float] = field(default_factory=dict)
    intent_confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


class IntentClassifier:
    """Telecom support intent classifier with strict structured JSON output."""

    def __init__(self, llm_client: Callable[[str], str] | None = None) -> None:
        self.llm_client = llm_client

    def classify(self, message: str) -> IntentClassification:
        normalized = message.strip()
        if not normalized:
            raise ValueError("message must not be empty")

        if self.llm_client is not None:
            raw_output = self.llm_client(self.build_prompt(normalized))
            return self._classification_from_llm_output(raw_output)

        return self._classification_from_rules(normalized)

    def classify_json(self, message: str) -> str:
        return self.classify(message).to_json()

    @staticmethod
    def build_prompt(message: str) -> str:
        allowed = json.dumps(list(ALLOWED_INTENTS))
        return (
            "You are an intent detector for a telecom support agent.\n"
            "Given the customer message, extract ALL distinct issues.\n"
            f"Return a JSON object with intents from this allowed list only: {allowed}.\n"
            "Only extract an intent if the customer is actively experiencing or requesting it. "
            "If the customer explicitly denies an intent (e.g. 'I do not want to cancel'), do NOT include it.\n"
            "Use this exact schema:\n"
            "{\n"
            '  "intents": ["intent1", "intent2"],\n'
            '  "primary_intent": "intent1",\n'
            '  "cancellation_risk": true,\n'
            '  "urgency": "high",\n'
            '  "confidence": 0.0,\n'
            '  "emotion": "neutral",\n'
            '  "evidence_terms": ["short phrase from customer message"]\n'
            "}\n"
            "Do not include markdown, commentary, or extra keys.\n"
            f"Customer message: {message}"
        )

    def _classification_from_llm_output(self, raw_output: str) -> IntentClassification:
        payload = self._extract_json_object(raw_output)
        return self._validate_payload(payload)

    @staticmethod
    def _extract_json_object(raw_output: str) -> dict:
        cleaned = raw_output.strip()
        fenced = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
        if fenced:
            cleaned = fenced.group(1)

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM output was not valid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise ValueError("LLM output must be a JSON object")
        return payload

    @staticmethod
    def _validate_payload(payload: dict) -> IntentClassification:
        intents = _dedupe(payload.get("intents", []))
        if not intents:
            intents = ["general_query"]

        unknown = [intent for intent in intents if intent not in ALLOWED_INTENTS]
        if unknown:
            raise ValueError(f"unknown intents from LLM: {unknown}")

        primary_intent = payload.get(
            "primary_intent") or _primary_intent(intents)
        if primary_intent not in intents:
            primary_intent = _primary_intent(intents)

        urgency = payload.get("urgency", "low")
        if urgency not in URGENCY_LEVELS:
            urgency = "low"

        confidence = float(payload.get("confidence", 0.7))
        confidence = max(0.0, min(1.0, confidence))
        intent_probabilities = _clean_probability_payload(
            payload.get("intent_probabilities"))
        if not intent_probabilities:
            intent_probabilities = _probabilities_from_confidence(
                primary_intent, confidence)
        intent_confidence = _intent_confidence_from_probabilities(
            intents, intent_probabilities)

        emotion = str(payload.get("emotion", "neutral")
                      ).strip().lower() or "neutral"
        evidence_terms = [str(term).strip() for term in payload.get(
            "evidence_terms", []) if str(term).strip()]

        return IntentClassification(
            intents=intents,
            primary_intent=primary_intent,
            cancellation_risk=bool(payload.get(
                "cancellation_risk", "cancellation_intent" in intents)),
            urgency=urgency,
            confidence=round(confidence, 2),
            emotion=emotion,
            evidence_terms=evidence_terms,
            intent_probabilities=intent_probabilities,
            intent_confidence=intent_confidence,
        )

    def _classification_from_rules(self, message: str) -> IntentClassification:
        text = message.lower()
        matches: dict[str, list[str]] = {}

        keyword_groups = {
            "duplicate_charge": (
                "charged twice",
                "double charged",
                "duplicate charge",
                "two payments",
                "twice",
            ),
            "billing_dispute": (
                "bill",
                "billing",
                "charge",
                "charged",
                "charging",
                "invoice",
                "payment",
                "paid",
                "paying",
            ),
            "service_outage": (
                "internet",
                "outage",
                "outages",
                "down",
                "not working",
                "nothing works",
                "connection",
                "wifi",
                "stopped",
            ),
            "router_issue": ("router", "modem", "signal", "blinking", "red light"),
            "plan_change": (
                "upgrade",
                "downgrade",
                "plan",
                "faster",
                "cheapest",
                "speed",
                "activate",
                "activation",
                "gbps",
                "1 gbps",
                "bundle",
                "add-on",
                "add-ons",
                "broadband only",
            ),
            "cancellation_intent": ("cancel", "cancellation", "disconnect", "stop service", "close my account"),
            "refund_request": ("refund", "money back", "credit", "compensate", "reimburse"),
            "technician_request": ("technician", "engineer", "visit", "appointment", "dispatch", "send someone"),
        }

        for intent, keywords in keyword_groups.items():
            found = [keyword for keyword in keywords if keyword in text]
            if found:
                matches[intent] = found

        if not matches:
            matches["general_query"] = ["general support request"]

        intents = sorted(matches, key=lambda item: INTENT_PRIORITY[item])
        cancellation_risk = "cancellation_intent" in intents
        urgency = _infer_urgency(text, intents)
        emotion = _infer_emotion(text)
        evidence_terms = _dedupe(
            term for terms in matches.values() for term in terms)
        intent_logits = _rule_intent_logits(matches)
        intent_probabilities = _softmax(intent_logits)
        intent_confidence = _intent_confidence_from_probabilities(
            intents, intent_probabilities)
        confidence = intent_confidence

        return IntentClassification(
            intents=intents,
            primary_intent=_primary_intent(intents),
            cancellation_risk=cancellation_risk,
            urgency=urgency,
            confidence=round(confidence, 2),
            emotion=emotion,
            evidence_terms=evidence_terms[:8],
            intent_probabilities=intent_probabilities,
            intent_confidence=intent_confidence,
        )


def _primary_intent(intents: Iterable[str]) -> str:
    return min(intents, key=lambda intent: INTENT_PRIORITY[intent])


def _dedupe(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _rule_intent_logits(matches: dict[str, list[str]]) -> dict[str, float]:
    logits = {intent: -1.5 for intent in ALLOWED_INTENTS}
    for intent, terms in matches.items():
        if intent == "general_query":
            logits[intent] = 1.5
            continue
        priority_boost = max(0.0, (10 - INTENT_PRIORITY[intent]) * 0.03)
        logits[intent] = 1.0 + (0.45 * len(terms)) + priority_boost
    return logits


def _softmax(logits: dict[str, float]) -> dict[str, float]:
    if set(logits) != set(ALLOWED_INTENTS):
        raise ValueError("softmax logits must cover all allowed intents")
    max_logit = max(logits.values())
    exponentials = {intent: math.exp(value - max_logit)
                    for intent, value in logits.items()}
    denominator = sum(exponentials.values())
    return {
        intent: round(exponentials[intent] / denominator, 6)
        for intent in ALLOWED_INTENTS
    }


def _intent_confidence_from_probabilities(intents: list[str], probabilities: dict[str, float]) -> float:
    confidence = sum(probabilities.get(intent, 0.0) for intent in intents)
    return round(max(0.0, min(1.0, confidence)), 2)


def _clean_probability_payload(raw_probabilities: object) -> dict[str, float]:
    if not isinstance(raw_probabilities, dict):
        return {}
    probabilities = {}
    for intent in ALLOWED_INTENTS:
        try:
            value = float(raw_probabilities.get(intent, 0.0))
        except (TypeError, ValueError):
            value = 0.0
        probabilities[intent] = max(0.0, value)
    total = sum(probabilities.values())
    if total <= 0:
        return {}
    return {
        intent: round(probabilities[intent] / total, 6)
        for intent in ALLOWED_INTENTS
    }


def _probabilities_from_confidence(primary_intent: str, confidence: float) -> dict[str, float]:
    remaining_intents = [
        intent for intent in ALLOWED_INTENTS if intent != primary_intent]
    remainder = max(0.0, 1.0 - confidence)
    per_other = remainder / len(remaining_intents)
    probabilities = {intent: round(per_other, 6) for intent in ALLOWED_INTENTS}
    probabilities[primary_intent] = round(confidence, 6)
    return probabilities


def _infer_urgency(text: str, intents: list[str]) -> str:
    high_terms = ("now", "immediately", "cancel", "angry",
                  "ridiculous", "dead", "not working")
    if any(term in text for term in high_terms) or "cancellation_intent" in intents:
        return "high"
    if {"duplicate_charge", "service_outage", "refund_request"} & set(intents):
        return "medium"
    return "low"


def _infer_emotion(text: str) -> str:
    frustrated_terms = ("angry", "ridiculous", "tired",
                        "frustrated", "cancel", "stop the bot")
    if any(term in text for term in frustrated_terms):
        return "frustrated"
    if "please" in text or "can you" in text:
        return "calm"
    return "neutral"
