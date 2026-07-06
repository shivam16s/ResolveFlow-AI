from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .llm_client import GeminiGenerateClient


RETRIEVE_TOKENS = ("yes", "no", "continue")
RETRIEVE_TOKEN_LABELS = {
    "yes": "[Retrieve]",
    "no": "[No Retrieve]",
    "continue": "[Continue]",
}
CRAG_RELEVANCE_LABELS = ("relevant", "ambiguous", "irrelevant")
CRAG_RELEVANT_THRESHOLD = 0.6
CRAG_IRRELEVANT_THRESHOLD = 0.2
CRAG_MAX_STRIP_EVALUATION_WORKERS = 6


@dataclass(frozen=True)
class RetrieveDecision:
    token: str
    label: str
    should_retrieve: bool
    confidence: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CRAGRelevanceEvaluation:
    relevance: str
    score: float
    is_relevant: bool
    route: str
    rationale: str
    evidence_terms: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PolicyStrip:
    strip_id: str
    text: str
    strip_index: int
    source_id: str
    token_count: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ScoredPolicyStrip:
    strip: PolicyStrip
    evaluation: CRAGRelevanceEvaluation

    def to_dict(self) -> dict:
        return {
            "strip": self.strip.to_dict(),
            "evaluation": self.evaluation.to_dict(),
        }


@dataclass(frozen=True)
class CRAGCorrectPathResult:
    route: str
    source_id: str
    strips_considered: int
    refined_strips: list[ScoredPolicyStrip]

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "source_id": self.source_id,
            "strips_considered": self.strips_considered,
            "refined_strips": [strip.to_dict() for strip in self.refined_strips],
        }


@dataclass(frozen=True)
class PolicyQueryRewrite:
    rewritten_query: str
    keywords: list[str]
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CRAGIncorrectPathResult:
    route: str
    original_query: str
    rewritten_query: str
    candidates_considered: int
    strips_considered: int
    refined_strips: list[ScoredPolicyStrip]

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "original_query": self.original_query,
            "rewritten_query": self.rewritten_query,
            "candidates_considered": self.candidates_considered,
            "strips_considered": self.strips_considered,
            "refined_strips": [strip.to_dict() for strip in self.refined_strips],
        }


@dataclass(frozen=True)
class CRAGAmbiguousPathResult:
    route: str
    source_id: str
    external_provider: str
    internal_strips_considered: int
    external_strips_considered: int
    combined_strips: list[ScoredPolicyStrip]

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "source_id": self.source_id,
            "external_provider": self.external_provider,
            "internal_strips_considered": self.internal_strips_considered,
            "external_strips_considered": self.external_strips_considered,
            "combined_strips": [strip.to_dict() for strip in self.combined_strips],
        }


@dataclass(frozen=True)
class AnswerSupportUsefulnessScore:
    is_sup: bool
    is_use: bool
    support_score: float
    usefulness_score: float
    cited_strip_ids: list[str]
    missing_claims: list[str]
    rationale: str

    def to_dict(self) -> dict:
        return asdict(self)


class SelfRAGRetrieveDecider:
    """Self-RAG [Retrieve] token decision for policy-grounded retrieval."""

    def __init__(self, llm_client: Callable[[str], str] | None = None) -> None:
        self.llm_client = llm_client or GeminiGenerateClient()

    def decide(self, query: str) -> RetrieveDecision:
        normalized = re.sub(r"\s+", " ", query.strip())
        if not normalized:
            raise ValueError("query must not be empty")

        raw_output = self.llm_client(
            build_retrieve_decision_prompt(normalized))
        payload = _extract_json_object(raw_output)
        return _decision_from_payload(payload)

    def decide_json(self, query: str) -> str:
        return json.dumps(self.decide(query).to_dict(), sort_keys=True)


def decide_policy_retrieval(query: str, llm_client: Callable[[str], str] | None = None) -> RetrieveDecision:
    if llm_client is None:
        return rule_based_retrieve_decision(query)
    return SelfRAGRetrieveDecider(llm_client=llm_client).decide(query)


def build_retrieve_decision_prompt(query: str) -> str:
    schema = {
        "retrieve": "yes|no|continue",
        "confidence": 0.0,
        "reason": "short reason grounded in the query",
    }
    return (
        "You are the Self-RAG retrieval controller for a telecom customer-care policy system.\n"
        "Predict the [Retrieve] reflection token for the user query.\n"
        "Use retrieve=yes when answering safely requires policy, refund, credit, cancellation, escalation, plan, billing, technician, or compliance evidence.\n"
        "Use retrieve=no when the query is greetings, small talk, acknowledgments, or can be answered without policy evidence.\n"
        "Use retrieve=continue when the query is a partial follow-up and more conversation context is needed before deciding.\n"
        "Return JSON only, with no markdown or extra keys.\n\n"
        "Schema:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        f"Query: {query}"
    )


