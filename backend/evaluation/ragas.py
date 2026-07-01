from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from .runner import run_evaluation
from .scenarios import DEFAULT_EVALUATION_SCENARIOS_PATH, EvaluationScenario, load_evaluation_scenarios


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "with",
}


@dataclass(frozen=True)
class RAGASPolicyRetrievalScore:
    pass_index: int
    scenario_id: str
    policy_id: str
    query: str
    context_recall: float
    context_precision: float
    context_count: int
    supported_terms: list[str]
    missing_terms: list[str]
    relevant_context_ranks: list[int]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RAGASEvaluationReport:
    retrieval_count: int
    average_context_recall: float
    average_context_precision: float
    scores: list[RAGASPolicyRetrievalScore]
    source: str = "ragas_compatible_policy_retrieval_evaluation"

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["scores"] = [score.to_dict() for score in self.scores]
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def evaluate_policy_retrievals_with_ragas(
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

    scenarios = {scenario.scenario_id: scenario for scenario in load_evaluation_scenarios(
        scenarios_path)}
    scores = []
    for result in _result_items(evaluation_result):
        scenario_id = str(result.get("scenario_id", "")).strip()
        if scenario_id not in scenarios:
            raise ValueError(
                f"unknown scenario_id in evaluation result: {scenario_id!r}")
        scenario = scenarios[scenario_id]
        for retrieval in _policy_retrievals(result):
            scores.append(_score_retrieval(result, scenario, retrieval))

    return RAGASEvaluationReport(
        retrieval_count=len(scores),
        average_context_recall=_average(score.context_recall for score in scores),
        average_context_precision=_average(
            score.context_precision for score in scores),
        scores=scores,
    ).to_dict()


def _score_retrieval(
    result: dict,
    scenario: EvaluationScenario,
    retrieval: dict,
) -> RAGASPolicyRetrievalScore:
    policy_id = str(retrieval.get("policy_id")
                    or retrieval.get("policy_name") or "").strip()
    query = str(retrieval.get("query") or " ".join(
        scenario.customer_messages)).strip()
    contexts = _contexts(retrieval)
    answer_terms = _answer_terms(scenario, policy_id)
    context_text = " ".join(contexts).lower()
    supported_terms = [term for term in answer_terms if term in context_text]
    missing_terms = [term for term in answer_terms if term not in context_text]
    context_recall = round(len(supported_terms) /
                         len(answer_terms), 4) if answer_terms else 1.0
    relevant_ranks = _relevant_context_ranks(
        contexts, query=query, answer_terms=answer_terms)
    context_precision = _average_precision(relevant_ranks, len(contexts))

    return RAGASPolicyRetrievalScore(
        pass_index=int(result.get("pass_index", 0)),
        scenario_id=scenario.scenario_id,
        policy_id=policy_id,
        query=query,
        context_recall=context_recall,
        context_precision=context_precision,
        context_count=len(contexts),
        supported_terms=supported_terms,
        missing_terms=missing_terms,
        relevant_context_ranks=relevant_ranks,
    )


def _policy_retrievals(result: dict) -> list[dict]:
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    retrievals = artifacts.get("policy_retrievals")
    if retrievals is None:
        retrieval_tool = artifacts.get(
            "tool_results", {}).get("retrieve_policy")
        if isinstance(retrieval_tool, dict) and isinstance(retrieval_tool.get("retrievals"), list):
            retrievals = retrieval_tool["retrievals"]
    if retrievals is None:
        return []
    if not isinstance(retrievals, list):
        raise ValueError(
            "artifacts.policy_retrievals must be a list when present")
    for retrieval in retrievals:
        if not isinstance(retrieval, dict):
            raise ValueError("policy retrieval entries must be dicts")
    return retrievals


def _contexts(retrieval: dict) -> list[str]:
    strips = retrieval.get("evidence_strips")
    if not isinstance(strips, list):
        return []
    contexts = []
    for strip in strips:
        if isinstance(strip, dict):
            text = strip.get("text")
        else:
            text = str(strip)
        normalized = " ".join(str(text or "").split())
        if normalized:
            contexts.append(normalized)
    return contexts


def _answer_terms(scenario: EvaluationScenario, policy_id: str) -> list[str]:
    """Policy-concept terms the answer relies on, for context_recall scoring.

    Context recall asks whether the concepts the answer depends on are grounded in
    the retrieved policy context. We therefore build expected terms from the
    policy name and the conceptual success criteria, and deliberately EXCLUDE raw
    expected-artifact data values (specific amounts, invoice/credit IDs, status
    enums) and bare numbers: those are case-specific outcomes a policy document
    never restates verbatim, so counting them would understate context_recall by
    construction rather than measure real grounding.
    """
    terms = set(_tokens(policy_id.replace("_", " ")))
    for policy in scenario.goal_state.get("required_policies", []):
        if policy == policy_id:
            terms.update(_tokens(policy.replace("_", " ")))
    for criterion in scenario.goal_state.get("success_criteria", []):
        terms.update(_tokens(str(criterion)))
    return sorted(
        term for term in terms
        if len(term) >= 4 and not _is_numeric(term)
    )


def _is_numeric(token: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", token))


def _relevant_context_ranks(contexts: list[str], *, query: str, answer_terms: list[str]) -> list[int]:
    query_terms = set(_tokens(query))
    answer_term_set = set(answer_terms)
    ranks = []
    for index, context in enumerate(contexts, start=1):
        context_terms = set(_tokens(context))
        query_overlap = len(context_terms & query_terms)
        answer_overlap = len(context_terms & answer_term_set)
        if query_overlap > 0 or answer_overlap >= 2:
            ranks.append(index)
    return ranks


def _average_precision(relevant_ranks: list[int], context_count: int) -> float:
    if context_count <= 0:
        return 0.0
    if not relevant_ranks:
        return 0.0
    precision_sum = 0.0
    for found_index, rank in enumerate(relevant_ranks, start=1):
        precision_sum += found_index / rank
    return round(precision_sum / len(relevant_ranks), 4)


def _tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_/-]*|\d+(?:\.\d+)?", text.lower())
    return [token for token in tokens if token not in STOPWORDS]


def _result_items(evaluation_result: dict) -> list[dict]:
    results = evaluation_result.get("results")
    if not isinstance(results, list):
        raise ValueError("evaluation_result.results must be a list")
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("evaluation_result.results entries must be dicts")
    return results


def _average(values) -> float:
    items = list(values)
    if not items:
        return 0.0
    return round(sum(items) / len(items), 4)
