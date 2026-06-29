from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Callable, Iterable

from .llm_client import GeminiGenerateClient


@dataclass(frozen=True)
class OpenIETriple:
    subject: str
    relation: str
    object: str
    confidence: float
    evidence: str

    def to_dict(self) -> dict:
        return asdict(self)


def extract_openie_triples(
    text: str,
    llm_client: Callable[[str], str] | None = None,
    *,
    max_triples: int = 12,
) -> list[OpenIETriple]:
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        raise ValueError("text must not be empty")
    if max_triples < 1:
        raise ValueError("max_triples must be at least 1")

    client = llm_client or GeminiGenerateClient()
    raw_output = client(build_openie_prompt(
        normalized, max_triples=max_triples))
    payload = _extract_json_object(raw_output)
    return _triples_from_payload(payload, max_triples=max_triples)


def build_openie_prompt(text: str, *, max_triples: int = 12) -> str:
    schema = {
        "entities": [{"name": "entity name from text", "type": "customer|invoice|payment|outage|plan|location|date|amount|service|other"}],
        "triples": [
            {
                "subject": "short noun phrase from text",
                "relation": "lower_snake_case_relation",
                "object": "short noun phrase or value from text",
                "confidence": 0.0,
                "evidence": "minimal exact phrase supporting the triple",
            }
        ],
    }
    example_output = {
        "entities": [
            {"name": "customer", "type": "customer"},
            {"name": "outage", "type": "outage"},
            {"name": "May 14", "type": "date"},
            {"name": "Chennai Zone 04", "type": "location"},
        ],
        "triples": [
            {
                "subject": "customer",
                "relation": "reported_outage",
                "object": "May 14",
                "confidence": 0.93,
                "evidence": "customer reported outage on May 14",
            },
            {
                "subject": "outage",
                "relation": "occurred_in",
                "object": "Chennai Zone 04",
                "confidence": 0.91,
                "evidence": "outage in Chennai Zone 04",
            },
        ],
    }
    return (
        "You extract OpenIE triples for a telecom customer-support memory graph.\n"
        "Use only facts explicitly stated in the input. Do not infer policy outcomes, eligibility, refunds, or causes.\n"
        "Normalize relation names to lower_snake_case verbs or verb phrases.\n"
        "Keep subjects and objects as concise noun phrases that can become graph nodes.\n"
        f"Return at most {max_triples} triples.\n"
        "Return JSON only, with no markdown or commentary.\n\n"
        "Schema:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        "One-shot example input:\n"
        "customer reported outage on May 14 in Chennai Zone 04\n"
        "One-shot example output:\n"
        f"{json.dumps(example_output, indent=2)}\n\n"
        "Input:\n"
        f"{text}"
    )


def _extract_json_object(raw_output: str) -> dict:
    cleaned = raw_output.strip()
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"OpenIE LLM output was not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("OpenIE LLM output must be a JSON object")
    return payload


def _triples_from_payload(payload: dict, *, max_triples: int) -> list[OpenIETriple]:
    raw_triples = payload.get("triples", [])
    if not isinstance(raw_triples, list):
        raise ValueError("OpenIE payload must contain a triples list")

    triples: list[OpenIETriple] = []
    seen = set()
    for raw_triple in raw_triples:
        if not isinstance(raw_triple, dict):
            continue

        subject = _clean_node(raw_triple.get("subject", ""))
        relation = _clean_relation(raw_triple.get("relation", ""))
        object_ = _clean_node(raw_triple.get("object", ""))
        if not subject or not relation or not object_:
            continue

        key = (subject.lower(), relation, object_.lower())
        if key in seen:
            continue
        seen.add(key)

        triples.append(
            OpenIETriple(
                subject=subject,
                relation=relation,
                object=object_,
                confidence=_clean_confidence(
                    raw_triple.get("confidence", 0.7)),
                evidence=_clean_evidence(raw_triple.get("evidence", "")),
            )
        )
        if len(triples) >= max_triples:
            break

    return triples


def _clean_node(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value).strip())
    return text.strip(" .,:;()[]{}")


def _clean_relation(value: object) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _clean_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.7
    return round(max(0.0, min(1.0, confidence)), 2)


def _clean_evidence(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip())[:240]


def triples_to_dicts(triples: Iterable[OpenIETriple]) -> list[dict]:
    return [triple.to_dict() for triple in triples]