class CRAGRelevanceEvaluator:
    """LLM-as-judge evaluator for CRAG policy retrieval quality."""

    def __init__(self, llm_client: Callable[[str], str] | None = None) -> None:
        self.llm_client = llm_client or GeminiGenerateClient()

    def evaluate(self, query: str, document: str) -> CRAGRelevanceEvaluation:
        normalized_query = re.sub(r"\s+", " ", query.strip())
        normalized_document = re.sub(r"\s+", " ", document.strip())
        if not normalized_query:
            raise ValueError("query must not be empty")
        if not normalized_document:
            raise ValueError("document must not be empty")

        raw_output = self.llm_client(build_crag_relevance_prompt(
            normalized_query, normalized_document))
        payload = _extract_json_object(
            raw_output, error_prefix="CRAG relevance")
        return _evaluation_from_payload(payload)

    def evaluate_json(self, query: str, document: str) -> str:
        return json.dumps(self.evaluate(query, document).to_dict(), sort_keys=True)


def evaluate_policy_relevance(
    query: str,
    document: str,
    llm_client: Callable[[str], str] | None = None,
) -> CRAGRelevanceEvaluation:
    if llm_client is None:
        return rule_based_relevance_evaluation(query, document)
    return CRAGRelevanceEvaluator(llm_client=llm_client).evaluate(query, document)


def crag_correct_path(
    query: str,
    document: str,
    *,
    source_id: str = "policy",
    llm_client: Callable[[str], str] | None = None,
    threshold: float = CRAG_RELEVANT_THRESHOLD,
    max_strip_tokens: int = 120,
) -> CRAGCorrectPathResult:
    normalized_query = re.sub(r"\s+", " ", query.strip())
    normalized_document = document.strip()
    source_id = source_id.strip() or "policy"
    if not normalized_query:
        raise ValueError("query must not be empty")
    if not normalized_document:
        raise ValueError("document must not be empty")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.0 and 1.0")

    strips = decompose_policy_to_strips(
        normalized_document,
        source_id=source_id,
        max_strip_tokens=max_strip_tokens,
    )
    evaluator = CRAGRelevanceEvaluator(llm_client=llm_client)
    refined = _score_policy_strips(
        normalized_query,
        strips,
        evaluator=evaluator,
        threshold=threshold,
    )

    refined.sort(
        key=lambda item: (
            -item.evaluation.score,
            item.strip.strip_index,
            item.strip.strip_id,
        )
    )
    return CRAGCorrectPathResult(
        route="correct",
        source_id=source_id,
        strips_considered=len(strips),
        refined_strips=refined,
    )


class CRAGKeywordRewriter:
    """LLM keyword rewriter for the CRAG INCORRECT retry path."""

    def __init__(self, llm_client: Callable[[str], str] | None = None) -> None:
        self.llm_client = llm_client or GeminiGenerateClient()

    def rewrite(self, query: str) -> PolicyQueryRewrite:
        normalized = re.sub(r"\s+", " ", query.strip())
        if not normalized:
            raise ValueError("query must not be empty")

        raw_output = self.llm_client(
            build_crag_keyword_rewrite_prompt(normalized))
        payload = _extract_json_object(
            raw_output, error_prefix="CRAG keyword rewrite")
        return _rewrite_from_payload(payload, original_query=normalized)


def rewrite_policy_query_keywords(
    query: str,
    llm_client: Callable[[str], str] | None = None,
) -> PolicyQueryRewrite:
    if llm_client is None:
        return rule_based_keyword_rewrite(query)
    return CRAGKeywordRewriter(llm_client=llm_client).rewrite(query)


