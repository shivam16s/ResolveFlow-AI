from __future__ import annotations

import sys
from pathlib import Path
from datetime import date


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import fact_augmented_expansion, time_aware_expansion  # noqa: E402


def terms(expanded: str) -> list[str]:
    return expanded.split(" OR ")


def test_fact_augmented_expansion_for_duplicate_billing() -> None:
    expanded = fact_augmented_expansion("billing issue")
    expanded_terms = terms(expanded)

    assert expanded_terms[0] == "billing issue"
    assert "duplicate charge" in expanded_terms
    assert "invoice" in expanded_terms
    assert "payment" in expanded_terms


def test_fact_augmented_expansion_for_outage_and_router_query() -> None:
    expanded = fact_augmented_expansion("internet is down and router signal is weak")
    expanded_terms = terms(expanded)

    assert "service outage" in expanded_terms
    assert "internet not working" in expanded_terms
    assert "router diagnostic" in expanded_terms
    assert "weak signal" in expanded_terms


def test_fact_augmented_expansion_for_cancellation_refund_query() -> None:
    expanded = fact_augmented_expansion("I want to cancel and get a refund")
    expanded_terms = terms(expanded)

    assert "cancellation intent" in expanded_terms
    assert "churn risk" in expanded_terms
    assert "refund request" in expanded_terms
    assert "account credit" in expanded_terms


def test_fact_augmented_expansion_keeps_ids_and_limits_terms() -> None:
    expanded = fact_augmented_expansion("check invoice INV-8821 payment PAY-1001-A", max_terms=8)
    expanded_terms = terms(expanded)

    assert len(expanded_terms) == 8
    assert expanded_terms[0] == "check invoice INV-8821 payment PAY-1001-A"
    assert len(expanded_terms) == len({term.lower() for term in expanded_terms})


def test_fact_augmented_expansion_rejects_empty_query() -> None:
    try:
        fact_augmented_expansion("   ")
    except ValueError as exc:
        assert "query must not be empty" in str(exc)
    else:
        raise AssertionError("empty query was accepted")


def test_time_aware_expansion_for_yesterday() -> None:
    expanded = time_aware_expansion("outage yesterday", reference_date=date(2026, 5, 21))

    assert expanded == (
        "outage yesterday OR "
        "yesterday:created_at>=2026-05-20 OR "
        "yesterday:created_at<=2026-05-20 OR "
        "yesterday:date_range=2026-05-20..2026-05-20"
    )


def test_time_aware_expansion_for_last_week() -> None:
    expanded = time_aware_expansion("last week's complaint", reference_date=date(2026, 5, 21))
    expanded_terms = terms(expanded)

    assert expanded_terms[0] == "last week's complaint"
    assert "last_week:created_at>=2026-05-11" in expanded_terms
    assert "last_week:created_at<=2026-05-17" in expanded_terms
    assert "last_week:date_range=2026-05-11..2026-05-17" in expanded_terms


def test_time_aware_expansion_for_recent_and_this_month() -> None:
    expanded = time_aware_expansion("recent credits this month", reference_date=date(2026, 5, 21))
    expanded_terms = terms(expanded)

    assert "recent:date_range=2026-05-07..2026-05-21" in expanded_terms
    assert "this_month:date_range=2026-05-01..2026-05-21" in expanded_terms


def test_time_aware_expansion_without_temporal_phrase_returns_query() -> None:
    assert time_aware_expansion("duplicate billing", reference_date=date(2026, 5, 21)) == "duplicate billing"


def main() -> None:
    test_fact_augmented_expansion_for_duplicate_billing()
    test_fact_augmented_expansion_for_outage_and_router_query()
    test_fact_augmented_expansion_for_cancellation_refund_query()
    test_fact_augmented_expansion_keeps_ids_and_limits_terms()
    test_fact_augmented_expansion_rejects_empty_query()
    test_time_aware_expansion_for_yesterday()
    test_time_aware_expansion_for_last_week()
    test_time_aware_expansion_for_recent_and_this_month()
    test_time_aware_expansion_without_temporal_phrase_returns_query()
    print("PASS memory fact-augmented expansion tests")


if __name__ == "__main__":
    main()
