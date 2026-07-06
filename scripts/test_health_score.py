from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import (  # noqa: E402
    CASAEmpathySequence,
    CASAEmpathyStep,
    HealthScore,
    IntentClassifier,
    KnowledgeCoverageComponent,
    LoopPenaltyComponent,
    MissingInfoRiskComponent,
    RecommendedAction,
    RelationshipScore,
    SessionStartBehavior,
    SentimentScoreComponent,
    build_sentiment_prompt,
    casa_empathy_sequence,
    compute_health_score,
    compute_relationship_score,
    get_recommended_action,
    intent_confidence_component,
    knowledge_coverage_component,
    loop_penalty_component,
    missing_info_risk_component,
    session_start_behavior,
    sentiment_score_component,
)


def assert_intent_confidence_component_still_uses_softmax() -> None:
    classification = IntentClassifier().classify("I was charged twice and the internet is down.")
    component = intent_confidence_component(classification)

    if component.value != classification.intent_confidence:
        raise AssertionError(f"intent confidence drifted: {component.to_dict()} {classification.to_dict()}")
    if component.source != "classifier_softmax":
        raise AssertionError(f"intent confidence source wrong: {component.to_dict()}")


def assert_missing_info_risk_uses_slot_schema_weights() -> None:
    risk = missing_info_risk_component(
        ["duplicate_charge", "service_outage"],
        {"customer_id": "CUST-1001", "location": "Chennai Zone-04"},
    )

    if not isinstance(risk, MissingInfoRiskComponent):
        raise AssertionError(f"wrong risk type: {risk}")
    if risk.to_dict() != {
        "value": 0.17,
        "intents": ["duplicate_charge", "service_outage"],
        "total_required_weight": 3.0,
        "missing_required_weight": 0.5,
        "missing_slots": [
            {
                "intent": "duplicate_charge",
                "slot": "invoice_id",
                "value_type": "string",
                "priority": 2,
                "prompt": "Which invoice shows the duplicate charge?",
                "aliases": ["bill_id"],
                "weight": 0.5,
            }
        ],
        "source": "slot_schema",
    }:
        raise AssertionError(f"missing-info risk payload wrong: {risk.to_dict()}")


def assert_missing_info_risk_is_zero_when_complete() -> None:
    risk = missing_info_risk_component(
        ["duplicate_charge", "service_outage"],
        {
            "customer_id": "CUST-1001",
            "invoice_id": "INV-8821",
            "location": "Chennai Zone-04",
        },
    )

    if risk.value != 0.0 or risk.missing_slots:
        raise AssertionError(f"complete slots should have zero risk: {risk.to_dict()}")


def assert_missing_info_risk_prioritizes_customer_id() -> None:
    no_customer = missing_info_risk_component(["refund_request"], {"amount": 300, "reason": "duplicate payment"})
    no_reason = missing_info_risk_component(["refund_request"], {"customer_id": "CUST-1001", "amount": 300})

    if no_customer.value <= no_reason.value:
        raise AssertionError(
            f"missing customer_id should be riskier than missing reason: {no_customer.to_dict()} {no_reason.to_dict()}"
        )
    if no_customer.missing_slots[0]["slot"] != "customer_id":
        raise AssertionError(f"customer_id should be the missing high-priority slot: {no_customer.to_dict()}")


def assert_missing_info_risk_dedupes_intents_and_defaults_general() -> None:
    deduped = missing_info_risk_component(["service_outage", "service_outage"], {"customer_id": "CUST-1001"})
    if deduped.intents != ["service_outage"]:
        raise AssertionError(f"intents should dedupe in order: {deduped.to_dict()}")

    defaulted = missing_info_risk_component([], {})
    if defaulted.intents != ["general_query"] or defaulted.value != 1.0:
        raise AssertionError(f"empty intents should default to general_query risk: {defaulted.to_dict()}")


def assert_missing_info_risk_validates_inputs() -> None:
    bad_calls = (
        lambda: missing_info_risk_component("duplicate_charge", {}),
        lambda: missing_info_risk_component(["duplicate_charge"], []),
        lambda: missing_info_risk_component(["missing_intent"], {}),
        lambda: missing_info_risk_component([123], {}),
    )
    for bad_call in bad_calls:
        try:
            bad_call()
        except ValueError:
            pass
        else:
            raise AssertionError("bad missing_info_risk_component input was accepted")