def crag_incorrect_path(
    query: str,
    policy_store,
    *,
    llm_client: Callable[[str], str] | None = None,
    top_k: int = 5,
    threshold: float = CRAG_IRRELEVANT_THRESHOLD,
    max_strip_tokens: int = 120,
) -> CRAGIncorrectPathResult:
    normalized_query = re.sub(r"\s+", " ", query.strip())
    if not normalized_query:
        raise ValueError("query must not be empty")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.0 and 1.0")
    if not hasattr(policy_store, "query"):
        raise ValueError(
            "policy_store must provide a query(query_text, top_k) method")

    rewrite = CRAGKeywordRewriter(
        llm_client=llm_client).rewrite(normalized_query)
    retry_results = policy_store.query(rewrite.rewritten_query, top_k=top_k)
    candidates = _policy_candidates_from_query_results(retry_results)
    evaluator = CRAGRelevanceEvaluator(llm_client=llm_client)
    all_strips = []
    strips_considered = 0

    for candidate in candidates:
        source_id = str(candidate.get("id") or candidate.get(
            "metadata", {}).get("policy_id") or "policy")
        strips = decompose_policy_to_strips(
            str(candidate["document"]),
            source_id=source_id,
            max_strip_tokens=max_strip_tokens,
        )
        strips_considered += len(strips)
        all_strips.extend(strips)

    refined = _score_policy_strips(
        normalized_query,
        all_strips,
        evaluator=evaluator,
        threshold=threshold,
    )

    refined.sort(
        key=lambda item: (
            -item.evaluation.score,
            item.strip.source_id,
            item.strip.strip_index,
        )
    )
    return CRAGIncorrectPathResult(
        route="incorrect",
        original_query=normalized_query,
        rewritten_query=rewrite.rewritten_query,
        candidates_considered=len(candidates),
        strips_considered=strips_considered,
        refined_strips=refined,
    )


def crag_ambiguous_path(
    query: str,
    document: str,
    *,
    source_id: str = "policy",
    external_strip_provider: Callable[[str], list[Any]] | None = None,
    llm_client: Callable[[str], str] | None = None,
    threshold: float = CRAG_IRRELEVANT_THRESHOLD,
    max_strip_tokens: int = 120,
) -> CRAGAmbiguousPathResult:
    normalized_query = re.sub(r"\s+", " ", query.strip())
    normalized_document = document.strip()
    source_id = source_id.strip() or "policy"
    if not normalized_query:
        raise ValueError("query must not be empty")
    if not normalized_document:
        raise ValueError("document must not be empty")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.0 and 1.0")
    if external_strip_provider is not None and not callable(external_strip_provider):
        raise ValueError("external_strip_provider must be callable")

    provider = external_strip_provider or mock_external_policy_strips
    provider_name = getattr(provider, "__name__", "external_strip_provider")
    internal_strips = decompose_policy_to_strips(
        normalized_document,
        source_id=source_id,
        max_strip_tokens=max_strip_tokens,
    )
    external_strips = _normalize_external_policy_strips(
        provider(normalized_query),
        provider_name=provider_name,
        max_strip_tokens=max_strip_tokens,
    )

    evaluator = CRAGRelevanceEvaluator(llm_client=llm_client)
    combined = _score_policy_strips(
        normalized_query,
        [*internal_strips, *external_strips],
        evaluator=evaluator,
        threshold=threshold,
    )

    combined.sort(
        key=lambda item: (
            -item.evaluation.score,
            0 if item.strip.source_id == source_id else 1,
            item.strip.source_id,
            item.strip.strip_index,
        )
    )
    return CRAGAmbiguousPathResult(
        route="ambiguous",
        source_id=source_id,
        external_provider=provider_name,
        internal_strips_considered=len(internal_strips),
        external_strips_considered=len(external_strips),
        combined_strips=combined,
    )


def mock_external_policy_strips(query: str) -> list[dict]:
    normalized_query = re.sub(r"\s+", " ", query.strip())
    if not normalized_query:
        raise ValueError("query must not be empty")
    return []


def score_answer_support_usefulness(
    *,
    query: str,
    answer: str,
    evidence_strips: list[ScoredPolicyStrip | PolicyStrip | dict],
    llm_client: Callable[[str], str] | None = None,
) -> AnswerSupportUsefulnessScore:
    normalized_query = re.sub(r"\s+", " ", query.strip())
    normalized_answer = re.sub(r"\s+", " ", answer.strip())
    evidence = _normalize_answer_evidence(evidence_strips)
    if not normalized_query:
        raise ValueError("query must not be empty")
    if not normalized_answer:
        raise ValueError("answer must not be empty")
    if not evidence:
        return AnswerSupportUsefulnessScore(
            is_sup=False,
            is_use=False,
            support_score=0.0,
            usefulness_score=0.0,
            cited_strip_ids=[],
            missing_claims=["No policy evidence strips were supplied."],
            rationale="Final answer cannot be scored as supported or useful without policy evidence.",
        )

    if llm_client is None:
        return rule_based_answer_support_usefulness(
            query=normalized_query,
            answer=normalized_answer,
            evidence=evidence,
        )

    raw_output = llm_client(build_answer_support_usefulness_prompt(
        normalized_query, normalized_answer, evidence))
    payload = _extract_json_object(
        raw_output, error_prefix="answer support/usefulness")
    return _answer_score_from_payload(payload, evidence)


