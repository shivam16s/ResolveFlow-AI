from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Callable

from .intent_classifier import ALLOWED_INTENTS, IntentClassification
from .slot_schema import detect_missing_required_slots, get_slot_schema


@dataclass(frozen=True)
class IntentConfidenceComponent:
    value: float
    primary_intent: str
    probabilities: dict[str, float]
    source: str = "classifier_softmax"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MissingInfoRiskComponent:
    value: float
    intents: list[str]
    total_required_weight: float
    missing_required_weight: float
    missing_slots: list[dict]
    source: str = "slot_schema"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SentimentScoreComponent:
    value: float
    label: str
    messages_analyzed: list[dict[str, str]]
    rationale: str
    source: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LoopPenaltyComponent:
    value: float
    repeated_question_count: int
    repeated_question: str | None
    question_counts: dict[str, int]
    source: str = "repeated_question_threshold"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class KnowledgeCoverageComponent:
    value: float
    tool_coverage: float
    crag_confidence: float
    tools_called: list[dict]
    required_tools: list[str]
    crag_scores: list[float]
    source: str = "tool_calls_crag_confidence"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class HealthScore:
    score: float
    components: dict[str, float]
    contributions: dict[str, float]
    formula: str = "H = 0.30*ic + 0.25*(1-mr) + 0.20*ss + 0.15*(1-lp) + 0.10*kc"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RecommendedAction:
    action: str
    label: str
    health_score: float
    threshold_band: str
    reason: str
    source: str = "health_score_thresholds"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RelationshipScore:
    score: float
    session_scores: list[float]
    weights: list[float]
    weighted_sum: float
    weight_total: float
    sessions_used: int
    decay_factor: float
    source: str = "exponential_decay_past_5_sessions"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SessionStartBehavior:
    state: str
    relationship_score: float
    threshold_band: str
    opening_mode: str
    priority: str
    use_casa_sequence: bool
    reason: str
    source: str = "relationship_score_session_start"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CASAEmpathyStep:
    code: str
    name: str
    goal: str
    customer_message: str
    agent_instruction: str
    requires_verification_before_action: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CASAEmpathySequence:
    relationship_score: float
    state: str
    customer_ref: str
    issue_summary: str
    steps: list[CASAEmpathyStep]
    completion_gate: str
    source: str = "casa_empathy_at_risk"

    def to_dict(self) -> dict:
        return asdict(self)


def intent_confidence_component(classification: IntentClassification) -> IntentConfidenceComponent:
    if not isinstance(classification, IntentClassification):
        raise ValueError("classification must be an IntentClassification")
    probabilities = _normalized_probabilities(
        classification.intent_probabilities)
    value = sum(probabilities.get(intent, 0.0)
                for intent in classification.intents)
    return IntentConfidenceComponent(
        value=round(max(0.0, min(1.0, value)), 2),
        primary_intent=classification.primary_intent,
        probabilities=probabilities,
    )


def compute_health_score(
    *,
    intent_confidence,
    missing_info_risk,
    sentiment_score,
    loop_penalty,
    knowledge_coverage,
) -> HealthScore:
    ic = _component_value(intent_confidence, "intent_confidence")
    mr = _component_value(missing_info_risk, "missing_info_risk")
    ss = _component_value(sentiment_score, "sentiment_score")
    lp = _component_value(loop_penalty, "loop_penalty")
    kc = _component_value(knowledge_coverage, "knowledge_coverage")
    contributions = {
        "intent_confidence": round(0.30 * ic, 4),
        "missing_info_completeness": round(0.25 * (1 - mr), 4),
        "sentiment_score": round(0.20 * ss, 4),
        "loop_stability": round(0.15 * (1 - lp), 4),
        "knowledge_coverage": round(0.10 * kc, 4),
    }
    raw_score = sum(contributions.values())
    return HealthScore(
        score=round(max(0.0, min(1.0, raw_score)) * 100, 2),
        components={
            "intent_confidence": ic,
            "missing_info_risk": mr,
            "sentiment_score": ss,
            "loop_penalty": lp,
            "knowledge_coverage": kc,
        },
        contributions=contributions,
    )