def assert_sentiment_score_uses_last_three_messages_with_rules() -> None:
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "How can I help?"},
        {"role": "user", "content": "This is still not fixed and I am frustrated."},
        {"role": "assistant", "content": "I will check it."},
    ]
    sentiment = sentiment_score_component(messages)

    if not isinstance(sentiment, SentimentScoreComponent):
        raise AssertionError(f"wrong sentiment type: {sentiment}")
    if sentiment.label != "frustrated" or sentiment.value != 0.25:
        raise AssertionError(f"frustrated sentiment expected: {sentiment.to_dict()}")
    if len(sentiment.messages_analyzed) != 3:
        raise AssertionError(f"should only analyze last three messages: {sentiment.to_dict()}")
    if sentiment.messages_analyzed[0]["content"] != "How can I help?":
        raise AssertionError(f"oldest message should be dropped: {sentiment.to_dict()}")
    if sentiment.source != "rule_sentiment_last_3_messages":
        raise AssertionError(f"rule source wrong: {sentiment.to_dict()}")


def assert_sentiment_score_uses_llm_json_output() -> None:
    captured_prompts = []

    def fake_llm(prompt: str) -> str:
        captured_prompts.append(prompt)
        return """```json
{"label":"angry","score":0.08,"rationale":"Customer says the service is terrible."}
```"""

    messages = [
        {"role": "user", "content": "First message should be ignored."},
        {"role": "assistant", "content": "Please try again."},
        {"role": "user", "content": "This is terrible."},
        {"role": "assistant", "content": "I understand."},
    ]
    sentiment = sentiment_score_component(messages, llm_client=fake_llm)

    if sentiment.to_dict() != {
        "value": 0.08,
        "label": "angry",
        "messages_analyzed": [
            {"role": "assistant", "content": "Please try again."},
            {"role": "user", "content": "This is terrible."},
            {"role": "assistant", "content": "I understand."},
        ],
        "rationale": "Customer says the service is terrible.",
        "source": "llm_sentiment_last_3_messages",
    }:
        raise AssertionError(f"LLM sentiment payload wrong: {sentiment.to_dict()}")
    if len(captured_prompts) != 1 or "First message should be ignored" in captured_prompts[0]:
        raise AssertionError(f"prompt should contain only last three messages: {captured_prompts}")


def assert_sentiment_prompt_and_validation() -> None:
    prompt = build_sentiment_prompt([{"role": "user", "content": "Please help"}])
    if "strict JSON" not in prompt or "score" not in prompt:
        raise AssertionError(f"sentiment prompt missing contract: {prompt}")

    bad_calls = (
        lambda: sentiment_score_component("bad"),
        lambda: sentiment_score_component([{"role": "", "content": "hello"}]),
        lambda: sentiment_score_component(["hello"]),
        lambda: sentiment_score_component([{"role": "user", "content": "hello"}], llm_client=lambda _: "not json"),
    )
    for bad_call in bad_calls:
        try:
            bad_call()
        except ValueError:
            pass
        else:
            raise AssertionError("bad sentiment_score_component input was accepted")


def assert_loop_penalty_detects_two_repeated_questions() -> None:
    penalty = loop_penalty_component(
        [
            {"role": "user", "content": "Why is my internet down?"},
            {"role": "assistant", "content": "I am checking."},
            {"role": "user", "content": "Why is my internet down"},
        ]
    )

    if not isinstance(penalty, LoopPenaltyComponent):
        raise AssertionError(f"wrong loop penalty type: {penalty}")
    if penalty.to_dict() != {
        "value": 0.5,
        "repeated_question_count": 2,
        "repeated_question": "why is my internet down",
        "question_counts": {"why is my internet down": 2},
        "source": "repeated_question_threshold",
    }:
        raise AssertionError(f"2x loop penalty wrong: {penalty.to_dict()}")