def answer_passes_evidence_gate(
    score: AnswerSupportUsefulnessScore,
    *,
    min_support: float = CRAG_RELEVANT_THRESHOLD,
    min_usefulness: float = CRAG_RELEVANT_THRESHOLD,
) -> bool:
    return (
        score.is_sup
        and score.is_use
        and score.support_score >= min_support
        and score.usefulness_score >= min_usefulness
    )


def decompose_policy_to_strips(
    document: str,
    *,
    source_id: str = "policy",
    max_strip_tokens: int = 120,
) -> list[PolicyStrip]:
    normalized_document = document.strip()
    source_id = source_id.strip() or "policy"
    if not normalized_document:
        raise ValueError("document must not be empty")
    if max_strip_tokens < 1:
        raise ValueError("max_strip_tokens must be at least 1")

    sections = _markdown_sections(normalized_document)
    strips = []
    for section in sections:
        for window in _strip_windows(section, max_strip_tokens=max_strip_tokens):
            strips.append(
                PolicyStrip(
                    strip_id=f"{source_id}#strip-{len(strips)}",
                    text=window,
                    strip_index=len(strips),
                    source_id=source_id,
                    token_count=len(_tokens(window)),
                )
            )
    return strips


def build_crag_relevance_prompt(query: str, document: str) -> str:
    schema = {
        "relevance": "relevant|ambiguous|irrelevant",
        "score": 0.0,
        "rationale": "short explanation grounded in query and policy text",
        "evidence_terms": ["term or phrase from the policy document"],
    }
    return (
        "You are the CRAG retrieval evaluator for a telecom policy RAG system.\n"
        "Judge whether the retrieved policy document is useful for answering the query.\n"
        "Score from 0.0 to 1.0 using these routing thresholds:\n"
        f"- relevant: score > {CRAG_RELEVANT_THRESHOLD}\n"
        f"- irrelevant: score < {CRAG_IRRELEVANT_THRESHOLD}\n"
        "- ambiguous: otherwise\n"
        "Only mark relevant when the policy text directly supports the answer or required decision.\n"
        "Return JSON only, with no markdown, commentary, or extra keys.\n\n"
        "Schema:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        f"Query: {query}\n\n"
        "Policy document:\n"
        f"{document}"
    )


def build_crag_keyword_rewrite_prompt(query: str) -> str:
    schema = {
        "rewritten_query": "space-separated policy search keywords",
        "keywords": ["keyword"],
        "reason": "short reason for the rewrite",
    }
    return (
        "You are the CRAG query rewriter for a telecom policy retrieval system.\n"
        "The first retrieval attempt was weak or irrelevant.\n"
        "Rewrite the user query into concise policy-search keywords that are likely to match internal telecom policy documents.\n"
        "Prefer policy terms such as service credit, refund, duplicate charge, cancellation, technician visit, plan change, payment failure, escalation, outage, invoice, eligibility, evidence, limit.\n"
        "Do not add external facts. Return JSON only, with no markdown or extra keys.\n\n"
        "Schema:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        f"Original query: {query}"
    )


def build_answer_support_usefulness_prompt(query: str, answer: str, evidence: list[dict]) -> str:
    schema = {
        "is_sup": True,
        "is_use": True,
        "support_score": 0.0,
        "usefulness_score": 0.0,
        "cited_strip_ids": ["policy#strip-0"],
        "missing_claims": ["claim not supported by evidence"],
        "rationale": "short explanation",
    }
    return (
        "You are the Self-RAG final answer judge for a telecom policy assistant.\n"
        "Score the answer with two binary reflection labels:\n"
        "- [IsSup]: true only when important claims are supported by the supplied policy evidence.\n"
        "- [IsUse]: true only when the answer directly helps the customer with the query.\n"
        "Use support_score and usefulness_score from 0.0 to 1.0.\n"
        "Return JSON only, with no markdown or extra keys.\n\n"
        "Schema:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        f"Query: {query}\n\n"
        f"Answer: {answer}\n\n"
        "Evidence strips:\n"
        f"{json.dumps(evidence, indent=2)}"
    )


def rule_based_retrieve_decision(query: str) -> RetrieveDecision:
    normalized = re.sub(r"\s+", " ", query.strip())
    if not normalized:
        raise ValueError("query must not be empty")

    text = normalized.lower()
    if _looks_like_context_dependent_followup(text):
        return _decision("continue", 0.64, "The query appears to be a context-dependent follow-up.")
    if any(term in text for term in RETRIEVE_REQUIRED_TERMS):
        return _decision("yes", 0.78, "The query may require policy-grounded evidence.")
    if any(term in text for term in NO_RETRIEVE_TERMS) or len(text.split()) <= 3:
        return _decision("no", 0.72, "The query does not appear to require policy retrieval.")
    return _decision("yes", 0.58, "Defaulting to retrieval for an unclear customer-support request.")


