from __future__ import annotations

import re
from datetime import date, timedelta
from dataclasses import asdict, dataclass, field
from typing import Iterable, Mapping


TranscriptInput = str | Iterable[str] | Iterable[Mapping[str, object]]


@dataclass(frozen=True)
class MemoryUnit:
    content: str
    memory_type: str
    topic: str
    source_role: str
    source_turn_index: int
    confidence: float
    entity_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


TOPIC_KEYWORDS = {
    "billing": ("bill", "billing", "charge", "charged", "payment", "invoice", "refund", "credit"),
    "service": ("internet", "broadband", "outage", "connection", "wifi", "down", "not working"),
    "router": ("router", "modem", "signal", "blinking", "red light"),
    "plan": ("plan", "upgrade", "downgrade", "bundle", "speed"),
    "cancellation": ("cancel", "cancellation", "disconnect", "close my account"),
    "handoff": ("human", "agent", "specialist", "supervisor"),
}

STABLE_KEYWORDS = (
    "preferred language",
    "my language",
    "i prefer",
    "my plan",
    "current plan",
    "my address",
    "my location",
)

EPISODIC_KEYWORDS = (
    "charged",
    "paid",
    "reported",
    "ticket",
    "outage",
    "not working",
    "down",
    "refund",
    "credit",
    "cancel",
    "scheduled",
    "diagnostic",
)

FACT_EXPANSION_TERMS = {
    "billing": (
        "billing issue",
        "bill dispute",
        "invoice",
        "payment",
        "charge",
        "duplicate charge",
        "double charged",
    ),
    "duplicate_charge": (
        "duplicate charge",
        "double charged",
        "charged twice",
        "two payments",
        "duplicate payment",
        "invoice dispute",
    ),
    "service": (
        "service outage",
        "internet not working",
        "connection down",
        "broadband outage",
        "wifi down",
        "service disruption",
    ),
    "router": (
        "router diagnostic",
        "weak signal",
        "modem issue",
        "router offline",
        "blinking red light",
    ),
    "plan": (
        "plan change",
        "upgrade",
        "downgrade",
        "current plan",
        "monthly price",
        "speed",
    ),
    "cancellation": (
        "cancellation intent",
        "cancel service",
        "disconnect",
        "retention",
        "churn risk",
    ),
    "refund": (
        "refund request",
        "money back",
        "account credit",
        "service credit",
        "billing adjustment",
    ),
    "technician": (
        "technician visit",
        "field visit",
        "appointment",
        "dispatch",
        "engineer visit",
    ),
    "handoff": (
        "human handoff",
        "human agent",
        "specialist",
        "supervisor approval",
        "escalation",
    ),
}