def casa_empathy_sequence(
    relationship_score,
    *,
    customer_name: str | None = None,
    issue_summary: str | None = None,
) -> CASAEmpathySequence:
    score = _health_score_value(relationship_score)
    if score >= 40:
        raise ValueError(
            "CASA empathy sequence is only for AT_RISK relationship scores below 40")
    customer_ref = _clean_optional_phrase(customer_name, default="there")
    summary = _clean_optional_phrase(
        issue_summary, default="the current support issue")
    steps = [
        CASAEmpathyStep(
            code="C",
            name="Connect",
            goal="Lower friction before asking for details.",
            customer_message=(
                f"Hi {customer_ref}, I can see this has taken more effort than it should. "
                "I will keep this focused and avoid making you repeat what we already know."
            ),
            agent_instruction=(
                "Open with ownership and a calm tone. Do not ask for a new slot in this step."
            ),
            requires_verification_before_action=False,
        ),
        CASAEmpathyStep(
            code="A",
            name="Acknowledge",
            goal="Reflect the risk and the known issue without overpromising.",
            customer_message=(
                f"I understand the priority is {summary}. I will check the account context first, "
                "then I will tell you exactly what I can verify."
            ),
            agent_instruction=(
                "Mention the known issue summary and explicitly promise verification before action."
            ),
            requires_verification_before_action=False,
        ),
        CASAEmpathyStep(
            code="S",
            name="Stabilize",
            goal="Set a one-step plan so the customer knows what happens next.",
            customer_message=(
                "The next step is one verification check. After that, I will either resolve it here "
                "or move it to a specialist with the context included."
            ),
            agent_instruction=(
                "Choose exactly one highest-priority verification path from the active issue queue."
            ),
            requires_verification_before_action=True,
        ),
        CASAEmpathyStep(
            code="A",
            name="Act",
            goal="Take only verified, policy-allowed action.",
            customer_message=(
                "I will only take action after the check confirms it is allowed. If anything is blocked, "
                "I will explain why and hand this over with the details already attached."
            ),
            agent_instruction=(
                "Call the mapped verification tool before applying credits, refunds, plan changes, or tickets."
            ),
            requires_verification_before_action=True,
        ),
    ]
    return CASAEmpathySequence(
        relationship_score=score,
        state="AT_RISK",
        customer_ref=customer_ref,
        issue_summary=summary,
        steps=steps,
        completion_gate="All CASA steps must be emitted before normal automated resolution continues.",
    )


def session_start_behavior(relationship_score) -> SessionStartBehavior:
    score = _health_score_value(relationship_score)
    if score >= 70:
        return SessionStartBehavior(
            state="HEALTHY",
            relationship_score=score,
            threshold_band=">=70",
            opening_mode="standard",
            priority="normal",
            use_casa_sequence=False,
            reason="Relationship score is healthy; start with the normal resolution flow.",
        )
    if score >= 40:
        return SessionStartBehavior(
            state="DRIFTING",
            relationship_score=score,
            threshold_band="40-70",
            opening_mode="stabilize",
            priority="elevated",
            use_casa_sequence=False,
            reason="Relationship score is drifting; acknowledge prior friction and keep the first step focused.",
        )
    return SessionStartBehavior(
        state="AT_RISK",
        relationship_score=score,
        threshold_band="<40",
        opening_mode="casa_required",
        priority="urgent",
        use_casa_sequence=True,
        reason="Relationship score is at risk; begin with the CASA empathy path before normal automation.",
    )