def rule_based_relevance_evaluation(query: str, document: str) -> CRAGRelevanceEvaluation:
    normalized_query = re.sub(r"\s+", " ", query.strip())
    normalized_document = re.sub(r"\s+", " ", document.strip())
    if not normalized_query:
        raise ValueError("query must not be empty")
    if not normalized_document:
        raise ValueError("document must not be empty")

    query_terms = _content_terms(normalized_query)
    document_terms = _content_terms(normalized_document)
    if not query_terms:
        return _evaluation(
            score=0.0,
            rationale="No meaningful query terms were available for relevance scoring.",
            evidence_terms=[],
        )

    overlap = sorted(query_terms & document_terms)
    policy_signal = _policy_signal_score(
        normalized_query.lower(), normalized_document.lower())
    lexical_score = len(overlap) / max(len(query_terms), 1)
    score = min(1.0, (0.65 * lexical_score) + (0.35 * policy_signal))
    return _evaluation(
        score=score,
        rationale="Rule-based relevance uses query/policy term overlap and telecom policy signals.",
        evidence_terms=overlap[:8],
    )


def rule_based_keyword_rewrite(query: str) -> PolicyQueryRewrite:
    normalized = re.sub(r"\s+", " ", query.strip())
    if not normalized:
        raise ValueError("query must not be empty")

    text = normalized.lower()
    keywords = []
    rewrite_map = {
        "duplicate charge": ("duplicate", "charged twice", "charged me twice", "twice", "double payment", "invoice"),
        "refund": ("refund", "money back", "duplicate amount", "billing"),
        "service credit": ("credit", "outage", "service disruption", "eligible"),
        "cancellation": ("cancel", "disconnect", "retention", "fee"),
        "technician visit": ("technician", "engineer", "appointment", "router diagnostic"),
        "plan change": ("plan", "upgrade", "downgrade", "speed"),
        "payment failure": ("payment", "failed payment", "retry", "invoice"),
        "escalation": ("escalate", "supervisor", "handoff", "exception"),
    }
    for policy_term, triggers in rewrite_map.items():
        if any(trigger in text for trigger in triggers) or policy_term in text:
            keywords.append(policy_term)
            keywords.extend(triggers)

    if not keywords:
        keywords = [term for term in _content_terms(
            normalized) if term not in STOPWORDS]

    clean_keywords = _dedupe(
        [keyword for keyword in keywords if keyword.strip()])[:12]
    rewritten_query = " ".join(
        clean_keywords) if clean_keywords else normalized
    return PolicyQueryRewrite(
        rewritten_query=rewritten_query,
        keywords=clean_keywords,
        reason="Rule-based rewrite expands customer language into telecom policy terms.",
    )


def rule_based_answer_support_usefulness(
    *,
    query: str,
    answer: str,
    evidence: list[dict],
) -> AnswerSupportUsefulnessScore:
    query_terms = _content_terms(query)
    answer_terms = _content_terms(answer)
    evidence_text = " ".join(str(item["text"]) for item in evidence)
    evidence_terms = _content_terms(evidence_text)
    cited_strip_ids = [
        str(item["strip_id"])
        for item in evidence
        if _content_terms(str(item["text"])) & answer_terms
    ]

    support_overlap = answer_terms & evidence_terms
    usefulness_overlap = query_terms & answer_terms
    support_score = min(1.0, len(support_overlap) / max(len(answer_terms), 1))
    usefulness_score = min(
        1.0, len(usefulness_overlap) / max(len(query_terms), 1))
    missing_claims = []
    if support_score < CRAG_RELEVANT_THRESHOLD:
        missing_claims.append(
            "Answer contains too few terms grounded in supplied policy evidence.")
    if usefulness_score < CRAG_RELEVANT_THRESHOLD:
        missing_claims.append(
            "Answer does not directly cover enough of the customer query.")

    return AnswerSupportUsefulnessScore(
        is_sup=support_score >= CRAG_RELEVANT_THRESHOLD and bool(
            cited_strip_ids),
        is_use=usefulness_score >= CRAG_RELEVANT_THRESHOLD,
        support_score=round(support_score, 2),
        usefulness_score=round(usefulness_score, 2),
        cited_strip_ids=_dedupe(cited_strip_ids),
        missing_claims=missing_claims,
        rationale="Rule-based [IsSup]/[IsUse] uses answer/evidence and answer/query term overlap.",
    )


