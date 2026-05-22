from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import decompose_to_memory_units  # noqa: E402


def test_decompose_customer_turn_into_atomic_facts() -> None:
    units = decompose_to_memory_units(
        [
            {
                "role": "customer",
                "content": "I was charged twice this month and my internet is still not working. I want to cancel now.",
            }
        ]
    )

    assert [unit.content for unit in units] == [
        "Customer said: I was charged twice this month",
        "Customer said: my internet is still not working",
        "Customer said: I want to cancel now",
    ]
    assert [unit.memory_type for unit in units] == ["episodic", "episodic", "episodic"]
    assert [unit.topic for unit in units] == ["billing", "service", "cancellation"]
    assert all(unit.source_turn_index == 0 for unit in units)


def test_decompose_mixed_transcript_preserves_source_turns() -> None:
    units = decompose_to_memory_units(
        [
            {"role": "customer", "content": "My preferred language is Tamil and my plan is Fiber Plus 200."},
            {"role": "assistant", "content": "I found invoice INV-8821 and payment PAY-1001-A."},
        ]
    )

    assert len(units) == 4
    assert units[0].memory_type == "stable"
    assert units[1].memory_type == "stable"
    assert units[2].source_role == "assistant"
    assert "INV-8821" in units[2].entity_tags
    assert "PAY-1001-A" in units[3].entity_tags


def test_decompose_deduplicates_repeated_facts() -> None:
    units = decompose_to_memory_units(
        [
            "Internet is down.",
            "Internet is down.",
            "Router signal is weak.",
        ]
    )

    assert [unit.content for unit in units] == [
        "Conversation said: Internet is down",
        "Conversation said: Router signal is weak",
    ]
    assert units[0].topic == "service"
    assert units[1].topic == "router"


def main() -> None:
    test_decompose_customer_turn_into_atomic_facts()
    test_decompose_mixed_transcript_preserves_source_turns()
    test_decompose_deduplicates_repeated_facts()
    print("PASS memory decomposition tests")


if __name__ == "__main__":
    main()