def compute_relationship_score(
    past_sessions: list,
    *,
    decay_factor: float = 0.7,
    max_sessions: int = 5,
    default_score: float = 50.0,
) -> RelationshipScore:
    if not isinstance(past_sessions, list):
        raise ValueError("past_sessions must be a list")
    if max_sessions < 1:
        raise ValueError("max_sessions must be at least 1")
    if decay_factor <= 0 or decay_factor > 1:
        raise ValueError("decay_factor must be greater than 0 and at most 1")
    default = _health_score_value(default_score)
    if not past_sessions:
        return RelationshipScore(
            score=default,
            session_scores=[],
            weights=[],
            weighted_sum=0.0,
            weight_total=0.0,
            sessions_used=0,
            decay_factor=round(decay_factor, 4),
        )

    session_scores = [
        _session_health_score_value(session, index)
        for index, session in enumerate(past_sessions)
    ][-max_sessions:]
    weights = [
        round(decay_factor ** (len(session_scores) - index - 1), 4)
        for index in range(len(session_scores))
    ]
    weighted_sum = round(
        sum(score * weight for score, weight in zip(session_scores, weights)),
        4,
    )
    weight_total = round(sum(weights), 4)
    score = default if weight_total == 0 else round(
        weighted_sum / weight_total, 2)
    return RelationshipScore(
        score=score,
        session_scores=session_scores,
        weights=weights,
        weighted_sum=weighted_sum,
        weight_total=weight_total,
        sessions_used=len(session_scores),
        decay_factor=round(decay_factor, 4),
    )


def get_recommended_action(health_score) -> RecommendedAction:
    score = _health_score_value(health_score)
    if score >= 70:
        return RecommendedAction(
            action="CONTINUE",
            label="continue autonomous resolution",
            health_score=score,
            threshold_band=">=70",
            reason="Conversation health is strong enough to continue normal automated resolution.",
        )
    if score >= 50:
        return RecommendedAction(
            action="CAUTION",
            label="proceed with caution",
            health_score=score,
            threshold_band="50-70",
            reason="Conversation health is moderate; continue, but prefer low-risk actions and tighter checks.",
        )
    if score >= 30:
        return RecommendedAction(
            action="REPAIR",
            label="repair conversation before continuing",
            health_score=score,
            threshold_band="30-50",
            reason="Conversation health is low; ask a focused repair question before more automation.",
        )
    return RecommendedAction(
        action="HANDOFF",
        label="handoff to human specialist",
        health_score=score,
        threshold_band="<30",
        reason="Conversation health is critical and should be escalated to a human specialist.",
    )


def knowledge_coverage_component(
    tools_called: list,
    crag_evaluations: list | None = None,
    *,
    required_tools: list[str] | None = None,
) -> KnowledgeCoverageComponent:
    normalized_tools = _normalize_tool_calls_for_coverage(tools_called)
    normalized_required_tools = _normalize_required_tools(required_tools)
    tool_coverage = _tool_coverage(normalized_tools, normalized_required_tools)
    crag_scores = _extract_crag_scores(crag_evaluations or [])
    crag_confidence = round(
        sum(crag_scores) / len(crag_scores), 2) if crag_scores else 0.0
    value = round((0.5 * tool_coverage) + (0.5 * crag_confidence), 2)
    return KnowledgeCoverageComponent(
        value=value,
        tool_coverage=tool_coverage,
        crag_confidence=crag_confidence,
        tools_called=normalized_tools,
        required_tools=normalized_required_tools,
        crag_scores=crag_scores,
    )


def loop_penalty_component(messages: list[dict[str, object]]) -> LoopPenaltyComponent:
    cleaned_messages = _clean_messages(messages)
    question_counts: dict[str, int] = {}
    for message in cleaned_messages:
        if message["role"] != "user":
            continue
        question = _normalized_question(message["content"])
        if question is None:
            continue
        question_counts[question] = question_counts.get(question, 0) + 1

    repeated_question = None
    repeated_count = 0
    for question, count in question_counts.items():
        if count > repeated_count:
            repeated_question = question
            repeated_count = count

    if repeated_count >= 3:
        value = 1.0
    elif repeated_count == 2:
        value = 0.5
    else:
        value = 0.0
        repeated_question = None
        repeated_count = 0

    return LoopPenaltyComponent(
        value=value,
        repeated_question_count=repeated_count,
        repeated_question=repeated_question,
        question_counts=question_counts,
    )


