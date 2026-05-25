from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .runner import run_evaluation
from .scenarios import DEFAULT_EVALUATION_SCENARIOS_PATH, EvaluationScenario, load_evaluation_scenarios


NON_COLLABORATIVE_SCENARIOS = {
    "case_06_angry_customer",
    "case_07_vague_customer",
    "case_10_repeated_question_loop",
    "case_11_impatient_user",
    "case_12_tangential_user",
}

METRIC_NAMES = (
    "resolution_success",
    "policy_compliance",
    "correct_tools_called",
    "wrong_tools_avoided",
    "hallucination_count",
    "escalation_correctness",
    "average_turns",
    "audit_trail_coverage",
    "non_collaborative_degradation",
)


@dataclass(frozen=True)
class EvaluationMetric:
    name: str
    value: float
    numerator: float | None
    denominator: float | None
    higher_is_better: bool
    description: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationMetricReport:
    pass_k: int
    scenario_count: int
    total_runs: int
    metrics: dict[str, dict]
    per_scenario: dict[str, dict]
    non_collaborative_scenarios: list[str]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def generate_metric_report(
    evaluation_result: dict | None = None,
    *,
    k: int = 5,
    scenarios_path: Path = DEFAULT_EVALUATION_SCENARIOS_PATH,
    db_path: Path | None = None,
) -> dict:
    if evaluation_result is None:
        evaluation_result = run_evaluation(k=k, scenarios_path=scenarios_path, db_path=db_path)
    if not isinstance(evaluation_result, dict):
        raise ValueError("evaluation_result must be a dict when provided")

    scenarios = load_evaluation_scenarios(scenarios_path)
    scenario_by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    results = _result_items(evaluation_result)
    total_runs = len(results)
    pass_k = int(evaluation_result.get("pass_k", k))
    scenario_count = int(evaluation_result.get("scenario_count", len(scenarios)))

    metrics = _compute_metrics(results, scenario_by_id)
    report = EvaluationMetricReport(
        pass_k=pass_k,
        scenario_count=scenario_count,
        total_runs=total_runs,
        metrics={metric.name: metric.to_dict() for metric in metrics},
        per_scenario=_per_scenario(results, scenario_by_id),
        non_collaborative_scenarios=sorted(NON_COLLABORATIVE_SCENARIOS),
    )
    return report.to_dict()


def _compute_metrics(results: list[dict], scenario_by_id: dict[str, EvaluationScenario]) -> list[EvaluationMetric]:
    total = len(results)
    passed = sum(1 for result in results if result.get("passed") is True)
    required_tool_total = 0
    required_tool_hit = 0
    forbidden_tool_total = 0
    forbidden_tool_clean = 0
    policy_total = 0
    policy_hit = 0
    hallucination_count = 0
    escalation_total = 0
    escalation_correct = 0
    audit_expected = 0
    audit_hit = 0
    turn_total = 0
    noncollab_scores = []
    collaborative_scores = []

    for result in results:
        scenario = _scenario_for_result(result, scenario_by_id)
        goal = scenario.goal_state
        required_tools = list(goal.get("required_tools", []))
        forbidden_tools = list(goal.get("forbidden_tools", []))
        required_policies = list(goal.get("required_policies", []))
        tools_called = set(result.get("tools_called", []))
        policies_retrieved = set(result.get("policies_retrieved", []))

        required_tool_total += len(required_tools)
        required_tool_hit += sum(1 for tool in required_tools if tool in tools_called)
        forbidden_tool_total += len(forbidden_tools)
        forbidden_tool_clean += sum(1 for tool in forbidden_tools if tool not in tools_called)
        policy_total += len(required_policies)
        policy_hit += sum(1 for policy in required_policies if policy in policies_retrieved)
        hallucination_count += len(result.get("forbidden_tools_called", []))
        if _abstention_violated(result, scenario):
            hallucination_count += 1

        expected_escalation = _expected_escalation(scenario)
        if expected_escalation is not None:
            escalation_total += 1
            if _observed_escalation(result) == expected_escalation:
                escalation_correct += 1

        if _expects_audit_trail(scenario):
            audit_expected += 1
            if _has_audit_trail(result):
                audit_hit += 1

        turn_total += len(scenario.customer_messages)
        score = float(result.get("score", 0.0))
        if scenario.scenario_id in NON_COLLABORATIVE_SCENARIOS:
            noncollab_scores.append(score)
        else:
            collaborative_scores.append(score)

    collaborative_average = _average(collaborative_scores)
    noncollab_average = _average(noncollab_scores)
    noncollab_degradation = max(0.0, collaborative_average - noncollab_average)

    return [
        EvaluationMetric(
            name="resolution_success",
            value=_rate(passed, total),
            numerator=float(passed),
            denominator=float(total),
            higher_is_better=True,
            description="Share of pass^k scenario runs with no harness failures.",
        ),
        EvaluationMetric(
            name="policy_compliance",
            value=_rate(policy_hit, policy_total),
            numerator=float(policy_hit),
            denominator=float(policy_total),
            higher_is_better=True,
            description="Share of required policy documents retrieved by scenario runs.",
        ),
        EvaluationMetric(
            name="correct_tools_called",
            value=_rate(required_tool_hit, required_tool_total),
            numerator=float(required_tool_hit),
            denominator=float(required_tool_total),
            higher_is_better=True,
            description="Share of required tools present in each case result.",
        ),
        EvaluationMetric(
            name="wrong_tools_avoided",
            value=_rate(forbidden_tool_clean, forbidden_tool_total),
            numerator=float(forbidden_tool_clean),
            denominator=float(forbidden_tool_total),
            higher_is_better=True,
            description="Share of forbidden tool opportunities avoided.",
        ),
        EvaluationMetric(
            name="hallucination_count",
            value=float(hallucination_count),
            numerator=float(hallucination_count),
            denominator=float(total),
            higher_is_better=False,
            description="Count of forbidden tool calls plus explicit abstention violations.",
        ),
        EvaluationMetric(
            name="escalation_correctness",
            value=_rate(escalation_correct, escalation_total),
            numerator=float(escalation_correct),
            denominator=float(escalation_total),
            higher_is_better=True,
            description="Share of cases whose observed handoff behavior matches expected escalation state.",
        ),
        EvaluationMetric(
            name="average_turns",
            value=round(turn_total / total, 4) if total else 0.0,
            numerator=float(turn_total),
            denominator=float(total),
            higher_is_better=False,
            description="Average number of customer turns per evaluated run.",
        ),
        EvaluationMetric(
            name="audit_trail_coverage",
            value=_rate(audit_hit, audit_expected),
            numerator=float(audit_hit),
            denominator=float(audit_expected),
            higher_is_better=True,
            description="Share of audit-expected runs with audit-capable artifacts generated.",
        ),
        EvaluationMetric(
            name="non_collaborative_degradation",
            value=round(noncollab_degradation, 4),
            numerator=round(noncollab_average, 4),
            denominator=round(collaborative_average, 4),
            higher_is_better=False,
            description="Drop in average score for angry, vague, repeated, impatient, or tangential users versus other cases.",
        ),
    ]