def assert_loop_penalty_detects_three_repeated_questions() -> None:
    penalty = loop_penalty_component(
        [
            {"role": "user", "content": "Can you fix this?"},
            {"role": "assistant", "content": "Trying now."},
            {"role": "user", "content": "Can you fix this"},
            {"role": "assistant", "content": "Still checking."},
            {"role": "user", "content": "can you fix this???"},
        ]
    )

    if penalty.value != 1.0 or penalty.repeated_question_count != 3:
        raise AssertionError(f"3x loop penalty wrong: {penalty.to_dict()}")


def assert_loop_penalty_ignores_non_questions_and_assistant_repetition() -> None:
    penalty = loop_penalty_component(
        [
            {"role": "assistant", "content": "Why is my internet down?"},
            {"role": "assistant", "content": "Why is my internet down?"},
            {"role": "user", "content": "Internet is down"},
            {"role": "user", "content": "Router light is red"},
        ]
    )

    if penalty.value != 0.0 or penalty.repeated_question is not None or penalty.question_counts:
        raise AssertionError(f"non-question loop penalty should be zero: {penalty.to_dict()}")


def assert_loop_penalty_validates_inputs() -> None:
    bad_calls = (
        lambda: loop_penalty_component("bad"),
        lambda: loop_penalty_component(["hello"]),
        lambda: loop_penalty_component([{"role": "", "content": "Why?"}]),
    )
    for bad_call in bad_calls:
        try:
            bad_call()
        except ValueError:
            pass
        else:
            raise AssertionError("bad loop_penalty_component input was accepted")


def assert_loop_penalty_ignores_blank_transcript_messages() -> None:
    penalty = loop_penalty_component(
        [
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": "\n\t"},
            {"role": "user", "content": "Why is my internet still down?"},
        ]
    )
    if penalty.repeated_question_count != 0 or penalty.value != 0.0:
        raise AssertionError(f"blank transcript messages should be ignored: {penalty.to_dict()}")

    sentiment = sentiment_score_component([{"role": "user", "content": "   "}])
    if sentiment.label != "neutral":
        raise AssertionError(f"all-blank transcript should produce neutral sentiment: {sentiment.to_dict()}")


def assert_knowledge_coverage_combines_tool_calls_and_crag_confidence() -> None:
    coverage = knowledge_coverage_component(
        [
            {"tool_name": "lookup_customer", "ok": True},
            {"tool_name": "check_outage_status", "status": "ok"},
            {"tool_name": "retrieve_policy", "status": "failed"},
        ],
        [
            {"score": 0.8},
            {"evaluation": {"score": 0.6}},
        ],
        required_tools=["lookup_customer", "check_outage_status", "retrieve_policy"],
    )

    if not isinstance(coverage, KnowledgeCoverageComponent):
        raise AssertionError(f"wrong knowledge coverage type: {coverage}")
    if coverage.to_dict() != {
        "value": 0.69,
        "tool_coverage": 0.67,
        "crag_confidence": 0.7,
        "tools_called": [
            {"name": "lookup_customer", "successful": True},
            {"name": "check_outage_status", "successful": True},
            {"name": "retrieve_policy", "successful": False},
        ],
        "required_tools": ["lookup_customer", "check_outage_status", "retrieve_policy"],
        "crag_scores": [0.8, 0.6],
        "source": "tool_calls_crag_confidence",
    }:
        raise AssertionError(f"knowledge coverage payload wrong: {coverage.to_dict()}")


def assert_knowledge_coverage_handles_open_tool_lists() -> None:
    coverage = knowledge_coverage_component(
        ["lookup_customer", {"name": "retrieve_policy", "result": {"policy_id": "service_credit_policy"}}],
        [{"relevance": {"score": 1.4}}],
    )

    if coverage.tool_coverage != 1.0 or coverage.crag_confidence != 1.0 or coverage.value != 1.0:
        raise AssertionError(f"open successful tool coverage should be full: {coverage.to_dict()}")

    empty = knowledge_coverage_component([], [])
    if empty.value != 0.0 or empty.tool_coverage != 0.0 or empty.crag_confidence != 0.0:
        raise AssertionError(f"empty evidence should have zero coverage: {empty.to_dict()}")


