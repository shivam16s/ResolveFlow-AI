from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.memory_reader import (
    ABSTENTION_MESSAGE,
    MemorySnippet,
    build_memory_reader_prompt,
    llm_read_with_citation,
)


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def assert_prompt_requires_citations_and_abstention() -> None:
    prompt = build_memory_reader_prompt(
        "What happened with the bill?",
        [
            MemorySnippet(
                memory_id="mem-001",
                text="Customer was charged twice for INV-8821.",
                metadata={"topic": "billing"},
            )
        ],
    )
    required = [
        "Answer the query using ONLY",
        "Every factual claim",
        "Citations must be exact memory IDs",
        "set abstained=true",
        ABSTENTION_MESSAGE,
        "[mem-001] Customer was charged twice for INV-8821.",
    ]
    for phrase in required:
        if phrase not in prompt:
            raise AssertionError(f"prompt missing phrase: {phrase}")


def assert_reads_answer_with_valid_citation() -> None:
    fake = FakeLLM(
        json.dumps(
            {
                "answer": "The customer was charged twice for invoice INV-8821.",
                "citations": ["mem-001"],
                "abstained": False,
                "confidence": 0.91,
            }
        )
    )
    answer = llm_read_with_citation(
        "What was the duplicate billing issue?",
        [{"memory_id": "mem-001", "document": "Customer was charged twice for invoice INV-8821."}],
        fake,
    )
    if answer.to_dict() != {
        "answer": "The customer was charged twice for invoice INV-8821.",
        "citations": ["mem-001"],
        "abstained": False,
        "confidence": 0.91,
    }:
        raise AssertionError(f"unexpected cited answer: {answer.to_dict()}")
    if "Query: What was the duplicate billing issue?" not in fake.prompts[0]:
        raise AssertionError("query was not included in reader prompt")


def assert_abstains_without_memory_or_valid_citation() -> None:
    empty_answer = llm_read_with_citation("What plan does the customer have?", [], FakeLLM("{}"))
    if not empty_answer.abstained or empty_answer.citations:
        raise AssertionError(f"empty memory should abstain: {empty_answer.to_dict()}")

    fake_unknown_citation = FakeLLM(
        json.dumps(
            {
                "answer": "The customer has a premium plan.",
                "citations": ["mem-not-provided"],
                "abstained": False,
                "confidence": 0.8,
            }
        )
    )
    unsafe_answer = llm_read_with_citation(
        "What plan does the customer have?",
        [{"memory_id": "mem-001", "document": "Customer asked about a refund."}],
        fake_unknown_citation,
    )
    if not unsafe_answer.abstained or unsafe_answer.answer != ABSTENTION_MESSAGE:
        raise AssertionError(f"unknown citation should force abstention: {unsafe_answer.to_dict()}")


def assert_respects_llm_abstention() -> None:
    fake = FakeLLM(
        json.dumps(
            {
                "answer": ABSTENTION_MESSAGE,
                "citations": [],
                "abstained": True,
                "confidence": 0.2,
            }
        )
    )
    answer = llm_read_with_citation(
        "Was a technician scheduled?",
        [{"memory_id": "mem-001", "document": "Customer reported an outage."}],
        fake,
    )
    if answer.to_dict() != {
        "answer": ABSTENTION_MESSAGE,
        "citations": [],
        "abstained": True,
        "confidence": 0.2,
    }:
        raise AssertionError(f"LLM abstention was not preserved: {answer.to_dict()}")


def assert_rejects_bad_query_and_bad_json() -> None:
    try:
        llm_read_with_citation("", [], FakeLLM("{}"))
    except ValueError:
        pass
    else:
        raise AssertionError("empty query was accepted")

    try:
        llm_read_with_citation(
            "valid query",
            [{"memory_id": "mem-001", "document": "Some memory."}],
            FakeLLM("not json"),
        )
    except ValueError as exc:
        if "not valid JSON" not in str(exc):
            raise AssertionError(f"wrong bad-json error: {exc}")
    else:
        raise AssertionError("bad JSON was accepted")


def main() -> None:
    assert_prompt_requires_citations_and_abstention()
    assert_reads_answer_with_valid_citation()
    assert_abstains_without_memory_or_valid_citation()
    assert_respects_llm_abstention()
    assert_rejects_bad_query_and_bad_json()
    print("memory reader tests passed")


if __name__ == "__main__":
    main()
