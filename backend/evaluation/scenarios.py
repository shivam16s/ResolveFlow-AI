from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.db.init_db import DEFAULT_DB_PATH


DEFAULT_EVALUATION_SCENARIOS_PATH = Path(__file__).resolve(
).parents[2] / "docs" / "evaluation_scenarios.json"

EXPECTED_SCENARIO_IDS = [
    "case_01_simple_bill_question",
    "case_02_duplicate_charge",
    "case_03_outage_credit",
    "case_04_cancellation_intent",
    "case_05_policy_exception",
    "case_06_angry_customer",
    "case_07_vague_customer",
    "case_08_wrong_refund_request",
    "case_09_tool_failure",
    "case_10_repeated_question_loop",
    "case_11_impatient_user",
    "case_12_tangential_user",
    "case_13_unavailable_service_request",
    "case_14_proactive_chennai_credit",
    "case_15_prompt_injection_refund",
    "case_16_multi_turn_digression_outage",
    "case_17_pending_cancellation_save",
    "case_18_unverified_outage_no_credit",
    "case_19_router_repeat_bengaluru",
    "case_20_critical_outage_refund_handoff",
    "case_21_suspended_plan_blocker",
    "case_22_tamil_multi_issue",
    "case_23_old_payment_refund_denial",
    "case_24_plan_downgrade_refund_combo",
    "case_25_technician_unverified_outage",
    "case_26_regional_bill_question",
    "case_27_critical_cancellation",
    "case_28_short_outage_no_credit",
    "case_29_duplicate_charge_human_request",
    "case_30_proactive_credit_then_cancel",
]