def assert_knowledge_coverage_treats_timeout_as_failed_tool() -> None:
    coverage = knowledge_coverage_component(
        [{"tool_name": "retrieve_policy", "status": "timeout"}],
        [],
        required_tools=["retrieve_policy"],
    )
    if coverage.tools_called != [{"name": "retrieve_policy", "successful": False}]:
        raise AssertionError(f"timeout should be a failed tool call: {coverage.to_dict()}")
    if coverage.tool_coverage != 0.0:
        raise AssertionError(f"timeout should not cover required tool: {coverage.to_dict()}")


def assert_knowledge_coverage_validates_inputs() -> None:
    bad_calls = (
        lambda: knowledge_coverage_component("lookup_customer", []),
        lambda: knowledge_coverage_component([{}], []),
        lambda: knowledge_coverage_component(["lookup_customer"], "bad"),
        lambda: knowledge_coverage_component(["lookup_customer"], [123]),
        lambda: knowledge_coverage_component(["lookup_customer"], [], required_tools="lookup_customer"),
        lambda: knowledge_coverage_component(["lookup_customer"], [], required_tools=[123]),
    )
    for bad_call in bad_calls:
        try:
            bad_call()
        except ValueError:
            pass
        else:
            raise AssertionError("bad knowledge_coverage_component input was accepted")


def assert_compute_health_score_applies_weighted_formula() -> None:
    health = compute_health_score(
        intent_confidence=0.8,
        missing_info_risk=0.25,
        sentiment_score=0.5,
        loop_penalty=0.5,
        knowledge_coverage=0.7,
    )

    if not isinstance(health, HealthScore):
        raise AssertionError(f"wrong health score type: {health}")
    if health.to_dict() != {
        "score": 67.25,
        "components": {
            "intent_confidence": 0.8,
            "missing_info_risk": 0.25,
            "sentiment_score": 0.5,
            "loop_penalty": 0.5,
            "knowledge_coverage": 0.7,
        },
        "contributions": {
            "intent_confidence": 0.24,
            "missing_info_completeness": 0.1875,
            "sentiment_score": 0.1,
            "loop_stability": 0.075,
            "knowledge_coverage": 0.07,
        },
        "formula": "H = 0.30*ic + 0.25*(1-mr) + 0.20*ss + 0.15*(1-lp) + 0.10*kc",
    }:
        raise AssertionError(f"health formula payload wrong: {health.to_dict()}")


def assert_compute_health_score_accepts_component_objects() -> None:
    classification = IntentClassifier().classify("I was charged twice and my internet is down.")
    intent_component = intent_confidence_component(classification)
    missing_component = missing_info_risk_component(
        classification.intents,
        {"customer_id": "CUST-1001", "invoice_id": "INV-8821", "location": "Chennai Zone-04"},
    )
    sentiment_component = sentiment_score_component([{"role": "user", "content": "Please help"}])
    loop_component = loop_penalty_component([{"role": "user", "content": "Can you help?"}])
    knowledge_component = knowledge_coverage_component(
        ["lookup_customer", "check_outage_status"],
        [{"score": 0.8}],
        required_tools=["lookup_customer", "check_outage_status"],
    )
    health = compute_health_score(
        intent_confidence=intent_component,
        missing_info_risk=missing_component,
        sentiment_score=sentiment_component,
        loop_penalty=loop_component,
        knowledge_coverage=knowledge_component,
    )

    if health.score <= 0:
        raise AssertionError(f"component-object health score should be positive: {health.to_dict()}")
    if health.components["missing_info_risk"] != missing_component.value:
        raise AssertionError(f"component values should be extracted: {health.to_dict()}")


def assert_compute_health_score_validates_component_ranges() -> None:
    bad_calls = (
        lambda: compute_health_score(
            intent_confidence=1.1,
            missing_info_risk=0,
            sentiment_score=0,
            loop_penalty=0,
            knowledge_coverage=0,
        ),
        lambda: compute_health_score(
            intent_confidence=0,
            missing_info_risk=-0.1,
            sentiment_score=0,
            loop_penalty=0,
            knowledge_coverage=0,
        ),
        lambda: compute_health_score(
            intent_confidence="bad",
            missing_info_risk=0,
            sentiment_score=0,
            loop_penalty=0,
            knowledge_coverage=0,
        ),
    )
    for bad_call in bad_calls:
        try:
            bad_call()
        except ValueError:
            pass
        else:
            raise AssertionError("bad compute_health_score input was accepted")


