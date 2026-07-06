from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.evaluation import evaluate_policy_retrievals_with_ragas, run_evaluation  # noqa: E402
from backend.evaluation.ragas import _answer_terms, _relevant_context_ranks, _score_retrieval  # noqa: E402


def assert_scores_all_policy_retrievals_from_real_evaluation() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-ragas-")) / "resolveflow.db"
    evaluation = run_evaluation(k=1, db_path=db_path)
    retrieval_count = sum(
        len(result["artifacts"].get("policy_retrievals", []))
        for result in evaluation["results"]
    )
    if retrieval_count <= 0:
        raise AssertionError("evaluation runner did not preserve policy retrievals")

    report = evaluate_policy_retrievals_with_ragas(evaluation)
    if report["retrieval_count"] != retrieval_count:
        raise AssertionError(f"RAGAS report should score every retrieval: {report}")
    if not 0 <= report["average_context_recall"] <= 1:
        raise AssertionError(f"context_recall out of range: {report}")
    if not 0 <= report["average_context_precision"] <= 1:
        raise AssertionError(f"context precision out of range: {report}")
    for score in report["scores"]:
        if score["context_count"] <= 0:
            raise AssertionError(f"retrieval should include contexts: {score}")
        if not 0 <= score["context_recall"] <= 1:
            raise AssertionError(f"score context_recall out of range: {score}")
        if not 0 <= score["context_precision"] <= 1:
            raise AssertionError(f"score context precision out of range: {score}")


def assert_duplicate_charge_policy_has_grounded_evidence() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-ragas-dup-")) / "resolveflow.db"
    evaluation = run_evaluation(k=1, db_path=db_path)
    report = evaluate_policy_retrievals_with_ragas(evaluation)
    duplicate_scores = [
        score
        for score in report["scores"]
        if score["scenario_id"] == "case_02_duplicate_charge"
        and score["policy_id"] == "duplicate_charge_policy"
    ]
    if len(duplicate_scores) != 1:
        raise AssertionError(f"expected one duplicate policy score: {duplicate_scores}")
    score = duplicate_scores[0]
    if score["context_recall"] <= 0:
        raise AssertionError(f"duplicate charge context_recall should find supported terms: {score}")
    if score["context_precision"] <= 0:
        raise AssertionError(f"duplicate charge context precision should be positive: {score}")
    if "duplicate" not in score["supported_terms"]:
        raise AssertionError(f"duplicate evidence terms should be supported: {score}")


def assert_context_precision_requires_more_than_one_shared_query_token() -> None:
    weak_ranks = _relevant_context_ranks(
        [
            "Refund exceptions are routed to a manager queue.",
            "Router diagnostics require signal checks before dispatch.",
        ],
        query="refund status",
        answer_terms=["duplicate", "charge", "policy"],
    )
    if weak_ranks:
        raise AssertionError(f"single shared query token should not mark context relevant: {weak_ranks}")

    strong_ranks = _relevant_context_ranks(
        ["Duplicate charge policy requires invoice and payment evidence."],
        query="duplicate charge refund",
        answer_terms=["duplicate", "charge", "policy"],
    )
    if strong_ranks != [1]:
        raise AssertionError(f"grounded policy context should still be relevant: {strong_ranks}")


def assert_context_recall_is_not_vacuously_perfect_without_answer_terms() -> None:
    scenario = SimpleNamespace(
        scenario_id="synthetic",
        customer_messages=["hello"],
        goal_state={"required_policies": [], "success_criteria": []},
    )
    score = _score_retrieval(
        {"pass_index": 1},
        scenario,
        {"policy_id": "", "query": "hello", "evidence_strips": [{"text": "generic text"}]},
    )
    if score.context_recall != 0.0:
        raise AssertionError(f"empty answer terms should not produce perfect recall: {score.to_dict()}")


def assert_answer_terms_are_policy_specific() -> None:
    scenario = SimpleNamespace(
        goal_state={
            "required_policies": ["duplicate_charge_policy", "refund_policy"],
            "success_criteria": [
                "Finds duplicate payments and duplicate invoice evidence.",
                "Refund exception requests must be escalated.",
            ],
        }
    )
    duplicate_terms = set(_answer_terms(scenario, "duplicate_charge_policy"))
    refund_terms = set(_answer_terms(scenario, "refund_policy"))
    if "duplicate" not in duplicate_terms:
        raise AssertionError(f"duplicate policy should include duplicate criteria terms: {duplicate_terms}")
    if "duplicate" in refund_terms:
        raise AssertionError(f"refund policy should not inherit duplicate criteria terms: {refund_terms}")
    if "refund" not in refund_terms:
        raise AssertionError(f"refund policy should keep refund-specific terms: {refund_terms}")


def assert_ragas_layer_validates_bad_payloads() -> None:
    bad_payloads = (
        [],
        {"results": "not-a-list"},
        {"results": [{"scenario_id": "missing-case", "artifacts": {}}]},
        {"results": [{"scenario_id": "case_01_simple_bill_question", "artifacts": {"policy_retrievals": "bad"}}]},
    )
    for payload in bad_payloads:
        try:
            evaluate_policy_retrievals_with_ragas(payload)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad RAGAS payload accepted: {payload}")


def main() -> None:
    assert_scores_all_policy_retrievals_from_real_evaluation()
    assert_duplicate_charge_policy_has_grounded_evidence()
    assert_context_precision_requires_more_than_one_shared_query_token()
    assert_context_recall_is_not_vacuously_perfect_without_answer_terms()
    assert_answer_terms_are_policy_specific()
    assert_ragas_layer_validates_bad_payloads()
    print("ragas evaluation tests passed")


if __name__ == "__main__":
    main()
