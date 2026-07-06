from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.policy_retrieval import (
    CRAGKeywordRewriter,
    CRAGRelevanceEvaluator,
    PolicyStrip,
    SelfRAGRetrieveDecider,
    answer_passes_evidence_gate,
    build_answer_support_usefulness_prompt,
    build_crag_keyword_rewrite_prompt,
    build_crag_relevance_prompt,
    crag_ambiguous_path,
    build_retrieve_decision_prompt,
    crag_correct_path,
    crag_incorrect_path,
    decide_policy_retrieval,
    decompose_policy_to_strips,
    evaluate_policy_relevance,
    mock_external_policy_strips,
    rewrite_policy_query_keywords,
    score_answer_support_usefulness,
)


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


class FakePolicyStore:
    def __init__(self) -> None:
        self.queries = []

    def query(self, query_text: str, top_k: int = 5) -> dict:
        self.queries.append({"query_text": query_text, "top_k": top_k})
        return {
            "ids": [["refund_policy_chunk", "router_policy_chunk"]],
            "documents": [
                [
                    """# Refund Policy

## Duplicate Payment

Duplicate charge refunds require invoice ID, payment records, and duplicate payment evidence.
""",
                    """# Technician Visit Policy

## Router Diagnostic

Technician visits require router diagnostics before dispatch.
""",
                ]
            ],
            "metadatas": [[{"policy_id": "refund_policy"}, {"policy_id": "technician_visit_policy"}]],
        }


def assert_prompt_uses_self_rag_tokens() -> None:
    prompt = build_retrieve_decision_prompt("Can I get a service credit?")
    required = [
        "Self-RAG",
        "[Retrieve]",
        "retrieve=yes",
        "retrieve=no",
        "retrieve=continue",
        "Return JSON only",
        "Can I get a service credit?",
    ]
    for phrase in required:
        if phrase not in prompt:
            raise AssertionError(f"retrieve prompt missing phrase: {phrase}")


def assert_crag_prompt_uses_llm_judge_schema() -> None:
    prompt = build_crag_relevance_prompt(
        "Can I get a service credit for an outage?",
        "Verified outage longer than 6 hours may receive a service credit.",
    )
    required = [
        "CRAG retrieval evaluator",
        "Score from 0.0 to 1.0",
        "relevant: score > 0.6",
        "irrelevant: score < 0.2",
        "ambiguous: otherwise",
        "Return JSON only",
        "Can I get a service credit for an outage?",
        "Verified outage longer than 6 hours",
    ]
    for phrase in required:
        if phrase not in prompt:
            raise AssertionError(f"CRAG prompt missing phrase: {phrase}")


def assert_crag_keyword_rewrite_prompt_is_structured() -> None:
    prompt = build_crag_keyword_rewrite_prompt("They took my money twice")
    required = [
        "CRAG query rewriter",
        "first retrieval attempt was weak",
        "policy-search keywords",
        "Return JSON only",
        "Original query: They took my money twice",
    ]
    for phrase in required:
        if phrase not in prompt:
            raise AssertionError(f"keyword rewrite prompt missing phrase: {phrase}")


def assert_answer_support_prompt_is_structured() -> None:
    prompt = build_answer_support_usefulness_prompt(
        "Can I get credit for an outage?",
        "You are eligible for service credit after a verified outage.",
        [
            {
                "strip_id": "service_credit_policy#strip-0",
                "source_id": "service_credit_policy",
                "text": "Verified outage customers may receive service credit after 6 hours.",
            }
        ],
    )
    required = [
        "Self-RAG final answer judge",
        "[IsSup]",
        "[IsUse]",
        "support_score",
        "usefulness_score",
        "Return JSON only",
        "Can I get credit for an outage?",
    ]
    for phrase in required:
        if phrase not in prompt:
            raise AssertionError(f"answer support prompt missing phrase: {phrase}")


