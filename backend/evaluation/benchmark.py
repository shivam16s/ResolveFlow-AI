from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from .ragas import evaluate_policy_retrievals_with_ragas
from .reporting import generate_metric_report
from .runner import run_evaluation
from .scenarios import DEFAULT_EVALUATION_SCENARIOS_PATH


@dataclass(frozen=True)
class TauBenchBaseline:
    baseline_id: str
    benchmark: str
    domain: str
    model: str
    metric: str
    score: float
    source_name: str
    source_url: str
    retrieved_date: str
    verified: bool
    notes: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkComparisonRow:
    row_id: str
    category: str
    resolveflow_metric: str
    resolveflow_score: float
    baseline_id: str | None
    baseline_label: str | None
    baseline_score: float | None
    delta_vs_baseline: float | None
    ratio_vs_baseline: float | None
    source_url: str | None
    notes: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkComparisonReport:
    generated_from: str
    pass_k: int
    scenario_count: int
    total_runs: int
    baseline_snapshot_date: str
    baselines: list[TauBenchBaseline]
    rows: list[BenchmarkComparisonRow]
    ragas_rows: list[BenchmarkComparisonRow]

    def to_dict(self) -> dict:
        return {
            "generated_from": self.generated_from,
            "pass_k": self.pass_k,
            "scenario_count": self.scenario_count,
            "total_runs": self.total_runs,
            "baseline_snapshot_date": self.baseline_snapshot_date,
            "baselines": [baseline.to_dict() for baseline in self.baselines],
            "rows": [row.to_dict() for row in self.rows],
            "ragas_rows": [row.to_dict() for row in self.ragas_rows],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


DEFAULT_TAU_BENCH_BASELINES = [
    TauBenchBaseline(
        baseline_id="tau_bench_retail_sota_claude_sonnet_4_5",
        benchmark="TAU-bench",
        domain="retail",
        model="Claude Sonnet 4.5",
        metric="pass^1 task success",
        score=0.862,
        source_name="llm-stats TAU-bench Retail leaderboard",
        source_url="https://llm-stats.com/benchmarks/tau-bench-retail",
        retrieved_date="2026-05-24",
        verified=False,
        notes="Public leaderboard snapshot; self-reported result according to the tracker.",
    ),
    TauBenchBaseline(
        baseline_id="tau_bench_airline_sota_claude_sonnet_4_5",
        benchmark="TAU-bench",
        domain="airline",
        model="Claude Sonnet 4.5",
        metric="pass^1 task success",
        score=0.700,
        source_name="llm-stats TAU-bench Airline leaderboard",
        source_url="https://llm-stats.com/benchmarks/tau-bench-airline",
        retrieved_date="2026-05-24",
        verified=False,
        notes="Public leaderboard snapshot; self-reported result according to the tracker.",
    ),
    TauBenchBaseline(
        baseline_id="tau_bench_original_gpt4o_upper_bound",
        benchmark="TAU-bench original paper",
        domain="retail+airline",
        model="GPT-4o function-calling agent",
        metric="task success upper bound",
        score=0.500,
        source_name="Yao et al. 2024 τ-bench paper",
        source_url="https://arxiv.org/abs/2406.12045",
        retrieved_date="2026-05-24",
        verified=True,
        notes="Paper reports GPT-4o-style function-calling agents succeed on less than 50%; encoded as a 0.500 ceiling for comparison.",
    ),
]


def generate_benchmark_comparison(
    evaluation_result: dict | None = None,
    *,
    metric_report: dict | None = None,
    ragas_report: dict | None = None,
    baselines: list[TauBenchBaseline | dict] | None = None,
    k: int = 5,
    scenarios_path: Path = DEFAULT_EVALUATION_SCENARIOS_PATH,
    db_path: Path | None = None,
) -> dict:
    if evaluation_result is None:
        evaluation_result = run_evaluation(
            k=k, scenarios_path=scenarios_path, db_path=db_path)
    if not isinstance(evaluation_result, dict):
        raise ValueError("evaluation_result must be a dict when provided")

    metric_report = metric_report or generate_metric_report(
        evaluation_result, scenarios_path=scenarios_path)
    ragas_report = ragas_report or evaluate_policy_retrievals_with_ragas(
        evaluation_result, scenarios_path=scenarios_path)
    normalized_baselines = _normalize_baselines(
        DEFAULT_TAU_BENCH_BASELINES if baselines is None else baselines)
    metric_rows = _metric_rows(metric_report, normalized_baselines)
    ragas_rows = _ragas_rows(ragas_report)
    rows = metric_rows + ragas_rows

    return BenchmarkComparisonReport(
        generated_from="resolveflow_evaluation_harness",
        pass_k=int(evaluation_result.get("pass_k", k)),
        scenario_count=int(evaluation_result.get("scenario_count", 0)),
        total_runs=int(evaluation_result.get(
            "total_runs", len(evaluation_result.get("results", [])))),
        baseline_snapshot_date=max(
            baseline.retrieved_date for baseline in normalized_baselines),
        baselines=normalized_baselines,
        rows=rows,
        ragas_rows=ragas_rows,
    ).to_dict()


def _metric_rows(metric_report: dict, baselines: list[TauBenchBaseline]) -> list[BenchmarkComparisonRow]:
    metrics = metric_report.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("metric_report.metrics must be a dict")
    resolution = _metric_value(metrics, "resolution_success")
    policy_compliance = _metric_value(metrics, "policy_compliance")
    wrong_tools_avoided = _metric_value(metrics, "wrong_tools_avoided")
    rows = []

    for baseline in baselines:
        rows.append(
            _comparison_row(
                row_id=f"resolveflow_resolution_success_vs_{baseline.baseline_id}",
                category="tau_bench_comparison",
                resolveflow_metric="resolution_success",
                resolveflow_score=resolution,
                baseline=baseline,
                notes="ResolveFlow telecom scenarios are not a τ-bench domain; compare as customer-service tool-agent task-success context.",
            )
        )

    rows.append(
        BenchmarkComparisonRow(
            row_id="resolveflow_policy_compliance_internal",
            category="resolveflow_internal",
            resolveflow_metric="policy_compliance",
            resolveflow_score=policy_compliance,
            baseline_id=None,
            baseline_label=None,
            baseline_score=None,
            delta_vs_baseline=None,
            ratio_vs_baseline=None,
            source_url=None,
            notes="Internal policy retrieval compliance metric; no direct τ-bench baseline row exists.",
        )
    )
    rows.append(
        BenchmarkComparisonRow(
            row_id="resolveflow_wrong_tools_avoided_internal",
            category="resolveflow_internal",
            resolveflow_metric="wrong_tools_avoided",
            resolveflow_score=wrong_tools_avoided,
            baseline_id=None,
            baseline_label=None,
            baseline_score=None,
            delta_vs_baseline=None,
            ratio_vs_baseline=None,
            source_url=None,
            notes="Internal forbidden-tool safety metric; complements τ-bench task-success comparison.",
        )
    )
    return rows


def _ragas_rows(ragas_report: dict) -> list[BenchmarkComparisonRow]:
    if not isinstance(ragas_report, dict):
        raise ValueError("ragas_report must be a dict")
    rows = []
    for metric_name in ("average_faithfulness", "average_context_precision"):
        value = ragas_report.get(metric_name)
        if value is None:
            raise ValueError(f"ragas_report.{metric_name} is required")
        rows.append(
            BenchmarkComparisonRow(
                row_id=f"resolveflow_ragas_{metric_name.removeprefix('average_')}",
                category="ragas_retrieval_quality",
                resolveflow_metric=metric_name,
                resolveflow_score=round(float(value), 4),
                baseline_id=None,
                baseline_label=None,
                baseline_score=None,
                delta_vs_baseline=None,
                ratio_vs_baseline=None,
                source_url=None,
                notes="RAGAS-style retrieval quality row for ResolveFlow policy retrievals; τ-bench public leaderboards do not publish this RAGAS metric.",
            )
        )
    return rows


def _comparison_row(
    *,
    row_id: str,
    category: str,
    resolveflow_metric: str,
    resolveflow_score: float,
    baseline: TauBenchBaseline,
    notes: str,
) -> BenchmarkComparisonRow:
    delta = round(resolveflow_score - baseline.score, 4)
    ratio = round(resolveflow_score / baseline.score,
                  4) if baseline.score else None
    return BenchmarkComparisonRow(
        row_id=row_id,
        category=category,
        resolveflow_metric=resolveflow_metric,
        resolveflow_score=round(resolveflow_score, 4),
        baseline_id=baseline.baseline_id,
        baseline_label=f"{baseline.benchmark} {baseline.domain} {baseline.model}",
        baseline_score=baseline.score,
        delta_vs_baseline=delta,
        ratio_vs_baseline=ratio,
        source_url=baseline.source_url,
        notes=notes,
    )


def _metric_value(metrics: dict, metric_name: str) -> float:
    metric = metrics.get(metric_name)
    if not isinstance(metric, dict) or metric.get("value") is None:
        raise ValueError(
            f"metric_report.metrics.{metric_name}.value is required")
    return round(float(metric["value"]), 4)


def _normalize_baselines(raw_baselines: list[TauBenchBaseline | dict]) -> list[TauBenchBaseline]:
    if not isinstance(raw_baselines, list) or not raw_baselines:
        raise ValueError("baselines must be a non-empty list")
    baselines = []
    for item in raw_baselines:
        if isinstance(item, TauBenchBaseline):
            baseline = item
        elif isinstance(item, dict):
            baseline = TauBenchBaseline(**item)
        else:
            raise ValueError(
                "baseline entries must be TauBenchBaseline or dict objects")
        if baseline.score < 0 or baseline.score > 1:
            raise ValueError("baseline score must be between 0 and 1")
        baselines.append(baseline)
    return baselines
