from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.evaluation import (  # noqa: E402
    HUMAN_REVIEW_RUBRIC,
    build_human_review_packets,
    build_three_layer_evaluation,
    run_evaluation,
)
from backend.evaluation.reporting import METRIC_NAMES  # noqa: E402


def assert_builds_three_layer_methodology_from_real_run() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-three-layer-")) / "resolveflow.db"
    evaluation = run_evaluation(k=1, db_path=db_path)
    methodology = build_three_layer_evaluation(evaluation)

    if [layer["name"] for layer in methodology["layers"]] != ["deterministic", "ragas", "human_review"]:
        raise AssertionError(f"wrong layer order: {methodology['layers']}")
    if tuple(methodology["deterministic_report"]["metrics"]) != METRIC_NAMES:
        raise AssertionError("deterministic layer should expose the 9 metric report")
    if methodology["ragas_report"]["retrieval_count"] <= 0:
        raise AssertionError("RAGAS layer should score real policy retrievals")
    if methodology["human_review"]["packet_count"] != evaluation["total_runs"]:
        raise AssertionError("human review should create one packet per evaluated run")
    if methodology["human_review"]["rubric"] != HUMAN_REVIEW_RUBRIC:
        raise AssertionError("human review rubric should be stable")


def assert_human_review_packets_capture_expected_and_observed_state() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-human-review-")) / "resolveflow.db"
    evaluation = run_evaluation(k=1, db_path=db_path)
    packets = build_human_review_packets(evaluation)

    if len(packets) != 30:
        raise AssertionError(f"expected 30 packets for k=1, got {len(packets)}")
    duplicate = next(packet for packet in packets if packet.scenario_id == "case_02_duplicate_charge")
    payload = duplicate.to_dict()
    if payload["review_id"] != "review-p01-case_02_duplicate_charge":
        raise AssertionError(f"review id should be stable: {payload}")
    if "duplicate_charge" not in payload["expected_goal_state"]["expected_intents"]:
        raise AssertionError(f"expected state missing duplicate intent: {payload}")
    for field_name in ("passed", "score", "tools_called", "policies_retrieved", "failures"):
        if field_name not in payload["observed_summary"]:
            raise AssertionError(f"observed summary missing {field_name}: {payload}")
    if len(payload["rubric"]) != 5:
        raise AssertionError(f"rubric should have 5 criteria: {payload['rubric']}")


def assert_three_layer_methodology_validates_bad_payloads() -> None:
    bad_payloads = (
        [],
        {"results": "not-a-list"},
        {"results": [{"scenario_id": "missing-case"}]},
    )
    for payload in bad_payloads:
        try:
            build_three_layer_evaluation(payload)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad methodology payload accepted: {payload}")


def main() -> None:
    assert_builds_three_layer_methodology_from_real_run()
    assert_human_review_packets_capture_expected_and_observed_state()
    assert_three_layer_methodology_validates_bad_payloads()
    print("three-layer evaluation tests passed")


if __name__ == "__main__":
    main()