def assert_rule_based_decisions_are_conservative() -> None:
    retrieve = decide_policy_retrieval("Can you apply a credit for yesterday's outage?")
    if retrieve.token != "yes" or retrieve.label != "[Retrieve]" or not retrieve.should_retrieve:
        raise AssertionError(f"policy query should retrieve: {retrieve.to_dict()}")

    no_retrieve = decide_policy_retrieval("hello")
    if no_retrieve.token != "no" or no_retrieve.label != "[No Retrieve]" or no_retrieve.should_retrieve:
        raise AssertionError(f"greeting should not retrieve: {no_retrieve.to_dict()}")

    continuation = decide_policy_retrieval("what about that")
    if continuation.token != "continue" or continuation.label != "[Continue]" or continuation.should_retrieve:
        raise AssertionError(f"context follow-up should continue: {continuation.to_dict()}")


def assert_keyword_rewrite_expands_customer_language() -> None:
    rewrite = rewrite_policy_query_keywords("They charged me twice and I want money back")
    if "duplicate charge" not in rewrite.rewritten_query or "refund" not in rewrite.rewritten_query:
        raise AssertionError(f"rule-based rewrite missed policy terms: {rewrite.to_dict()}")

    fake = FakeLLM(
        json.dumps(
            {
                "rewritten_query": "duplicate charge refund invoice payment evidence",
                "keywords": ["duplicate charge", "refund", "invoice", "payment evidence"],
                "reason": "Customer language maps to duplicate payment refund policy.",
            }
        )
    )
    llm_rewrite = CRAGKeywordRewriter(llm_client=fake).rewrite("They took my money twice")
    if llm_rewrite.rewritten_query != "duplicate charge refund invoice payment evidence":
        raise AssertionError(f"LLM rewrite wrong: {llm_rewrite.to_dict()}")
    if "Original query: They took my money twice" not in fake.prompts[0]:
        raise AssertionError("original query was not included in rewrite prompt")


def assert_rule_based_relevance_scores_policy_chunks() -> None:
    relevant = evaluate_policy_relevance(
        "Can I get a service credit for a verified outage?",
        "The service credit policy allows credit when the outage is verified and lasted at least 6 hours.",
    )
    if relevant.relevance != "relevant" or relevant.route != "correct" or not relevant.is_relevant:
        raise AssertionError(f"expected relevant CRAG score: {relevant.to_dict()}")

    irrelevant = evaluate_policy_relevance(
        "Can I get a service credit for a verified outage?",
        "Technician appointments can be rescheduled after router diagnostic checks.",
    )
    if irrelevant.relevance != "irrelevant" or irrelevant.route != "incorrect" or irrelevant.is_relevant:
        raise AssertionError(f"expected irrelevant CRAG score: {irrelevant.to_dict()}")


def assert_decomposes_policy_to_strips() -> None:
    document = """# Service Credit Policy

## Eligibility

Verified outage customers may receive service credit after 6 hours.

## Escalation

Escalate unverified outage requests or requests above INR 500.
"""
    strips = decompose_policy_to_strips(document, source_id="service_credit_policy", max_strip_tokens=12)
    if len(strips) < 2:
        raise AssertionError(f"expected multiple strips: {[strip.to_dict() for strip in strips]}")
    if not all(strip.source_id == "service_credit_policy" for strip in strips):
        raise AssertionError("source_id was not preserved")
    if not all(strip.token_count <= 12 for strip in strips):
        raise AssertionError(f"strip token limit failed: {[strip.to_dict() for strip in strips]}")
    if strips[0].strip_id != "service_credit_policy#strip-0":
        raise AssertionError(f"unexpected strip id: {strips[0].strip_id}")