def assert_recommended_action_routes_thresholds() -> None:
    cases = [
        (70, "CONTINUE", ">=70"),
        (100, "CONTINUE", ">=70"),
        (69.99, "CAUTION", "50-70"),
        (50, "CAUTION", "50-70"),
        (49.99, "REPAIR", "30-50"),
        (30, "REPAIR", "30-50"),
        (29.99, "HANDOFF", "<30"),
        (0, "HANDOFF", "<30"),
    ]

    for score, expected_action, expected_band in cases:
        recommendation = get_recommended_action(score)
        if not isinstance(recommendation, RecommendedAction):
            raise AssertionError(f"wrong recommendation type: {recommendation}")
        if recommendation.action != expected_action or recommendation.threshold_band != expected_band:
            raise AssertionError(
                f"score {score} routed wrong: {recommendation.to_dict()}"
            )
        if recommendation.health_score != round(float(score), 2):
            raise AssertionError(f"score should be preserved and rounded: {recommendation.to_dict()}")


def assert_recommended_action_accepts_health_score_object() -> None:
    health = compute_health_score(
        intent_confidence=0.8,
        missing_info_risk=0.25,
        sentiment_score=0.5,
        loop_penalty=0.5,
        knowledge_coverage=0.7,
    )
    recommendation = get_recommended_action(health)

    if recommendation.to_dict() != {
        "action": "CAUTION",
        "label": "proceed with caution",
        "health_score": 67.25,
        "threshold_band": "50-70",
        "reason": "Conversation health is moderate; continue, but prefer low-risk actions and tighter checks.",
        "source": "health_score_thresholds",
    }:
        raise AssertionError(f"health-object recommendation wrong: {recommendation.to_dict()}")


def assert_recommended_action_validates_inputs() -> None:
    bad_calls = (
        lambda: get_recommended_action(-1),
        lambda: get_recommended_action(101),
        lambda: get_recommended_action("bad"),
        lambda: get_recommended_action(object()),
    )
    for bad_call in bad_calls:
        try:
            bad_call()
        except ValueError:
            pass
        else:
            raise AssertionError("bad get_recommended_action input was accepted")


def assert_relationship_score_uses_exponential_decay_for_past_five_sessions() -> None:
    relationship = compute_relationship_score([20, 30, 40, 50, 60, 70, 80])

    if not isinstance(relationship, RelationshipScore):
        raise AssertionError(f"wrong relationship score type: {relationship}")
    if relationship.to_dict() != {
        "score": 66.77,
        "session_scores": [40.0, 50.0, 60.0, 70.0, 80.0],
        "weights": [0.2401, 0.343, 0.49, 0.7, 1.0],
        "weighted_sum": 185.154,
        "weight_total": 2.7731,
        "sessions_used": 5,
        "decay_factor": 0.7,
        "source": "exponential_decay_past_5_sessions",
    }:
        raise AssertionError(f"relationship score payload wrong: {relationship.to_dict()}")


def assert_relationship_score_accepts_session_objects_and_dicts() -> None:
    health = compute_health_score(
        intent_confidence=0.8,
        missing_info_risk=0.25,
        sentiment_score=0.5,
        loop_penalty=0.5,
        knowledge_coverage=0.7,
    )
    relationship = compute_relationship_score(
        [
            {"health_score_end": 45},
            health,
            {"relationship_score_end": 70},
        ],
        decay_factor=0.5,
    )

    if relationship.session_scores != [45.0, 67.25, 70.0]:
        raise AssertionError(f"session extraction wrong: {relationship.to_dict()}")
    if relationship.weights != [0.25, 0.5, 1.0]:
        raise AssertionError(f"custom decay weights wrong: {relationship.to_dict()}")
    if relationship.score != 65.64:
        raise AssertionError(f"custom decay score wrong: {relationship.to_dict()}")