RETRIEVE_REQUIRED_TERMS = (
    "policy",
    "credit",
    "refund",
    "duplicate",
    "charged",
    "charge",
    "invoice",
    "payment",
    "cancel",
    "cancellation",
    "disconnect",
    "outage",
    "down",
    "not working",
    "technician",
    "engineer",
    "appointment",
    "plan",
    "upgrade",
    "downgrade",
    "waive",
    "fee",
    "compensate",
    "escalate",
    "eligible",
    "allowed",
    "can you apply",
)

NO_RETRIEVE_TERMS = (
    "hello",
    "hi",
    "thanks",
    "thank you",
    "ok",
    "okay",
    "bye",
    "goodbye",
)

FOLLOWUP_TERMS = (
    "what about that",
    "do that",
    "same issue",
    "continue",
    "then what",
    "what next",
    "that one",
    "it",
    "this",
)


def _looks_like_context_dependent_followup(text: str) -> bool:
    if text in FOLLOWUP_TERMS:
        return True
    return len(text.split()) <= 5 and any(term in text for term in FOLLOWUP_TERMS)


def _extract_json_object(raw_output: str, *, error_prefix: str = "retrieve decision") -> dict:
    cleaned = raw_output.strip()
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{error_prefix} LLM output was not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{error_prefix} LLM output must be a JSON object")
    return payload


def _decision_from_payload(payload: dict) -> RetrieveDecision:
    token = str(payload.get("retrieve", "")).strip().lower()
    if token not in RETRIEVE_TOKENS:
        raise ValueError(f"unknown retrieve token from LLM: {token}")
    return _decision(
        token=token,
        confidence=_clean_confidence(payload.get("confidence", 0.7)),
        reason=str(payload.get("reason", "")).strip(
        ) or "LLM retrieval decision.",
    )


def _evaluation_from_payload(payload: dict) -> CRAGRelevanceEvaluation:
    score = _clean_confidence(payload.get("score", 0.0))
    relevance = str(payload.get("relevance", "")).strip().lower()
    expected_relevance = _relevance_from_score(score)
    if relevance not in CRAG_RELEVANCE_LABELS:
        relevance = expected_relevance
    if relevance != expected_relevance:
        relevance = expected_relevance

    evidence_terms = [
        re.sub(r"\s+", " ", str(term).strip())
        for term in payload.get("evidence_terms", [])
        if str(term).strip()
    ]
    return CRAGRelevanceEvaluation(
        relevance=relevance,
        score=score,
        is_relevant=relevance == "relevant",
        route=_route_for_relevance(relevance),
        rationale=re.sub(r"\s+", " ", str(payload.get("rationale", "")
                                          ).strip()) or "LLM relevance evaluation.",
        evidence_terms=_dedupe(evidence_terms)[:8],
    )


def _rewrite_from_payload(payload: dict, *, original_query: str) -> PolicyQueryRewrite:
    raw_keywords = payload.get("keywords", [])
    keywords = [
        re.sub(r"\s+", " ", str(keyword).strip())
        for keyword in raw_keywords
        if str(keyword).strip()
    ]
    keywords = _dedupe(keywords)[:12]
    rewritten_query = re.sub(
        r"\s+", " ", str(payload.get("rewritten_query", "")).strip())
    if not rewritten_query:
        rewritten_query = " ".join(keywords)
    if not rewritten_query:
        rewritten_query = rule_based_keyword_rewrite(
            original_query).rewritten_query

    return PolicyQueryRewrite(
        rewritten_query=rewritten_query,
        keywords=keywords or rewritten_query.split(),
        reason=re.sub(r"\s+", " ", str(payload.get("reason", "")
                                       ).strip()) or "LLM keyword rewrite.",
    )


def _answer_score_from_payload(payload: dict, evidence: list[dict]) -> AnswerSupportUsefulnessScore:
    known_strip_ids = {str(item["strip_id"]) for item in evidence}
    cited_strip_ids = [
        str(strip_id).strip()
        for strip_id in payload.get("cited_strip_ids", [])
        if str(strip_id).strip() in known_strip_ids
    ]
    missing_claims = [
        re.sub(r"\s+", " ", str(claim).strip())
        for claim in payload.get("missing_claims", [])
        if str(claim).strip()
    ]
    support_score = _clean_confidence(payload.get("support_score", 0.0))
    usefulness_score = _clean_confidence(payload.get("usefulness_score", 0.0))
    is_sup = bool(payload.get(
        "is_sup")) and support_score >= CRAG_RELEVANT_THRESHOLD and bool(cited_strip_ids)
    is_use = bool(payload.get("is_use")
                  ) and usefulness_score >= CRAG_RELEVANT_THRESHOLD
    return AnswerSupportUsefulnessScore(
        is_sup=is_sup,
        is_use=is_use,
        support_score=support_score,
        usefulness_score=usefulness_score,
        cited_strip_ids=_dedupe(cited_strip_ids),
        missing_claims=missing_claims[:8],
        rationale=re.sub(r"\s+", " ", str(payload.get("rationale", "")
                                          ).strip()) or "LLM [IsSup]/[IsUse] evaluation.",
    )