def sentiment_score_component(
    messages: list[dict[str, object]],
    *,
    llm_client: Callable[[str], str] | None = None,
) -> SentimentScoreComponent:
    recent_messages = _last_three_messages(messages)
    if llm_client is not None:
        raw_output = llm_client(build_sentiment_prompt(recent_messages))
        payload = _extract_json_object(raw_output)
        label = _clean_sentiment_label(payload.get("label", "neutral"))
        value = _clean_score(payload.get(
            "score", _score_for_sentiment_label(label)))
        rationale = str(payload.get("rationale", "")).strip(
        ) or f"LLM classified sentiment as {label}."
        return SentimentScoreComponent(
            value=value,
            label=label,
            messages_analyzed=recent_messages,
            rationale=rationale,
            source="llm_sentiment_last_3_messages",
        )

    label, value, rationale = _rule_sentiment(recent_messages)
    return SentimentScoreComponent(
        value=value,
        label=label,
        messages_analyzed=recent_messages,
        rationale=rationale,
        source="rule_sentiment_last_3_messages",
    )


def build_sentiment_prompt(messages: list[dict[str, str]]) -> str:
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    return (
        "Classify the customer sentiment from only the last 3 conversation messages below.\n"
        "Return strict JSON with this schema: "
        '{"label":"positive|calm|neutral|concerned|frustrated|angry","score":0.0,"rationale":"short reason"}.\n'
        "Score means conversation health from sentiment only: 1.0 is positive/calm, 0.0 is angry/hostile.\n"
        f"Messages: {json.dumps(messages, ensure_ascii=True)}"
    )


def missing_info_risk_component(
    intents: list[str],
    slots: dict[str, object] | None = None,
) -> MissingInfoRiskComponent:
    if not isinstance(intents, list):
        raise ValueError("intents must be a list")
    if slots is not None and not isinstance(slots, dict):
        raise ValueError("slots must be a dict when provided")
    normalized_intents = _dedupe_intents(intents)
    provided_slots = slots or {}
    total_weight = 0.0
    missing_weight = 0.0
    missing_slots = []

    for intent in normalized_intents:
        schema = get_slot_schema(intent)
        missing_by_slot = {
            missing.slot: missing
            for missing in detect_missing_required_slots(intent, provided_slots)
        }
        for slot in schema.slots:
            if not slot.required:
                continue
            weight = _slot_priority_weight(slot.priority)
            total_weight += weight
            missing = missing_by_slot.get(slot.name)
            if missing is None:
                continue
            missing_weight += weight
            payload = missing.to_dict()
            payload["weight"] = round(weight, 4)
            missing_slots.append(payload)

    value = 0.0 if total_weight == 0 else missing_weight / total_weight
    return MissingInfoRiskComponent(
        value=round(max(0.0, min(1.0, value)), 2),
        intents=normalized_intents,
        total_required_weight=round(total_weight, 4),
        missing_required_weight=round(missing_weight, 4),
        missing_slots=missing_slots,
    )


def _normalized_probabilities(probabilities: dict[str, float]) -> dict[str, float]:
    if not isinstance(probabilities, dict):
        raise ValueError("classification intent_probabilities must be a dict")
    cleaned = {}
    for intent in ALLOWED_INTENTS:
        try:
            value = float(probabilities.get(intent, 0.0))
        except (TypeError, ValueError):
            value = 0.0
        cleaned[intent] = max(0.0, value)
    total = sum(cleaned.values())
    if total <= 0:
        raise ValueError(
            "classification intent_probabilities must contain positive mass")
    return {
        intent: round(cleaned[intent] / total, 6)
        for intent in ALLOWED_INTENTS
    }