@dataclass(frozen=True)
class EvaluationScenario:
    scenario_id: str
    title: str
    customer_id: str
    customer_messages: list[str]
    initial_state: dict[str, Any]
    goal_state: dict[str, Any]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationScenarioValidationReport:
    ok: bool
    scenario_count: int
    scenario_ids: list[str]
    problems: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def load_evaluation_scenarios(
    scenarios_path: Path = DEFAULT_EVALUATION_SCENARIOS_PATH,
) -> list[EvaluationScenario]:
    payload = json.loads(Path(scenarios_path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("evaluation scenarios file must contain a JSON list")
    return [_scenario_from_payload(item) for item in payload]


def validate_evaluation_scenarios(
    *,
    scenarios_path: Path = DEFAULT_EVALUATION_SCENARIOS_PATH,
    db_path: Path = DEFAULT_DB_PATH,
) -> EvaluationScenarioValidationReport:
    problems: list[str] = []
    try:
        scenarios = load_evaluation_scenarios(scenarios_path)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        return EvaluationScenarioValidationReport(
            ok=False,
            scenario_count=0,
            scenario_ids=[],
            problems=[str(exc)],
        )

    scenario_ids = [scenario.scenario_id for scenario in scenarios]
    if scenario_ids != EXPECTED_SCENARIO_IDS:
        problems.append(
            f"expected scenario ids {EXPECTED_SCENARIO_IDS}, found {scenario_ids}")
    if len(set(scenario_ids)) != len(scenario_ids):
        problems.append("scenario ids must be unique")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for scenario in scenarios:
            problems.extend(_validate_scenario(scenario, connection))

    return EvaluationScenarioValidationReport(
        ok=not problems,
        scenario_count=len(scenarios),
        scenario_ids=scenario_ids,
        problems=problems,
    )


def _scenario_from_payload(payload: dict) -> EvaluationScenario:
    if not isinstance(payload, dict):
        raise ValueError("each evaluation scenario must be an object")
    scenario_id = _required_text(payload, "scenario_id")
    title = _required_text(payload, "title")
    customer_id = _required_text(payload, "customer_id")
    customer_messages = _required_text_list(payload, "customer_messages")
    initial_state = payload.get("initial_state")
    goal_state = payload.get("goal_state")
    if not isinstance(initial_state, dict):
        raise ValueError(f"{scenario_id}: initial_state must be an object")
    if not isinstance(goal_state, dict):
        raise ValueError(f"{scenario_id}: goal_state must be an object")
    return EvaluationScenario(
        scenario_id=scenario_id,
        title=title,
        customer_id=customer_id,
        customer_messages=customer_messages,
        initial_state=initial_state,
        goal_state=goal_state,
    )


def _validate_scenario(scenario: EvaluationScenario, connection: sqlite3.Connection) -> list[str]:
    problems: list[str] = []
    prefix = f"{scenario.scenario_id}: "
    initial_state = scenario.initial_state
    goal_state = scenario.goal_state

    customer = _customer(connection, scenario.customer_id)
    if customer is None:
        problems.append(prefix + f"customer {scenario.customer_id} not found")
    else:
        expected_customer = initial_state.get("customer")
        if not isinstance(expected_customer, dict):
            problems.append(
                prefix + "initial_state.customer must be an object")
        else:
            for field_name in ("customer_id", "account_status", "plan_id", "risk_level"):
                expected = expected_customer.get(field_name)
                if expected is None:
                    problems.append(
                        prefix + f"initial_state.customer.{field_name} is required")
                    continue
                if str(customer[field_name]) != str(expected):
                    problems.append(
                        prefix
                        + f"customer {field_name} expected {expected!r}, found {customer[field_name]!r}"
                    )

    if initial_state.get("db_reset_required") is not True:
        problems.append(
            prefix + "initial_state.db_reset_required must be true")
    if not _required_nested_text(initial_state, "scenario_date"):
        problems.append(prefix + "initial_state.scenario_date is required")

    seed_evidence = initial_state.get("seed_evidence")
    if not isinstance(seed_evidence, dict):
        problems.append(
            prefix + "initial_state.seed_evidence must be an object")
    else:
        problems.extend(_validate_references(
            prefix, seed_evidence, connection))

    expected_final_status = goal_state.get("expected_final_status")
    if expected_final_status not in {"active", "ask", "resolved", "escalated", "abandoned"}:
        problems.append(prefix + "goal_state.expected_final_status is invalid")
    for field_name in (
        "expected_intents",
        "issue_queue_order",
        "required_tools",
        "forbidden_tools",
        "required_policies",
        "success_criteria",
    ):
        if not isinstance(goal_state.get(field_name), list):
            problems.append(prefix + f"goal_state.{field_name} must be a list")
    if not isinstance(goal_state.get("expected_artifacts"), dict):
        problems.append(
            prefix + "goal_state.expected_artifacts must be an object")
    if not goal_state.get("success_criteria"):
        problems.append(
            prefix + "goal_state.success_criteria must not be empty")

    context = initial_state.get("conversation_context", [])
    if not isinstance(context, list):
        problems.append(
            prefix + "initial_state.conversation_context must be a list")
    return problems


def _validate_references(prefix: str, seed_evidence: dict, connection: sqlite3.Connection) -> list[str]:
    problems: list[str] = []
    for field_name, table_name, id_column in (
        ("invoices", "invoices", "invoice_id"),
        ("payments", "payments", "payment_id"),
        ("outages", "outages", "outage_id"),
    ):
        references = seed_evidence.get(field_name)
        if not isinstance(references, list):
            problems.append(
                prefix + f"initial_state.seed_evidence.{field_name} must be a list")
            continue
        for reference_id in references:
            exists = connection.execute(
                f"SELECT 1 FROM {table_name} WHERE {id_column} = ?",
                (str(reference_id),),
            ).fetchone()
            if exists is None:
                problems.append(
                    prefix + f"{table_name} reference {reference_id!r} not found")
    return problems


def _customer(connection: sqlite3.Connection, customer_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT customer_id, account_status, plan_id, risk_level
        FROM customers
        WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()


def _required_text(payload: dict, field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _required_nested_text(payload: dict, field_name: str) -> str | None:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _required_text_list(payload: dict, field_name: str) -> list[str]:
    value = payload.get(field_name)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} must be a non-empty list")
    normalized = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} entries must be non-empty strings")
        normalized.append(item.strip())
    return normalized
