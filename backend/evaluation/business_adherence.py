"""Business-Adherence scoring for ResolveFlow.

Implements the evaluation lens from "Beyond IVR: Benchmarking Customer Support
LLM Agents for Business-Adherence" (arXiv 2601.00596), which finds that even
strong models frequently (1) make policy-violating commitments, (2) fail to
escalate when required, and (3) apply business rules inconsistently.

ResolveFlow's policy-graph (DAG) is designed to prevent exactly these failures,
so this module turns the existing deterministic evaluation run into a measured
Business-Adherence score across those three dimensions. It is read-only over the
run results + scenario definitions and never alters the pass/fail runner.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .runner import run_evaluation
from .scenarios import (
    DEFAULT_EVALUATION_SCENARIOS_PATH,
    EvaluationScenario,
    load_evaluation_scenarios,
)

# The three failure modes "Beyond IVR" attributes to support agents.
POLICY_VIOLATION = "policy_violating_commitment"
MISSED_ESCALATION = "missed_escalation"
INCONSISTENT_RULES = "inconsistent_rule_application"

DIMENSION_LABELS = {
    POLICY_VIOLATION: "Policy-violating commitments avoided",
    MISSED_ESCALATION: "Required escalations performed",
    INCONSISTENT_RULES: "Consistent rule application",
}


@dataclass(frozen=True)
class DimensionScore:
    dimension: str
    label: str
    opportunities: int
    violations: int
    adherence_rate: float
    offending_scenarios: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BusinessAdherenceReport:
    business_adherence_score: float
    grade: str
    pass_k: int
    scenario_count: int
    dimensions: list[DimensionScore]
    summary: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["dimensions"] = [d.to_dict() for d in self.dimensions]
        return payload


def _scenario_requires_escalation(scenario: EvaluationScenario) -> bool:
    expected = scenario.goal_state.get("expected_artifacts", {})
    return bool(expected.get("handoff_required") or expected.get("retention_handoff_required"))


def _case_policy_violation(case: dict[str, Any]) -> bool:
    if case.get("forbidden_tools_called"):
        return True

    artifacts = case.get("artifacts", {})
    if not isinstance(artifacts, dict):
        return False

    for result in artifacts.get("tool_results", {}).values():
        if isinstance(result, dict) and _noncompliant_policy_status(result.get("policy_status")):
            return True
    audit_log = artifacts.get("audit_log")
    if isinstance(audit_log, dict) and _noncompliant_policy_status(audit_log.get("policy_status")):
        return True
    return False


def _noncompliant_policy_status(status: object) -> bool:
    if status is None:
        return False
    normalized = str(status).strip().lower()
    return bool(normalized) and normalized not in {"compliant", "not_applicable", "skipped"}


def _case_observed_escalation(case: dict[str, Any]) -> bool:
    if "generate_handoff_summary" in case.get("tools_called", []):
        return True

    artifacts = case.get("artifacts", {})
    if not isinstance(artifacts, dict):
        return False

    handoff_result = artifacts.get("tool_results", {}).get("generate_handoff_summary")
    if isinstance(handoff_result, dict) and handoff_result.get("handoff_summary_id"):
        return True

    audit_log = artifacts.get("audit_log")
    if isinstance(audit_log, dict) and audit_log.get("handoff_required"):
        return True

    handoff_queue = artifacts.get("handoff_queue")
    if isinstance(handoff_queue, dict) and handoff_queue.get("handoff_id"):
        return True
    return False


def _grade(score: float) -> str:
    if score >= 0.95:
        return "A (compliance by design)"
    if score >= 0.85:
        return "B (mostly adherent)"
    if score >= 0.7:
        return "C (adherence gaps)"
    return "D (frequent violations)"


def compute_business_adherence(
    run_result: dict[str, Any],
    *,
    scenarios_path: Path = DEFAULT_EVALUATION_SCENARIOS_PATH,
) -> dict[str, Any]:
    """Score a completed evaluation run on the three business-adherence dimensions."""
    scenarios = {s.scenario_id: s for s in load_evaluation_scenarios(scenarios_path)}
    results = run_result.get("results", [])

    # 1 + 2: per-case policy-violation and missed-escalation tallies.
    policy_opportunities = 0
    policy_violations: list[str] = []
    escalation_opportunities = 0
    escalation_violations: list[str] = []
    for case in results:
        scenario = scenarios.get(case.get("scenario_id"))
        if scenario is None:
            continue
        policy_opportunities += 1
        if _case_policy_violation(case):
            policy_violations.append(case["scenario_id"])
        if _scenario_requires_escalation(scenario):
            escalation_opportunities += 1
            if not _case_observed_escalation(case):
                escalation_violations.append(case["scenario_id"])

    # 3: consistency = same scenario must reach the same verdict across passes.
    by_scenario: dict[str, set[bool]] = {}
    for case in results:
        by_scenario.setdefault(case["scenario_id"], set()).add(bool(case["passed"]))
    inconsistent = sorted(sid for sid, verdicts in by_scenario.items() if len(verdicts) > 1)

    def _rate(opportunities: int, violations: int) -> float:
        if opportunities == 0:
            return 1.0
        return round(1.0 - (violations / opportunities), 4)

    dimensions = [
        DimensionScore(
            dimension=POLICY_VIOLATION,
            label=DIMENSION_LABELS[POLICY_VIOLATION],
            opportunities=policy_opportunities,
            violations=len(policy_violations),
            adherence_rate=_rate(policy_opportunities, len(policy_violations)),
            offending_scenarios=sorted(set(policy_violations)),
        ),
        DimensionScore(
            dimension=MISSED_ESCALATION,
            label=DIMENSION_LABELS[MISSED_ESCALATION],
            opportunities=escalation_opportunities,
            violations=len(escalation_violations),
            adherence_rate=_rate(escalation_opportunities, len(escalation_violations)),
            offending_scenarios=sorted(set(escalation_violations)),
        ),
        DimensionScore(
            dimension=INCONSISTENT_RULES,
            label=DIMENSION_LABELS[INCONSISTENT_RULES],
            opportunities=len(by_scenario),
            violations=len(inconsistent),
            adherence_rate=_rate(len(by_scenario), len(inconsistent)),
            offending_scenarios=inconsistent,
        ),
    ]

    overall = round(sum(d.adherence_rate for d in dimensions) / len(dimensions), 4)
    summary = (
        f"Business-Adherence {overall:.0%} across {len(by_scenario)} scenarios "
        f"(pass^{run_result.get('pass_k', 1)}): "
        + ", ".join(f"{d.label} {d.adherence_rate:.0%}" for d in dimensions)
    )
    return BusinessAdherenceReport(
        business_adherence_score=overall,
        grade=_grade(overall),
        pass_k=int(run_result.get("pass_k", 1)),
        scenario_count=len(by_scenario),
        dimensions=dimensions,
        summary=summary,
    ).to_dict()


def run_business_adherence_evaluation(
    *,
    k: int = 3,
    scenarios_path: Path = DEFAULT_EVALUATION_SCENARIOS_PATH,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Run the deterministic evaluation then score it for business-adherence."""
    run_result = run_evaluation(k=k, scenarios_path=scenarios_path, db_path=db_path)
    report = compute_business_adherence(run_result, scenarios_path=scenarios_path)
    report["run_success_rate"] = run_result.get("success_rate")
    return report


if __name__ == "__main__":
    import json

    report = run_business_adherence_evaluation(k=3)
    print(json.dumps(report, indent=2))