def _component_value(component, component_name: str) -> float:
    value = component.value if hasattr(component, "value") else component
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{component_name} must be numeric or expose a numeric value") from exc
    if numeric < 0 or numeric > 1:
        raise ValueError(f"{component_name} must be between 0 and 1")
    return round(numeric, 4)


def _health_score_value(health_score) -> float:
    value = health_score.score if hasattr(
        health_score, "score") else health_score
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "health_score must be numeric or expose a numeric score") from exc
    if numeric < 0 or numeric > 100:
        raise ValueError("health_score must be between 0 and 100")
    return round(numeric, 2)


def _session_health_score_value(session, index: int) -> float:
    if hasattr(session, "score"):
        return _health_score_value(getattr(session, "score"))
    if isinstance(session, dict):
        for key in ("health_score", "health_score_end", "score", "relationship_score_end"):
            if key in session and session[key] is not None:
                return _health_score_value(session[key])
        raise ValueError(
            f"past_sessions[{index}] must include a score-like field")
    return _health_score_value(session)


def _clean_optional_phrase(value: str | None, *, default: str) -> str:
    if value is None:
        return default
    normalized = re.sub(r"\s+", " ", str(value)).strip()
    return normalized or default


def _last_three_messages(messages: list[dict[str, object]]) -> list[dict[str, str]]:
    return _clean_messages(messages)[-3:]


def _clean_messages(messages: list[dict[str, object]]) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    cleaned = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("messages must contain dict objects")
        role = str(message.get("role", "")).strip().lower()
        content = str(message.get("content", "")).strip()
        if not role:
            raise ValueError("message role must not be empty")
        if not content:
            raise ValueError("message content must not be empty")
        cleaned.append({"role": role, "content": content})
    return cleaned


def _extract_json_object(raw_output: str) -> dict:
    cleaned = str(raw_output).strip()
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"sentiment LLM output was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("sentiment LLM output must be a JSON object")
    return payload


def _clean_sentiment_label(label: object) -> str:
    normalized = str(label).strip().lower()
    allowed = {"positive", "calm", "neutral",
               "concerned", "frustrated", "angry"}
    if normalized not in allowed:
        return "neutral"
    return normalized


