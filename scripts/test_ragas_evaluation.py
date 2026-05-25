from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.evaluation import evaluate_policy_retrievals_with_ragas, run_evaluation  # noqa: E402


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
    if not 0 <= report["average_faithfulness"] <= 1:
        raise AssertionError(f"faithfulness out of range: {report}")
    if not 0 <= report["average_context_precision"] <= 1:
        raise AssertionError(f"context precision out of range: {report}")
    for score in report["scores"]:
        if score["context_count"] <= 0:
            raise AssertionError(f"retrieval should include contexts: {score}")
        if not 0 <= score["faithfulness"] <= 1:
            raise AssertionError(f"score faithfulness out of range: {score}")
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
    if score["faithfulness"] <= 0:
        raise AssertionError(f"duplicate charge faithfulness should find supported terms: {score}")
    if score["context_precision"] <= 0:
        raise AssertionError(f"duplicate charge context precision should be positive: {score}")
    if "duplicate" not in score["supported_terms"]:
        raise AssertionError(f"duplicate evidence terms should be supported: {score}")


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
    assert_ragas_layer_validates_bad_payloads()
    print("ragas evaluation tests passed")


if __name__ == "__main__":
    main()