def assert_crag_correct_path_refines_and_rescores_strips() -> None:
    document = """# Service Credit Policy

## Eligibility

Verified outage customers may receive service credit after 6 hours.

## Technician Visits

Technician visits require router diagnostics before dispatch.

## Escalation

Escalate unverified outage requests or requests above INR 500.
"""

    class StripScoringLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def __call__(self, prompt: str) -> str:
            self.prompts.append(prompt)
            if "Verified outage customers may receive service credit" in prompt:
                return json.dumps(
                    {
                        "relevance": "relevant",
                        "score": 0.88,
                        "rationale": "Eligibility strip directly answers service credit for outage.",
                        "evidence_terms": ["service credit", "6 hours"],
                    }
                )
            if "Escalate unverified outage requests" in prompt:
                return json.dumps(
                    {
                        "relevance": "relevant",
                        "score": 0.7,
                        "rationale": "Escalation strip is related to outage credit boundaries.",
                        "evidence_terms": ["unverified outage", "INR 500"],
                    }
                )
            return json.dumps(
                {
                    "relevance": "irrelevant",
                    "score": 0.08,
                    "rationale": "Technician strip is unrelated.",
                    "evidence_terms": [],
                }
            )

    llm = StripScoringLLM()
    result = crag_correct_path(
        "Can I get a service credit for an outage?",
        document,
        source_id="service_credit_policy",
        llm_client=llm,
    )
    if result.route != "correct":
        raise AssertionError(f"wrong route: {result.to_dict()}")
    if result.strips_considered < 3:
        raise AssertionError(f"expected at least 3 strips considered: {result.to_dict()}")
    if [strip.evaluation.score for strip in result.refined_strips] != [0.88, 0.7]:
        raise AssertionError(f"strips were not filtered and sorted by score: {result.to_dict()}")
    if any("Technician visits" in strip.strip.text for strip in result.refined_strips):
        raise AssertionError(f"irrelevant strip leaked into refined evidence: {result.to_dict()}")
    if len(llm.prompts) != result.strips_considered:
        raise AssertionError("each strip should be re-scored")


def assert_crag_strip_scoring_is_parallel_and_fault_tolerant() -> None:
    document = """
## Credit Rule A
Verified outage service credit evidence includes invoice impact and affected location.

## Credit Rule B
Duplicate charge refund evidence includes a duplicate payment transaction and invoice ID.

## Credit Rule C
bad-json-strip should not abort the entire CRAG path when the judge response is malformed.

## Credit Rule D
Prior credit checks prevent a second automatic credit in the same billing period.

## Credit Rule E
Cancellation requests stay queued until billing and outage issues are acknowledged.

## Credit Rule F
Router diagnostics are required before technician dispatch for connectivity issues.
"""

    class SlowFaultyStripLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def __call__(self, prompt: str) -> str:
            with self.lock:
                self.prompts.append(prompt)
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.05)
                if "bad-json-strip" in prompt:
                    return "not json"
                return json.dumps(
                    {
                        "relevance": "relevant",
                        "score": 0.71,
                        "rationale": "Strip is related to the customer service policy route.",
                        "evidence_terms": ["service", "policy"],
                    }
                )
            finally:
                with self.lock:
                    self.active -= 1

    llm = SlowFaultyStripLLM()
    result = crag_correct_path(
        "outage service credit duplicate charge cancellation",
        document,
        source_id="resilience_policy",
        llm_client=llm,
        max_strip_tokens=30,
    )

    if result.strips_considered < 6:
        raise AssertionError(f"expected multiple strips: {result.to_dict()}")
    if len(llm.prompts) != result.strips_considered:
        raise AssertionError("all strips should be attempted even when one fails")
    if llm.max_active < 2:
        raise AssertionError("strip relevance calls did not run concurrently")
    if any("bad-json-strip" in item.strip.text for item in result.refined_strips):
        raise AssertionError(f"malformed strip response should not be refined: {result.to_dict()}")
    if len(result.refined_strips) != result.strips_considered - 1:
        raise AssertionError(f"only the malformed strip should be dropped: {result.to_dict()}")


