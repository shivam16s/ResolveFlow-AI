from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.openie import build_openie_prompt, extract_openie_triples, triples_to_dicts


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def assert_prompt_is_one_shot_and_strict() -> None:
    prompt = build_openie_prompt("Customer was charged twice for INV-8821.", max_triples=5)

    required_phrases = [
        "Schema:",
        "One-shot example input:",
        "One-shot example output:",
        "Return JSON only",
        "Use only facts explicitly stated",
        "Return at most 5 triples",
        "Customer was charged twice for INV-8821.",
    ]
    for phrase in required_phrases:
        if phrase not in prompt:
            raise AssertionError(f"prompt is missing required phrase: {phrase}")


def assert_extracts_normalized_triples_from_llm_json() -> None:
    fake = FakeLLM(
        json.dumps(
            {
                "entities": [
                    {"name": "CUST-1001", "type": "customer"},
                    {"name": "INV-8821", "type": "invoice"},
                ],
                "triples": [
                    {
                        "subject": " CUST-1001 ",
                        "relation": "was charged twice for",
                        "object": " INV-8821 ",
                        "confidence": 1.4,
                        "evidence": "CUST-1001 was charged twice for INV-8821",
                    },
                    {
                        "subject": "CUST-1001",
                        "relation": "was charged twice for",
                        "object": "INV-8821",
                        "confidence": 0.8,
                        "evidence": "duplicate should be removed",
                    },
                    {
                        "subject": "",
                        "relation": "ignored",
                        "object": "missing subject",
                        "confidence": 0.5,
                    },
                ],
            }
        )
    )

    triples = extract_openie_triples(
        "CUST-1001 was charged twice for INV-8821.",
        fake,
    )

    if len(triples) != 1:
        raise AssertionError(f"expected one deduped triple, got {triples_to_dicts(triples)}")

    triple = triples[0]
    expected = {
        "subject": "CUST-1001",
        "relation": "was_charged_twice_for",
        "object": "INV-8821",
        "confidence": 1.0,
        "evidence": "CUST-1001 was charged twice for INV-8821",
    }
    if triple.to_dict() != expected:
        raise AssertionError(f"unexpected triple: {triple.to_dict()}")

    if "One-shot example input:" not in fake.prompts[0]:
        raise AssertionError("extractor did not send the one-shot prompt to the LLM")


def assert_supports_fenced_json_and_limits_count() -> None:
    fake = FakeLLM(
        """```json
{
  "triples": [
    {"subject": "customer", "relation": "reported", "object": "outage", "confidence": 0.9, "evidence": "reported outage"},
    {"subject": "outage", "relation": "affected", "object": "broadband", "confidence": 0.8, "evidence": "broadband outage"}
  ]
}
```"""
    )

    triples = extract_openie_triples("customer reported broadband outage", fake, max_triples=1)
    if len(triples) != 1 or triples[0].relation != "reported":
        raise AssertionError(f"fenced JSON or max_triples handling failed: {triples_to_dicts(triples)}")


def assert_rejects_bad_inputs_and_bad_json() -> None:
    for bad_text in ("", "   "):
        try:
            extract_openie_triples(bad_text, FakeLLM("{}"))
        except ValueError:
            pass
        else:
            raise AssertionError("empty text was accepted")

    try:
        extract_openie_triples("valid text", FakeLLM("not json"))
    except ValueError as exc:
        if "not valid JSON" not in str(exc):
            raise AssertionError(f"wrong bad-json error: {exc}")
    else:
        raise AssertionError("bad JSON was accepted")


def main() -> None:
    assert_prompt_is_one_shot_and_strict()
    assert_extracts_normalized_triples_from_llm_json()
    assert_supports_fenced_json_and_limits_count()
    assert_rejects_bad_inputs_and_bad_json()
    print("openie extraction tests passed")


if __name__ == "__main__":
    main()