def decompose_to_memory_units(transcript: TranscriptInput) -> list[MemoryUnit]:
    turns = _normalize_transcript(transcript)
    units: list[MemoryUnit] = []
    seen = set()

    for turn_index, role, text in turns:
        for fragment in _split_into_atomic_fragments(text):
            content = _format_content(role, fragment)
            dedupe_key = (role, content.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            topic = _infer_topic(fragment)
            memory_type = _infer_memory_type(fragment, role)
            units.append(
                MemoryUnit(
                    content=content,
                    memory_type=memory_type,
                    topic=topic,
                    source_role=role,
                    source_turn_index=turn_index,
                    confidence=_infer_confidence(fragment, memory_type),
                    entity_tags=_extract_entity_tags(fragment, topic),
                )
            )

    return units


def fact_augmented_expansion(query: str, max_terms: int = 18) -> str:
    normalized = re.sub(r"\s+", " ", query.strip())
    if not normalized:
        raise ValueError("query must not be empty")

    terms = [normalized]
    lower_query = normalized.lower()
    matched_groups = _matched_expansion_groups(lower_query)

    for group in matched_groups:
        terms.extend(FACT_EXPANSION_TERMS[group])

    ids = re.findall(
        r"\b(?:CUST|INV|PAY|OUT|TICK|CASE)-[A-Z0-9-]+\b", normalized, flags=re.IGNORECASE)
    terms.extend(identifier.upper() for identifier in ids)

    return " OR ".join(_dedupe_case_insensitive(terms)[:max_terms])


def time_aware_expansion(query: str, reference_date: date | None = None) -> str:
    normalized = re.sub(r"\s+", " ", query.strip())
    if not normalized:
        raise ValueError("query must not be empty")

    today = reference_date or date.today()
    lower_query = normalized.lower()
    anchors = _temporal_anchors(lower_query, today)

    if not anchors:
        return normalized

    terms = [normalized]
    for label, start, end in anchors:
        terms.append(f"{label}:created_at>={start.isoformat()}")
        terms.append(f"{label}:created_at<={end.isoformat()}")
        terms.append(
            f"{label}:date_range={start.isoformat()}..{end.isoformat()}")

    return " OR ".join(_dedupe_case_insensitive(terms))


def _normalize_transcript(transcript: TranscriptInput) -> list[tuple[int, str, str]]:
    if isinstance(transcript, str):
        return [(0, "unknown", transcript)]

    turns = []
    for index, item in enumerate(transcript):
        if isinstance(item, str):
            turns.append((index, "unknown", item))
            continue

        role = str(item.get("role", "unknown")).strip().lower() or "unknown"
        text = str(item.get("content", "")).strip()
        if text:
            turns.append((index, role, text))

    return turns


def _temporal_anchors(lower_query: str, today: date) -> list[tuple[str, date, date]]:
    anchors: list[tuple[str, date, date]] = []

    if re.search(r"\btoday\b", lower_query):
        anchors.append(("today", today, today))
    if re.search(r"\byesterday\b", lower_query):
        yesterday = today - timedelta(days=1)
        anchors.append(("yesterday", yesterday, yesterday))
    if re.search(r"\bthis week\b", lower_query):
        start = today - timedelta(days=today.weekday())
        anchors.append(("this_week", start, today))
    if re.search(r"\blast week\b", lower_query):
        this_week_start = today - timedelta(days=today.weekday())
        start = this_week_start - timedelta(days=7)
        end = this_week_start - timedelta(days=1)
        anchors.append(("last_week", start, end))
    if re.search(r"\bthis month\b", lower_query):
        start = today.replace(day=1)
        anchors.append(("this_month", start, today))
    if re.search(r"\blast month\b", lower_query):
        this_month_start = today.replace(day=1)
        end = this_month_start - timedelta(days=1)
        start = end.replace(day=1)
        anchors.append(("last_month", start, end))
    if re.search(r"\brecent(?:ly)?\b|\blast few days\b", lower_query):
        anchors.append(("recent", today - timedelta(days=14), today))
    if re.search(r"\bprior\b|\bprevious\b|\bpast\b", lower_query):
        anchors.append(("past", today - timedelta(days=90), today))

    return anchors


def _matched_expansion_groups(lower_query: str) -> list[str]:
    groups = []
    keyword_map = {
        "duplicate_charge": ("duplicate", "twice", "double charged", "two payments"),
        "billing": ("bill", "billing", "invoice", "payment", "paid", "charge", "charged"),
        "service": ("internet", "broadband", "outage", "down", "not working", "connection", "wifi"),
        "router": ("router", "modem", "signal", "red light"),
        "plan": ("plan", "upgrade", "downgrade", "bundle", "speed", "cheapest"),
        "cancellation": ("cancel", "cancellation", "disconnect", "close account", "churn"),
        "refund": ("refund", "money back", "credit", "compensate", "reimburse"),
        "technician": ("technician", "engineer", "appointment", "dispatch", "visit"),
        "handoff": ("human", "agent", "specialist", "supervisor", "escalate"),
    }
    for group, keywords in keyword_map.items():
        if any(keyword in lower_query for keyword in keywords):
            groups.append(group)
    return groups


def _split_into_atomic_fragments(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return []

    sentence_parts = re.split(r"(?<=[.!?])\s+|[;\n]+", normalized)
    fragments: list[str] = []
    for sentence in sentence_parts:
        sentence = sentence.strip(" .!?")
        if not sentence:
            continue

        parts = re.split(r"\s+(?:and|also|but)\s+",
                         sentence, flags=re.IGNORECASE)
        fragments.extend(part.strip(" .!?")
                         for part in parts if part.strip(" .!?"))

    return fragments


def _format_content(role: str, fragment: str) -> str:
    subject = {
        "customer": "Customer",
        "user": "Customer",
        "assistant": "Agent",
        "agent": "Agent",
        "system": "System",
    }.get(role, "Conversation")
    return f"{subject} said: {fragment}"


def _infer_memory_type(fragment: str, role: str) -> str:
    text = fragment.lower()
    if any(keyword in text for keyword in STABLE_KEYWORDS):
        return "stable"
    if any(keyword in text for keyword in EPISODIC_KEYWORDS):
        return "episodic"
    if role in {"assistant", "agent", "system"}:
        return "session"
    return "session"


def _infer_topic(fragment: str) -> str:
    text = fragment.lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return topic
    return "general"


def _infer_confidence(fragment: str, memory_type: str) -> float:
    word_count = len(fragment.split())
    base = 0.72 if memory_type == "session" else 0.82
    if word_count >= 4:
        base += 0.08
    if any(char.isdigit() for char in fragment):
        base += 0.04
    return round(min(base, 0.96), 2)


def _extract_entity_tags(fragment: str, topic: str) -> list[str]:
    tags = [topic]
    lower = fragment.lower()

    tag_keywords = {
        "invoice": ("invoice", "inv-"),
        "payment": ("payment", "paid", "charged"),
        "outage": ("outage", "down", "not working"),
        "router": ("router", "modem", "signal"),
        "credit": ("credit",),
        "refund": ("refund",),
        "cancellation": ("cancel", "cancellation"),
        "technician": ("technician", "engineer", "visit"),
    }
    for tag, keywords in tag_keywords.items():
        if any(keyword in lower for keyword in keywords):
            tags.append(tag)

    ids = re.findall(
        r"\b(?:CUST|INV|PAY|OUT|TICK|CASE)-[A-Z0-9-]+\b", fragment, flags=re.IGNORECASE)
    tags.extend(identifier.upper() for identifier in ids)
    return _dedupe(tags)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _dedupe_case_insensitive(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
