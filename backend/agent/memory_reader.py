from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Mapping

from .llm_client import GeminiGenerateClient


ABSTENTION_MESSAGE = "I do not have enough memory evidence to answer that."


@dataclass(frozen=True)
class MemorySnippet:
    memory_id: str
    text: str
    metadata: dict

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CitedMemoryAnswer:
    answer: str
    citations: list[str]
    abstained: bool
    confidence: float

    def to_dict(self) -> dict:
        return asdict(self)


def llm_read_with_citation(
    query: str,
    memories: Iterable[MemorySnippet | Mapping[str, object]],
    llm_client: Callable[[str], str] | None = None,
) -> CitedMemoryAnswer:
    normalized_query = re.sub(r"\s+", " ", query.strip())
    if not normalized_query:
        raise ValueError("query must not be empty")

    snippets = _normalize_snippets(memories)
    if not snippets:
        return _abstain()

    client = llm_client or GeminiGenerateClient()
    raw_output = client(build_memory_reader_prompt(normalized_query, snippets))
    payload = _extract_json_object(raw_output)
    return _answer_from_payload(payload, allowed_memory_ids={snippet.memory_id for snippet in snippets})


def build_memory_reader_prompt(query: str, snippets: list[MemorySnippet]) -> str:
    context = "\n".join(
        f"[{snippet.memory_id}] {snippet.text}"
        for snippet in snippets
    )
    schema = {
        "answer": "short answer grounded only in cited memory snippets",
        "citations": ["memory_id"],
        "abstained": False,
        "confidence": 0.0,
    }
    return (
        "You are the LongMemEval Stage 3 reader for a telecom support memory system.\n"
        "Answer the query using ONLY the memory snippets below.\n"
        "Every factual claim in the answer must be supported by at least one citation.\n"
        "Citations must be exact memory IDs from the provided snippets.\n"
        f"If the snippets do not answer the query, set abstained=true and answer exactly: {ABSTENTION_MESSAGE}\n"
        "Return JSON only, with no markdown, commentary, or extra keys.\n\n"
        "Schema:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        "Memory snippets:\n"
        f"{context}\n\n"
        f"Query: {query}"
    )


def _normalize_snippets(memories: Iterable[MemorySnippet | Mapping[str, object]]) -> list[MemorySnippet]:
    snippets = []
    seen = set()
    for item in memories:
        if isinstance(item, MemorySnippet):
            memory_id = item.memory_id
            text = item.text
            metadata = item.metadata
        else:
            memory_id = str(item.get("memory_id")
                            or item.get("id") or "").strip()
            text = str(item.get("text") or item.get("document")
                       or item.get("content") or "").strip()
            raw_metadata = item.get("metadata", {})
            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}

        if not memory_id or not text or memory_id in seen:
            continue
        seen.add(memory_id)
        snippets.append(
            MemorySnippet(
                memory_id=memory_id,
                text=re.sub(r"\s+", " ", text),
                metadata=metadata,
            )
        )
    return snippets


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
            f"memory reader LLM output was not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("memory reader LLM output must be a JSON object")
    return payload


def _answer_from_payload(payload: dict, *, allowed_memory_ids: set[str]) -> CitedMemoryAnswer:
    abstained = bool(payload.get("abstained", False))
    answer = re.sub(r"\s+", " ", str(payload.get("answer", "")).strip())
    citations = _dedupe(
        str(citation).strip()
        for citation in payload.get("citations", [])
        if str(citation).strip()
    )
    valid_citations = [
        citation for citation in citations if citation in allowed_memory_ids]

    if abstained:
        return _abstain(confidence=_clean_confidence(payload.get("confidence", 0.0)))

    if not answer or len(valid_citations) != len(citations) or not valid_citations:
        return _abstain()

    return CitedMemoryAnswer(
        answer=answer,
        citations=valid_citations,
        abstained=False,
        confidence=_clean_confidence(payload.get("confidence", 0.7)),
    )


def _abstain(confidence: float = 0.0) -> CitedMemoryAnswer:
    return CitedMemoryAnswer(
        answer=ABSTENTION_MESSAGE,
        citations=[],
        abstained=True,
        confidence=_clean_confidence(confidence),
    )


def _clean_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return round(max(0.0, min(1.0, confidence)), 2)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