def assert_crag_incorrect_path_rewrites_retries_and_rescores() -> None:
    class RewriteAndScoreLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def __call__(self, prompt: str) -> str:
            self.prompts.append(prompt)
            if "CRAG query rewriter" in prompt:
                return json.dumps(
                    {
                        "rewritten_query": "duplicate charge refund invoice payment evidence",
                        "keywords": ["duplicate charge", "refund", "invoice", "payment evidence"],
                        "reason": "Weak retrieval needs billing-policy terms.",
                    }
                )
            if "Duplicate charge refunds require" in prompt:
                return json.dumps(
                    {
                        "relevance": "ambiguous",
                        "score": 0.55,
                        "rationale": "Strip contains refund evidence requirements.",
                        "evidence_terms": ["invoice ID", "payment records", "duplicate payment evidence"],
                    }
                )
            return json.dumps(
                {
                    "relevance": "irrelevant",
                    "score": 0.05,
                    "rationale": "Router diagnostics are unrelated.",
                    "evidence_terms": [],
                }
            )

    store = FakePolicyStore()
    llm = RewriteAndScoreLLM()
    result = crag_incorrect_path(
        "They took my money twice",
        store,
        llm_client=llm,
        top_k=2,
    )

    if result.route != "incorrect":
        raise AssertionError(f"wrong route: {result.to_dict()}")
    if result.rewritten_query != "duplicate charge refund invoice payment evidence":
        raise AssertionError(f"wrong rewritten query: {result.to_dict()}")
    if store.queries != [{"query_text": "duplicate charge refund invoice payment evidence", "top_k": 2}]:
        raise AssertionError(f"policy store was not retried with rewritten query: {store.queries}")
    if result.candidates_considered != 2 or result.strips_considered < 2:
        raise AssertionError(f"candidate/strip counts wrong: {result.to_dict()}")
    if len(result.refined_strips) != 1:
        raise AssertionError(f"only the refund strip should survive lower-threshold filtering: {result.to_dict()}")
    if result.refined_strips[0].evaluation.score != 0.55:
        raise AssertionError(f"wrong refined strip score: {result.to_dict()}")
    if "refund_policy" not in result.refined_strips[0].strip.source_id:
        raise AssertionError(f"policy source metadata should be preserved: {result.to_dict()}")


def assert_crag_ambiguous_path_combines_internal_and_external_strips() -> None:
    document = """# Service Credit Policy

## Partial Eligibility

Verified outage customers may receive service credit, but manual review is required when monitoring evidence is incomplete.

## Technician Visits

Technician visits require router diagnostics before dispatch.
"""

    def fake_external_provider(query: str) -> list[dict]:
        if "unverified outage" not in query:
            raise AssertionError(f"query was not passed to external provider: {query}")
        return [
            {
                "source_id": "external:consumer_guidance",
                "text": "Consumer guidance says customers should keep outage timestamps and billing records when requesting compensation review.",
            },
            {
                "source_id": "external:unrelated",
                "text": "Streaming quality depends on device hardware and home Wi-Fi placement.",
            },
        ]

    class AmbiguousScoreLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def __call__(self, prompt: str) -> str:
            self.prompts.append(prompt)
            if "Verified outage customers may receive service credit" in prompt:
                return json.dumps(
                    {
                        "relevance": "ambiguous",
                        "score": 0.45,
                        "rationale": "Internal policy is related but needs more support.",
                        "evidence_terms": ["manual review", "monitoring evidence"],
                    }
                )
            if "customers should keep outage timestamps" in prompt:
                return json.dumps(
                    {
                        "relevance": "relevant",
                        "score": 0.72,
                        "rationale": "External guidance adds compensation evidence requirements.",
                        "evidence_terms": ["outage timestamps", "billing records"],
                    }
                )
            return json.dumps(
                {
                    "relevance": "irrelevant",
                    "score": 0.05,
                    "rationale": "Unrelated support content.",
                    "evidence_terms": [],
                }
            )

    llm = AmbiguousScoreLLM()
    result = crag_ambiguous_path(
        "Can I get credit for an unverified outage?",
        document,
        source_id="service_credit_policy",
        external_strip_provider=fake_external_provider,
        llm_client=llm,
    )

    if result.route != "ambiguous":
        raise AssertionError(f"wrong route: {result.to_dict()}")
    if result.external_provider != "fake_external_provider":
        raise AssertionError(f"provider name was not recorded: {result.to_dict()}")
    if result.internal_strips_considered < 2 or result.external_strips_considered != 2:
        raise AssertionError(f"strip counts wrong: {result.to_dict()}")
    if [strip.evaluation.score for strip in result.combined_strips] != [0.72, 0.45]:
        raise AssertionError(f"internal and external strips were not combined/sorted: {result.to_dict()}")
    if result.combined_strips[0].strip.source_id != "external:consumer_guidance":
        raise AssertionError(f"external strip source should be preserved: {result.to_dict()}")
    if result.combined_strips[1].strip.source_id != "service_credit_policy":
        raise AssertionError(f"internal strip source should be preserved: {result.to_dict()}")
    if len(llm.prompts) != result.internal_strips_considered + result.external_strips_considered:
        raise AssertionError("each internal and external strip should be re-scored")

    default_result = crag_ambiguous_path(
        "Can I get credit for an unverified outage?",
        document,
        source_id="service_credit_policy",
        llm_client=AmbiguousScoreLLM(),
    )
    if default_result.external_provider != "mock_external_policy_strips":
        raise AssertionError(f"default provider should be the no-network mock: {default_result.to_dict()}")
    if default_result.external_strips_considered != 0:
        raise AssertionError(f"mock external provider should not create strips: {default_result.to_dict()}")
    if mock_external_policy_strips("valid query") != []:
        raise AssertionError("mock external provider should be an empty no-network provider")