def assert_relationship_score_defaults_when_no_history() -> None:
    relationship = compute_relationship_score([], default_score=55)

    if relationship.score != 55.0 or relationship.sessions_used != 0:
        raise AssertionError(f"empty history should return default score: {relationship.to_dict()}")
    if relationship.session_scores or relationship.weights:
        raise AssertionError(f"empty history should not invent sessions: {relationship.to_dict()}")


def assert_relationship_score_validates_inputs() -> None:
    bad_calls = (
        lambda: compute_relationship_score("bad"),
        lambda: compute_relationship_score([101]),
        lambda: compute_relationship_score([-1]),
        lambda: compute_relationship_score([{"missing": 70}]),
        lambda: compute_relationship_score([70], decay_factor=0),
        lambda: compute_relationship_score([70], decay_factor=1.1),
        lambda: compute_relationship_score([70], max_sessions=0),
        lambda: compute_relationship_score([], default_score=101),
    )
    for bad_call in bad_calls:
        try:
            bad_call()
        except ValueError:
            pass
        else:
            raise AssertionError("bad compute_relationship_score input was accepted")


def assert_session_start_behavior_routes_relationship_thresholds() -> None:
    cases = [
        (70, "HEALTHY", ">=70", "standard", "normal", False),
        (100, "HEALTHY", ">=70", "standard", "normal", False),
        (69.99, "DRIFTING", "40-70", "stabilize", "elevated", False),
        (40, "DRIFTING", "40-70", "stabilize", "elevated", False),
        (39.99, "AT_RISK", "<40", "casa_required", "urgent", True),
        (0, "AT_RISK", "<40", "casa_required", "urgent", True),
    ]

    for score, state, band, opening_mode, priority, use_casa in cases:
        behavior = session_start_behavior(score)
        if not isinstance(behavior, SessionStartBehavior):
            raise AssertionError(f"wrong session behavior type: {behavior}")
        if behavior.state != state or behavior.threshold_band != band:
            raise AssertionError(f"score {score} routed wrong: {behavior.to_dict()}")
        if behavior.opening_mode != opening_mode or behavior.priority != priority:
            raise AssertionError(f"score {score} behavior mode wrong: {behavior.to_dict()}")
        if behavior.use_casa_sequence is not use_casa:
            raise AssertionError(f"score {score} CASA flag wrong: {behavior.to_dict()}")


def assert_session_start_behavior_accepts_relationship_score_object() -> None:
    relationship = compute_relationship_score([20, 35, 39])
    behavior = session_start_behavior(relationship)

    if behavior.to_dict() != {
        "state": "AT_RISK",
        "relationship_score": 33.47,
        "threshold_band": "<40",
        "opening_mode": "casa_required",
        "priority": "urgent",
        "use_casa_sequence": True,
        "reason": "Relationship score is at risk; begin with the CASA empathy path before normal automation.",
        "source": "relationship_score_session_start",
    }:
        raise AssertionError(f"relationship-object behavior wrong: {behavior.to_dict()}")


def assert_session_start_behavior_validates_inputs() -> None:
    bad_calls = (
        lambda: session_start_behavior(-1),
        lambda: session_start_behavior(101),
        lambda: session_start_behavior("bad"),
        lambda: session_start_behavior(object()),
    )
    for bad_call in bad_calls:
        try:
            bad_call()
        except ValueError:
            pass
        else:
            raise AssertionError("bad session_start_behavior input was accepted")