def _decision(token: str, confidence: float, reason: str) -> RetrieveDecision:
    return RetrieveDecision(
        token=token,
        label=RETRIEVE_TOKEN_LABELS[token],
        should_retrieve=token == "yes",
        confidence=_clean_confidence(confidence),
        reason=re.sub(r"\s+", " ", reason.strip()),
    )


def _evaluation(score: float, rationale: str, evidence_terms: list[str]) -> CRAGRelevanceEvaluation:
    clean_score = _clean_confidence(score)
    relevance = _relevance_from_score(clean_score)
    return CRAGRelevanceEvaluation(
        relevance=relevance,
        score=clean_score,
        is_relevant=relevance == "relevant",
        route=_route_for_relevance(relevance),
        rationale=re.sub(r"\s+", " ", rationale.strip()),
        evidence_terms=_dedupe(evidence_terms)[:8],
    )


def _relevance_from_score(score: float) -> str:
    if score > CRAG_RELEVANT_THRESHOLD:
        return "relevant"
    if score < CRAG_IRRELEVANT_THRESHOLD:
        return "irrelevant"
    return "ambiguous"


def _route_for_relevance(relevance: str) -> str:
    return {
        "relevant": "correct",
        "ambiguous": "ambiguous",
        "irrelevant": "incorrect",
    }[relevance]


def _clean_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.7
    return round(max(0.0, min(1.0, confidence)), 2)


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "for",
    "from",
    "has",
    "have",
    "i",
    "if",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "what",
    "when",
    "with",
    "you",
}


def _content_terms(text: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+", text.lower())
        if len(term) > 2 and term not in STOPWORDS
    }


def _policy_signal_score(query: str, document: str) -> float:
    signal_groups = {
        "duplicate": ("duplicate", "charged twice", "two payments"),
        "refund": ("refund", "money back", "duplicate amount"),
        "credit": ("credit", "service credit", "outage"),
        "cancellation": ("cancel", "cancellation", "disconnect"),
        "technician": ("technician", "engineer", "visit", "appointment"),
        "plan": ("plan", "upgrade", "downgrade"),
        "payment": ("payment", "invoice", "billing"),
        "escalation": ("escalate", "supervisor", "handoff"),
    }
    matched = 0
    possible = 0
    for terms in signal_groups.values():
        query_has_signal = any(term in query for term in terms)
        if not query_has_signal:
            continue
        possible += 1
        if any(term in document for term in terms):
            matched += 1
    if possible == 0:
        return 0.0
    return matched / possible


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _policy_candidates_from_query_results(results: dict) -> list[dict]:
    ids = _first_result_list(results.get("ids", []))
    documents = _first_result_list(results.get("documents", []))
    metadatas = _first_result_list(results.get("metadatas", []))
    candidates = []
    for index, document in enumerate(documents):
        if not str(document).strip():
            continue
        metadata = metadatas[index] if index < len(
            metadatas) and isinstance(metadatas[index], dict) else {}
        candidates.append(
            {
                "id": ids[index] if index < len(ids) else metadata.get("policy_id", f"policy-{index}"),
                "document": document,
                "metadata": metadata,
            }
        )
    return candidates


def _score_policy_strips(
    query: str,
    strips: list[PolicyStrip],
    *,
    evaluator: CRAGRelevanceEvaluator,
    threshold: float,
) -> list[ScoredPolicyStrip]:
    if not strips:
        return []

    max_workers = min(CRAG_MAX_STRIP_EVALUATION_WORKERS, len(strips))
    if max_workers <= 1:
        evaluations = [
            _safe_evaluate_policy_strip(query, strip, evaluator=evaluator)
            for strip in strips
        ]
    else:
        evaluations: list[tuple[PolicyStrip, CRAGRelevanceEvaluation] | None] = [
            None
        ] * len(strips)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _safe_evaluate_policy_strip,
                    query,
                    strip,
                    evaluator=evaluator,
                ): index
                for index, strip in enumerate(strips)
            }
            for future in as_completed(futures):
                evaluations[futures[future]] = future.result()

    refined = []
    for strip, evaluation in evaluations:
        if evaluation.score > threshold:
            refined.append(ScoredPolicyStrip(
                strip=strip, evaluation=evaluation))
    return refined