def assert_scores_answer_support_and_usefulness() -> None:
    evidence = [
        PolicyStrip(
            strip_id="service_credit_policy#strip-0",
            text="Verified outage customers may receive service credit after outage credit eligibility requirements are met.",
            strip_index=0,
            source_id="service_credit_policy",
            token_count=12,
        )
    ]
    score = score_answer_support_usefulness(
        query="Can I get outage credit?",
        answer="You can receive outage credit when outage credit eligibility requirements are met.",
        evidence_strips=evidence,
    )
    if not score.is_sup or not score.is_use:
        raise AssertionError(f"supported useful answer should pass: {score.to_dict()}")
    if not answer_passes_evidence_gate(score):
        raise AssertionError(f"evidence gate should pass: {score.to_dict()}")
    if score.cited_strip_ids != ["service_credit_policy#strip-0"]:
        raise AssertionError(f"citation ids wrong: {score.to_dict()}")

    unsupported = score_answer_support_usefulness(
        query="Can I get outage credit?",
        answer="You will receive a free router upgrade tomorrow.",
        evidence_strips=evidence,
    )
    if unsupported.is_sup or answer_passes_evidence_gate(unsupported):
        raise AssertionError(f"unsupported answer should fail: {unsupported.to_dict()}")

    fake = FakeLLM(
        json.dumps(
            {
                "is_sup": True,
                "is_use": True,
                "support_score": 0.91,
                "usefulness_score": 0.88,
                "cited_strip_ids": ["service_credit_policy#strip-0", "unknown"],
                "missing_claims": [],
                "rationale": "Answer is grounded and directly useful.",
            }
        )
    )
    llm_score = score_answer_support_usefulness(
        query="Can I get outage credit?",
        answer="You may receive service credit when the outage is verified.",
        evidence_strips=evidence,
        llm_client=fake,
    )
    if llm_score.cited_strip_ids != ["service_credit_policy#strip-0"] or not llm_score.is_sup:
        raise AssertionError(f"LLM answer score should filter citations and pass: {llm_score.to_dict()}")
    if "[IsSup]" not in fake.prompts[0] or "[IsUse]" not in fake.prompts[0]:
        raise AssertionError("LLM prompt should include reflection labels")