def _per_scenario(results: list[dict], scenario_by_id: dict[str, EvaluationScenario]) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = {}
    for result in results:
        grouped.setdefault(str(result.get("scenario_id")), []).append(result)

    summary = {}
    for scenario_id, items in sorted(grouped.items()):
        scenario = scenario_by_id[scenario_id]
        summary[scenario_id] = {
            "title": scenario.title,
            "runs": len(items),
            "passed_runs": sum(1 for item in items if item.get("passed") is True),
            "average_score": round(_average(float(item.get("score", 0.0)) for item in items), 4),
            "expected_final_status": scenario.goal_state.get("expected_final_status"),
            "non_collaborative": scenario_id in NON_COLLABORATIVE_SCENARIOS,
            "failure_count": sum(len(item.get("failures", [])) for item in items),
        }
    return summary


def _result_items(evaluation_result: dict) -> list[dict]:
    results = evaluation_result.get("results")
    if not isinstance(results, list):
        raise ValueError("evaluation_result.results must be a list")
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("evaluation_result.results entries must be dicts")
    return results


def _scenario_for_result(result: dict, scenario_by_id: dict[str, EvaluationScenario]) -> EvaluationScenario:
    scenario_id = str(result.get("scenario_id", "")).strip()
    if scenario_id not in scenario_by_id:
        raise ValueError(f"unknown scenario_id in evaluation result: {scenario_id!r}")
    return scenario_by_id[scenario_id]


def _expected_escalation(scenario: EvaluationScenario) -> bool | None:
    final_status = scenario.goal_state.get("expected_final_status")
    if final_status == "escalated":
        return True
    if final_status in {"resolved", "active", "ask"}:
        return False
    return None


def _observed_escalation(result: dict) -> bool:
    tools = set(result.get("tools_called", []))
    artifacts = result.get("artifacts", {})
    expected_handoff = _nested_get(artifacts, "tool_results", "generate_handoff_summary")
    return "generate_handoff_summary" in tools or bool(expected_handoff)


def _expects_audit_trail(scenario: EvaluationScenario) -> bool:
    expected = scenario.goal_state.get("expected_artifacts", {})
    return bool(
        expected.get("audit_log_required")
        or expected.get("audit_evidence_contains")
        or expected.get("credit_required")
        or expected.get("ticket_required")
        or expected.get("handoff_required")
        or expected.get("retention_handoff_required")
    )


def _has_audit_trail(result: dict) -> bool:
    tools = set(result.get("tools_called", []))
    return bool(tools & {"apply_credit", "create_ticket", "generate_handoff_summary"})


def _abstention_violated(result: dict, scenario: EvaluationScenario) -> bool:
    expected = scenario.goal_state.get("expected_artifacts", {})
    if not expected.get("must_abstain_from_outage_claim"):
        return False
    outage_result = _nested_get(result.get("artifacts", {}), "tool_results", "check_outage_status")
    if isinstance(outage_result, dict) and outage_result.get("ok") is False:
        return False
    return True


def _nested_get(payload: dict, *keys: str):
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, 4)


def _average(values) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(items) / len(items)
