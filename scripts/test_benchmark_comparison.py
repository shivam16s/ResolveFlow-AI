from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.evaluation import (  # noqa: E402
    DEFAULT_TAU_BENCH_BASELINES,
    generate_benchmark_comparison,
    run_evaluation,
)


def assert_generates_tau_bench_comparison_with_ragas_rows() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-benchmark-")) / "resolveflow.db"
    evaluation = run_evaluation(k=1, db_path=db_path)
    report = generate_benchmark_comparison(evaluation)

    if report["pass_k"] != 1 or report["scenario_count"] != 13 or report["total_runs"] != 13:
        raise AssertionError(f"wrong benchmark report identity: {report}")
    if len(report["baselines"]) != len(DEFAULT_TAU_BENCH_BASELINES):
        raise AssertionError(f"default baselines missing: {report['baselines']}")
    baseline_ids = {baseline["baseline_id"] for baseline in report["baselines"]}
    if "tau_bench_retail_sota_claude_sonnet_4_5" not in baseline_ids:
        raise AssertionError(f"retail SOTA baseline missing: {baseline_ids}")
    if "tau_bench_airline_sota_claude_sonnet_4_5" not in baseline_ids:
        raise AssertionError(f"airline SOTA baseline missing: {baseline_ids}")

    tau_rows = [row for row in report["rows"] if row["category"] == "tau_bench_comparison"]
    if len(tau_rows) != len(DEFAULT_TAU_BENCH_BASELINES):
        raise AssertionError(f"expected one comparison row per tau baseline: {tau_rows}")
    for row in tau_rows:
        if row["baseline_score"] is None or row["delta_vs_baseline"] is None:
            raise AssertionError(f"tau comparison row should include delta: {row}")
        if not row["source_url"]:
            raise AssertionError(f"tau comparison row should include source URL: {row}")

    ragas_metrics = {row["resolveflow_metric"] for row in report["ragas_rows"]}
    if ragas_metrics != {"average_faithfulness", "average_context_precision"}:
        raise AssertionError(f"RAGAS rows missing: {report['ragas_rows']}")
    for row in report["ragas_rows"]:
        if row["category"] != "ragas_retrieval_quality":
            raise AssertionError(f"RAGAS row category wrong: {row}")
        if row["baseline_score"] is not None:
            raise AssertionError(f"RAGAS rows should not fake tau baselines: {row}")


def assert_all_default_baselines_are_source_linked_and_bounded() -> None:
    for baseline in DEFAULT_TAU_BENCH_BASELINES:
        payload = baseline.to_dict()
        if not payload["source_url"].startswith("https://"):
            raise AssertionError(f"baseline must include source URL: {payload}")
        if not 0 <= payload["score"] <= 1:
            raise AssertionError(f"baseline score out of range: {payload}")
        if payload["retrieved_date"] != "2026-05-24":
            raise AssertionError(f"baseline snapshot date should be explicit: {payload}")


def assert_benchmark_comparison_validates_bad_inputs() -> None:
    bad_baselines = (
        [],
        [{"baseline_id": "bad", "benchmark": "x", "domain": "x", "model": "x", "metric": "x", "score": 1.5, "source_name": "x", "source_url": "x", "retrieved_date": "2026-05-24", "verified": False, "notes": "x"}],
        ["not-a-baseline"],
    )
    for baselines in bad_baselines:
        try:
            generate_benchmark_comparison(
                {"pass_k": 1, "scenario_count": 0, "total_runs": 0, "results": []},
                metric_report={"metrics": {"resolution_success": {"value": 1}, "policy_compliance": {"value": 1}, "wrong_tools_avoided": {"value": 1}}},
                ragas_report={"average_faithfulness": 1, "average_context_precision": 1},
                baselines=baselines,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad baselines accepted: {baselines}")


def main() -> None:
    assert_generates_tau_bench_comparison_with_ragas_rows()
    assert_all_default_baselines_are_source_linked_and_bounded()
    assert_benchmark_comparison_validates_bad_inputs()
    print("benchmark comparison tests passed")


if __name__ == "__main__":
    main()