def assert_llm_decision_parses_structured_json() -> None:
    fake = FakeLLM(
        json.dumps(
            {
                "retrieve": "yes",
                "confidence": 1.4,
                "reason": "Refund eligibility requires policy evidence.",
            }
        )
    )
    decider = SelfRAGRetrieveDecider(llm_client=fake)
    decision = decider.decide("Am I eligible for a refund?")

    if decision.to_dict() != {
        "token": "yes",
        "label": "[Retrieve]",
        "should_retrieve": True,
        "confidence": 1.0,
        "reason": "Refund eligibility requires policy evidence.",
    }:
        raise AssertionError(f"unexpected LLM decision: {decision.to_dict()}")
    if "Query: Am I eligible for a refund?" not in fake.prompts[0]:
        raise AssertionError("query was not included in the LLM prompt")

    json_output = decider.decide_json("Am I eligible for a refund?")
    if json.loads(json_output)["label"] != "[Retrieve]":
        raise AssertionError(f"decide_json output is wrong: {json_output}")


def assert_llm_relevance_parses_and_routes_scores() -> None:
    fake = FakeLLM(
        json.dumps(
            {
                "relevance": "relevant",
                "score": 0.74,
                "rationale": "The policy directly covers duplicate payment refund handling.",
                "evidence_terms": ["duplicate payment", "refund"],
            }
        )
    )
    evaluator = CRAGRelevanceEvaluator(llm_client=fake)
    evaluation = evaluator.evaluate(
        "Can I get a refund for duplicate payment?",
        "Duplicate payment refund handling requires invoice and payment evidence.",
    )

    if evaluation.to_dict() != {
        "relevance": "relevant",
        "score": 0.74,
        "is_relevant": True,
        "route": "correct",
        "rationale": "The policy directly covers duplicate payment refund handling.",
        "evidence_terms": ["duplicate payment", "refund"],
    }:
        raise AssertionError(f"unexpected CRAG evaluation: {evaluation.to_dict()}")
    if "Policy document:" not in fake.prompts[0]:
        raise AssertionError("document was not included in CRAG prompt")

    ambiguous = CRAGRelevanceEvaluator(
        llm_client=FakeLLM(json.dumps({"relevance": "relevant", "score": 0.4, "rationale": "partial", "evidence_terms": []}))
    ).evaluate("query", "document")
    if ambiguous.relevance != "ambiguous" or ambiguous.route != "ambiguous":
        raise AssertionError(f"score thresholds should override mismatched label: {ambiguous.to_dict()}")

    irrelevant = CRAGRelevanceEvaluator(
        llm_client=FakeLLM(json.dumps({"relevance": "unknown", "score": 0.1, "rationale": "weak", "evidence_terms": []}))
    ).evaluate("query", "document")
    if irrelevant.relevance != "irrelevant" or irrelevant.route != "incorrect":
        raise AssertionError(f"unknown relevance should be normalized from score: {irrelevant.to_dict()}")