def assert_casa_empathy_sequence_builds_four_at_risk_steps() -> None:
    sequence = casa_empathy_sequence(
        39.99,
        customer_name="Riya",
        issue_summary="the duplicate charge and outage credit",
    )

    if not isinstance(sequence, CASAEmpathySequence):
        raise AssertionError(f"wrong CASA sequence type: {sequence}")
    if sequence.relationship_score != 39.99 or sequence.state != "AT_RISK":
        raise AssertionError(f"CASA score/state wrong: {sequence.to_dict()}")
    if sequence.customer_ref != "Riya" or sequence.issue_summary != "the duplicate charge and outage credit":
        raise AssertionError(f"CASA context wrong: {sequence.to_dict()}")
    if [step.code for step in sequence.steps] != ["C", "A", "S", "A"]:
        raise AssertionError(f"CASA step codes wrong: {sequence.to_dict()}")
    if [step.name for step in sequence.steps] != ["Connect", "Acknowledge", "Stabilize", "Act"]:
        raise AssertionError(f"CASA step names wrong: {sequence.to_dict()}")
    if not all(isinstance(step, CASAEmpathyStep) for step in sequence.steps):
        raise AssertionError(f"CASA steps should be dataclasses: {sequence.to_dict()}")
    if sequence.steps[0].requires_verification_before_action:
        raise AssertionError(f"Connect should not require verification: {sequence.to_dict()}")
    if not sequence.steps[-1].requires_verification_before_action:
        raise AssertionError(f"Act should require verification: {sequence.to_dict()}")
    if "duplicate charge and outage credit" not in sequence.steps[1].customer_message:
        raise AssertionError(f"Acknowledge should reference issue summary: {sequence.to_dict()}")


def assert_casa_empathy_sequence_uses_safe_defaults() -> None:
    sequence = casa_empathy_sequence(0, customer_name="   ", issue_summary="")

    if sequence.customer_ref != "there":
        raise AssertionError(f"blank customer name should use default: {sequence.to_dict()}")
    if sequence.issue_summary != "the current support issue":
        raise AssertionError(f"blank issue summary should use default: {sequence.to_dict()}")
    if sequence.completion_gate != "All CASA steps must be emitted before normal automated resolution continues.":
        raise AssertionError(f"CASA completion gate wrong: {sequence.to_dict()}")


def assert_casa_empathy_sequence_rejects_non_at_risk_scores() -> None:
    bad_calls = (
        lambda: casa_empathy_sequence(40),
        lambda: casa_empathy_sequence(70),
        lambda: casa_empathy_sequence(-1),
        lambda: casa_empathy_sequence(101),
        lambda: casa_empathy_sequence("bad"),
    )
    for bad_call in bad_calls:
        try:
            bad_call()
        except ValueError:
            pass
        else:
            raise AssertionError("bad casa_empathy_sequence input was accepted")


def main() -> None:
    assert_intent_confidence_component_still_uses_softmax()
    assert_missing_info_risk_uses_slot_schema_weights()
    assert_missing_info_risk_is_zero_when_complete()
    assert_missing_info_risk_prioritizes_customer_id()
    assert_missing_info_risk_dedupes_intents_and_defaults_general()
    assert_missing_info_risk_validates_inputs()
    assert_sentiment_score_uses_last_three_messages_with_rules()
    assert_sentiment_score_uses_llm_json_output()
    assert_sentiment_prompt_and_validation()
    assert_loop_penalty_detects_two_repeated_questions()
    assert_loop_penalty_detects_three_repeated_questions()
    assert_loop_penalty_ignores_non_questions_and_assistant_repetition()
    assert_loop_penalty_validates_inputs()
    assert_loop_penalty_ignores_blank_transcript_messages()
    assert_knowledge_coverage_combines_tool_calls_and_crag_confidence()
    assert_knowledge_coverage_handles_open_tool_lists()
    assert_knowledge_coverage_treats_timeout_as_failed_tool()
    assert_knowledge_coverage_validates_inputs()
    assert_compute_health_score_applies_weighted_formula()
    assert_compute_health_score_accepts_component_objects()
    assert_compute_health_score_validates_component_ranges()
    assert_recommended_action_routes_thresholds()
    assert_recommended_action_accepts_health_score_object()
    assert_recommended_action_validates_inputs()
    assert_relationship_score_uses_exponential_decay_for_past_five_sessions()
    assert_relationship_score_accepts_session_objects_and_dicts()
    assert_relationship_score_defaults_when_no_history()
    assert_relationship_score_validates_inputs()
    assert_session_start_behavior_routes_relationship_thresholds()
    assert_session_start_behavior_accepts_relationship_score_object()
    assert_session_start_behavior_validates_inputs()
    assert_casa_empathy_sequence_builds_four_at_risk_steps()
    assert_casa_empathy_sequence_uses_safe_defaults()
    assert_casa_empathy_sequence_rejects_non_at_risk_scores()
    print("health score component tests passed")


if __name__ == "__main__":
    main()
