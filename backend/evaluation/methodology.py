from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .ragas import evaluate_policy_retrievals_with_ragas
from .reporting import generate_metric_report
from .runner import run_evaluation
from .scenarios import DEFAULT_EVALUATION_SCENARIOS_PATH, EvaluationScenario, load_evaluation_scenarios


HUMAN_REVIEW_RUBRIC = [
    {
        "criterion": "resolution_quality",
        "scale": "1-5",
        "question": "Did the agent resolve or safely route the customer's stated issue?",
    },
    {
        "criterion": "policy_safety",
        "scale": "1-5",
        "question": "Did the agent avoid policy-unsafe promises, refunds, credits, or scheduling?",
    },
    {
        "criterion": "evidence_use",
        "scale": "1-5",
        "question": "Did the agent ground its action in retrieved policies, tool evidence, and seeded records?",
    },
    {
        "criterion": "customer_experience",
        "scale": "1-5",
        "question": "Was the response empathetic, concise, and helpful under the customer's tone?",
    },
    {
        "criterion": "handoff_quality",
        "scale": "1-5",
        "question": "When escalation was needed, did the handoff preserve context and next steps?",
    },
]


@dataclass(frozen=True)
class HumanReviewPacket:
    review_id: str
    scenario_id: str
    pass_index: int
    customer_id: str
    customer_messages: list[str]
    expected_goal_state: dict[str, Any]
    observed_summary: dict[str, Any]
    rubric: list[dict[str, str]]
    reviewer_instructions: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ThreeLayerEvaluationMethodology:
    layers: list[dict[str, Any]]
    deterministic_report: dict[str, Any]
    ragas_report: dict[str, Any]
    human_review: dict[str, Any]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def build_three_layer_evaluation(
    evaluation_result: dict | None = None,
    *,
    k: int = 5,
    scenarios_path: Path = DEFAULT_EVALUATION_SCENARIOS_PATH,
    db_path: Path | None = None,
) -> dict:
    if evaluation_result is None:
        evaluation_result = run_evaluation(
            k=k, scenarios_path=scenarios_path, db_path=db_path)
    if not isinstance(evaluation_result, dict):
        raise ValueError("evaluation_result must be a dict when provided")

    scenarios = load_evaluation_scenarios(scenarios_path)
    deterministic_report = generate_metric_report(
        evaluation_result, scenarios_path=scenarios_path)
    ragas_report = evaluate_policy_retrievals_with_ragas(
        evaluation_result, scenarios_path=scenarios_path)
    packets = build_human_review_packets(
        evaluation_result, scenarios=scenarios)

    return ThreeLayerEvaluationMethodology(
        layers=[
            {
                "name": "deterministic",
                "source": "run_evaluation + generate_metric_report",
                "purpose": "Check scenario pass/fail, required tools, forbidden tools, policy compliance, escalation correctness, audit coverage, and non-collaborative degradation.",
                "output_key": "deterministic_report",
            },
            {
                "name": "ragas",
                "source": "evaluate_policy_retrievals_with_ragas",
                "purpose": "Score faithfulness and context precision on every policy retrieval evidence set.",
                "output_key": "ragas_report",
            },
            {
                "name": "human_review",
                "source": "human reviewer rubric packets",
                "purpose": "Capture qualitative judgment for empathy, resolution quality, evidence use, policy safety, and handoff quality.",
                "output_key": "human_review",
            },
        ],
        deterministic_report=deterministic_report,
        ragas_report=ragas_report,
        human_review={
            "rubric": HUMAN_REVIEW_RUBRIC,
            "packet_count": len(packets),
            "packets": [packet.to_dict() for packet in packets],
            "rating_contract": {
                "min_score": 1,
                "max_score": 5,
                "required_fields": ["criterion", "score", "rationale"],
                "decision_values": ["approve", "needs_fix", "unsafe"],
            },
        },
    ).to_dict()


def build_human_review_packets(
    evaluation_result: dict,
    *,
    scenarios: list[EvaluationScenario] | None = None,
) -> list[HumanReviewPacket]:
    if not isinstance(evaluation_result, dict):
        raise ValueError("evaluation_result must be a dict")
    scenarios = scenarios or load_evaluation_scenarios()
    scenario_by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    packets = []
    for result in _result_items(evaluation_result):
        scenario_id = str(result.get("scenario_id", "")).strip()
        scenario = scenario_by_id.get(scenario_id)
        if scenario is None:
            raise ValueError(
                f"unknown scenario_id in evaluation result: {scenario_id!r}")
        pass_index = int(result.get("pass_index", 0))
        packets.append(
            HumanReviewPacket(
                review_id=f"review-p{pass_index:02d}-{scenario_id}",
                scenario_id=scenario_id,
                pass_index=pass_index,
                customer_id=scenario.customer_id,
                customer_messages=list(scenario.customer_messages),
                expected_goal_state=scenario.goal_state,
                observed_summary={
                    "passed": bool(result.get("passed")),
                    "score": float(result.get("score", 0.0)),
                    "observed_intents": list(result.get("observed_intents", [])),
                    "issue_queue_order": list(result.get("issue_queue_order", [])),
                    "tools_called": list(result.get("tools_called", [])),
                    "policies_retrieved": list(result.get("policies_retrieved", [])),
                    "failures": list(result.get("failures", [])),
                },
                rubric=HUMAN_REVIEW_RUBRIC,
                reviewer_instructions=(
                    "Review the expected goal state against the observed summary. "
                    "Assign one 1-5 score and a short rationale for each rubric criterion, then choose approve, needs_fix, or unsafe."
                ),
            )
        )
    return packets


def _result_items(evaluation_result: dict) -> list[dict]:
    results = evaluation_result.get("results")
    if not isinstance(results, list):
        raise ValueError("evaluation_result.results must be a list")
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("evaluation_result.results entries must be dicts")
    return results