def assert_rejects_bad_inputs_and_unknown_tokens() -> None:
    try:
        decide_policy_retrieval("")
    except ValueError:
        pass
    else:
        raise AssertionError("empty query was accepted")

    try:
        SelfRAGRetrieveDecider(FakeLLM("not json")).decide("valid query")
    except ValueError as exc:
        if "not valid JSON" not in str(exc):
            raise AssertionError(f"wrong bad-json error: {exc}")
    else:
        raise AssertionError("bad JSON was accepted")

    try:
        SelfRAGRetrieveDecider(FakeLLM(json.dumps({"retrieve": "maybe"}))).decide("valid query")
    except ValueError as exc:
        if "unknown retrieve token" not in str(exc):
            raise AssertionError(f"wrong unknown-token error: {exc}")
    else:
        raise AssertionError("unknown retrieve token was accepted")

    try:
        evaluate_policy_relevance("", "document")
    except ValueError:
        pass
    else:
        raise AssertionError("empty relevance query was accepted")

    try:
        evaluate_policy_relevance("query", "")
    except ValueError:
        pass
    else:
        raise AssertionError("empty relevance document was accepted")

    try:
        CRAGRelevanceEvaluator(FakeLLM("not json")).evaluate("query", "document")
    except ValueError as exc:
        if "CRAG relevance LLM output was not valid JSON" not in str(exc):
            raise AssertionError(f"wrong CRAG bad-json error: {exc}")
    else:
        raise AssertionError("bad CRAG JSON was accepted")

    try:
        CRAGKeywordRewriter(FakeLLM("not json")).rewrite("query")
    except ValueError as exc:
        if "CRAG keyword rewrite LLM output was not valid JSON" not in str(exc):
            raise AssertionError(f"wrong keyword-rewrite bad-json error: {exc}")
    else:
        raise AssertionError("bad keyword rewrite JSON was accepted")

    for kwargs in (
        {"query": "", "document": "doc"},
        {"query": "query", "document": ""},
        {"query": "query", "document": "doc", "threshold": 1.5},
    ):
        try:
            crag_correct_path(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad CRAG correct-path inputs were accepted: {kwargs}")

    for kwargs in (
        {"query": "", "policy_store": FakePolicyStore()},
        {"query": "query", "policy_store": object()},
        {"query": "query", "policy_store": FakePolicyStore(), "top_k": 0},
        {"query": "query", "policy_store": FakePolicyStore(), "threshold": 1.5},
    ):
        try:
            crag_incorrect_path(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad CRAG incorrect-path inputs were accepted: {kwargs}")

    for kwargs in (
        {"query": "", "document": "doc"},
        {"query": "query", "document": ""},
        {"query": "query", "document": "doc", "threshold": 1.5},
        {"query": "query", "document": "doc", "external_strip_provider": []},
    ):
        try:
            crag_ambiguous_path(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad CRAG ambiguous-path inputs were accepted: {kwargs}")

    try:
        crag_ambiguous_path(
            "query",
            "document",
            external_strip_provider=lambda query: "not a list",
            llm_client=FakeLLM(json.dumps({"relevance": "relevant", "score": 0.9, "rationale": "x", "evidence_terms": []})),
        )
    except ValueError as exc:
        if "must return a list" not in str(exc):
            raise AssertionError(f"wrong bad-provider-result error: {exc}")
    else:
        raise AssertionError("bad external provider result was accepted")

    try:
        decompose_policy_to_strips("", source_id="policy")
    except ValueError:
        pass
    else:
        raise AssertionError("empty strip document was accepted")

    for kwargs in (
        {"query": "", "answer": "answer", "evidence_strips": []},
        {"query": "query", "answer": "", "evidence_strips": []},
    ):
        try:
            score_answer_support_usefulness(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad answer scoring inputs were accepted: {kwargs}")


def main() -> None:
    assert_prompt_uses_self_rag_tokens()
    assert_crag_prompt_uses_llm_judge_schema()
    assert_crag_keyword_rewrite_prompt_is_structured()
    assert_answer_support_prompt_is_structured()
    assert_rule_based_decisions_are_conservative()
    assert_keyword_rewrite_expands_customer_language()
    assert_rule_based_relevance_scores_policy_chunks()
    assert_decomposes_policy_to_strips()
    assert_crag_correct_path_refines_and_rescores_strips()
    assert_crag_strip_scoring_is_parallel_and_fault_tolerant()
    assert_crag_incorrect_path_rewrites_retries_and_rescores()
    assert_crag_ambiguous_path_combines_internal_and_external_strips()
    assert_scores_answer_support_and_usefulness()
    assert_llm_decision_parses_structured_json()
    assert_llm_relevance_parses_and_routes_scores()
    assert_rejects_bad_inputs_and_unknown_tokens()
    print("policy retrieval decision tests passed")


if __name__ == "__main__":
    main()