def _clean_score(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.5
    return round(max(0.0, min(1.0, score)), 2)


def _score_for_sentiment_label(label: str) -> float:
    return {
        "positive": 1.0,
        "calm": 0.85,
        "neutral": 0.65,
        "concerned": 0.45,
        "frustrated": 0.25,
        "angry": 0.05,
    }.get(label, 0.65)


def _rule_sentiment(messages: list[dict[str, str]]) -> tuple[str, float, str]:
    text = " ".join(message["content"].lower() for message in messages)
    angry_terms = ("angry", "ridiculous", "useless",
                   "terrible", "hate", "stop the bot")
    frustrated_terms = ("frustrated", "tired", "annoyed",
                        "again", "still not", "not fixed", "cancel")
    concerned_terms = ("worried", "concerned",
                       "confused", "why", "please help")
    positive_terms = ("thanks", "thank you", "great",
                      "working now", "resolved")
    calm_terms = ("please", "can you", "could you")

    if any(term in text for term in angry_terms):
        return "angry", 0.05, "Recent messages contain angry or hostile terms."
    if any(term in text for term in frustrated_terms):
        return "frustrated", 0.25, "Recent messages indicate frustration or repeated unresolved effort."
    if any(term in text for term in positive_terms):
        return "positive", 1.0, "Recent messages include positive resolution language."
    if any(term in text for term in concerned_terms):
        return "concerned", 0.45, "Recent messages indicate concern or confusion."
    if any(term in text for term in calm_terms):
        return "calm", 0.85, "Recent messages are polite and calm."
    return "neutral", 0.65, "Recent messages do not show strong sentiment."


def _normalized_question(content: str) -> str | None:
    normalized = content.lower()
    normalized = re.sub(r"[^a-z0-9\s?]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None
    question_mark = "?" in normalized
    question_starts = (
        "what ",
        "why ",
        "when ",
        "where ",
        "who ",
        "how ",
        "can ",
        "could ",
        "will ",
        "do ",
        "did ",
        "is ",
        "are ",
        "am ",
        "should ",
    )
    if not question_mark and not normalized.startswith(question_starts):
        return None
    return normalized.replace("?", "").strip()


def _normalize_tool_calls_for_coverage(tools_called: list) -> list[dict]:
    if not isinstance(tools_called, list):
        raise ValueError("tools_called must be a list")
    normalized = []
    for item in tools_called:
        if isinstance(item, str):
            name = item.strip()
            if not name:
                raise ValueError("tool call names must not be empty")
            normalized.append({"name": name, "successful": True})
            continue
        if not isinstance(item, dict):
            raise ValueError("tools_called entries must be strings or dicts")
        name = str(item.get("tool_name") or item.get(
            "name") or item.get("tool") or "").strip()
        if not name:
            raise ValueError(
                "tool call dicts must include tool_name, name, or tool")
        normalized.append(
            {"name": name, "successful": _tool_call_successful(item)})
    return normalized


def _tool_call_successful(tool_call: dict) -> bool:
    if "ok" in tool_call:
        return bool(tool_call["ok"])
    status = str(tool_call.get("status", "")).strip().lower()
    if status in {"ok", "success", "successful", "completed", "resolved"}:
        return True
    if status in {"fail", "failed", "error", "blocked"}:
        return False
    if tool_call.get("error") or tool_call.get("exception"):
        return False
    return True


def _normalize_required_tools(required_tools: list[str] | None) -> list[str]:
    if required_tools is None:
        return []
    if not isinstance(required_tools, list):
        raise ValueError("required_tools must be a list when provided")
    normalized = []
    seen = set()
    for tool in required_tools:
        if not isinstance(tool, str):
            raise ValueError("required_tools must contain strings")
        name = tool.strip()
        if not name:
            raise ValueError("required tool names must not be empty")
        if name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return normalized


def _tool_coverage(tools_called: list[dict], required_tools: list[str]) -> float:
    successful_tools = {tool["name"]
                        for tool in tools_called if tool["successful"]}
    if required_tools:
        covered = sum(1 for tool in required_tools if tool in successful_tools)
        return round(covered / len(required_tools), 2)
    if not tools_called:
        return 0.0
    successful_count = sum(1 for tool in tools_called if tool["successful"])
    return round(successful_count / len(tools_called), 2)


def _extract_crag_scores(crag_evaluations: list) -> list[float]:
    if not isinstance(crag_evaluations, list):
        raise ValueError("crag_evaluations must be a list when provided")
    scores = []
    for item in crag_evaluations:
        score = _score_from_crag_item(item)
        if score is None:
            continue
        scores.append(score)
    return scores


def _score_from_crag_item(item) -> float | None:
    if hasattr(item, "to_dict") and callable(item.to_dict):
        item = item.to_dict()
    if hasattr(item, "score"):
        return _clean_score(getattr(item, "score"))
    if not isinstance(item, dict):
        raise ValueError(
            "crag_evaluations entries must be dicts, scored objects, or objects with to_dict()")
    if "score" in item:
        return _clean_score(item["score"])
    for key in ("relevance", "evaluation"):
        nested = item.get(key)
        if isinstance(nested, dict) and "score" in nested:
            return _clean_score(nested["score"])
    return None


def _dedupe_intents(intents: list[str]) -> list[str]:
    normalized = []
    seen = set()
    for intent in intents:
        if not isinstance(intent, str):
            raise ValueError("intents must contain strings")
        clean_intent = intent.strip()
        if clean_intent not in ALLOWED_INTENTS:
            raise ValueError(f"unknown intent: {intent}")
        if clean_intent in seen:
            continue
        seen.add(clean_intent)
        normalized.append(clean_intent)
    if not normalized:
        normalized.append("general_query")
    return normalized


def _slot_priority_weight(priority: int) -> float:
    return 1.0 / max(priority, 1)