def _safe_evaluate_policy_strip(
    query: str,
    strip: PolicyStrip,
    *,
    evaluator: CRAGRelevanceEvaluator,
) -> tuple[PolicyStrip, CRAGRelevanceEvaluation]:
    try:
        evaluation = evaluator.evaluate(query, strip.text)
    except Exception as exc:
        evaluation = CRAGRelevanceEvaluation(
            relevance="irrelevant",
            score=0.0,
            is_relevant=False,
            route="incorrect",
            rationale=(
                "CRAG relevance evaluation failed for "
                f"{strip.strip_id}: {exc.__class__.__name__}."
            ),
            evidence_terms=[],
        )
    return strip, evaluation


def _normalize_external_policy_strips(
    raw_strips: object,
    *,
    provider_name: str,
    max_strip_tokens: int,
) -> list[PolicyStrip]:
    if raw_strips is None:
        return []
    if not isinstance(raw_strips, list):
        raise ValueError("external_strip_provider must return a list")

    normalized = []
    for index, item in enumerate(raw_strips):
        if isinstance(item, PolicyStrip):
            normalized.append(item)
            continue

        source_id = f"external:{provider_name}"
        text = ""
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get(
                "content") or item.get("document") or "")
            source_id = str(item.get("source_id")
                            or item.get("url") or source_id)
        else:
            raise ValueError(
                "external strips must be strings, dicts, or PolicyStrip objects")

        text = re.sub(r"\s+", " ", text.strip())
        if not text:
            continue
        for window_index, window in enumerate(_strip_windows(text, max_strip_tokens=max_strip_tokens)):
            normalized.append(
                PolicyStrip(
                    strip_id=f"{source_id}#strip-{index}-{window_index}",
                    text=window,
                    strip_index=len(normalized),
                    source_id=source_id,
                    token_count=len(_tokens(window)),
                )
            )
    return normalized


def _normalize_answer_evidence(evidence_strips: list[ScoredPolicyStrip | PolicyStrip | dict]) -> list[dict]:
    evidence = []
    for index, item in enumerate(evidence_strips):
        if isinstance(item, ScoredPolicyStrip):
            strip = item.strip
            score = item.evaluation.score
        elif isinstance(item, PolicyStrip):
            strip = item
            score = None
        elif isinstance(item, dict):
            strip_id = str(item.get("strip_id") or item.get(
                "id") or f"evidence-{index}")
            text = re.sub(r"\s+", " ", str(item.get("text")
                          or item.get("document") or "").strip())
            if not text:
                continue
            evidence.append(
                {
                    "strip_id": strip_id,
                    "source_id": str(item.get("source_id") or "policy"),
                    "text": text,
                    "score": item.get("score"),
                }
            )
            continue
        else:
            raise ValueError(
                "evidence_strips must contain ScoredPolicyStrip, PolicyStrip, or dict values")

        evidence.append(
            {
                "strip_id": strip.strip_id,
                "source_id": strip.source_id,
                "text": strip.text,
                "score": score,
            }
        )
    return evidence


def _first_result_list(value: object) -> list:
    if isinstance(value, list) and value and isinstance(value[0], list):
        return value[0]
    if isinstance(value, list):
        return value
    return []


def _markdown_sections(document: str) -> list[str]:
    lines = document.splitlines()
    sections = []
    current: list[str] = []
    for line in lines:
        if line.startswith("## ") and current:
            sections.append("\n".join(current).strip())
            current = [line]
            continue
        current.append(line)
    if current:
        sections.append("\n".join(current).strip())

    paragraphs = []
    for section in sections:
        section_parts = [part.strip() for part in re.split(
            r"\n\s*\n", section) if part.strip()]
        if len(section_parts) <= 1:
            paragraphs.append(section)
        else:
            heading = section_parts[0] if section_parts[0].startswith(
                "#") else ""
            for part in section_parts:
                if part == heading:
                    continue
                paragraphs.append(
                    f"{heading}\n{part}".strip() if heading else part)

    return [paragraph for paragraph in paragraphs if paragraph]


def _strip_windows(text: str, *, max_strip_tokens: int) -> list[str]:
    tokens = _tokens(text)
    if _is_heading_only_strip(text, tokens):
        return []
    if len(tokens) <= max_strip_tokens:
        return [" ".join(tokens)]
    return [
        " ".join(tokens[index: index + max_strip_tokens])
        for index in range(0, len(tokens), max_strip_tokens)
    ]


def _tokens(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def _is_heading_only_strip(text: str, tokens: list[str]) -> bool:
    stripped = text.strip()
    return stripped.startswith("#") and "\n" not in stripped and len(tokens) <= 8
