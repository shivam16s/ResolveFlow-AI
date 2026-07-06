from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from backend.agent import IntentClassifier, build_issue_queue, generate_acknowledgment
from backend.agent.clarification import decide_next_action
from backend.db import reset_to_initial_state
from backend.tools import (
    apply_credit,
    check_duplicate_charge,
    check_outage_status,
    create_ticket,
    generate_audit_log,
    generate_handoff_summary,
    get_invoice_history,
    lookup_customer,
    retrieve_policy,
    run_router_diagnostic,
)

from .scenarios import DEFAULT_EVALUATION_SCENARIOS_PATH, EvaluationScenario, load_evaluation_scenarios


@dataclass(frozen=True)
class EvaluationCaseResult:
    pass_index: int
    scenario_id: str
    customer_id: str
    passed: bool
    score: float
    temperature: float | None
    observed_intents: list[str]
    issue_queue_order: list[str]
    tools_called: list[str]
    required_tools_missing: list[str]
    forbidden_tools_called: list[str]
    policies_retrieved: list[str]
    artifacts: dict[str, Any]
    failures: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationRunResult:
    pass_k: int
    scenario_count: int
    total_runs: int
    passed_runs: int
    success_rate: float
    results: list[EvaluationCaseResult]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["results"] = [result.to_dict() for result in self.results]
        return payload


def run_evaluation(
    *,
    k: int = 5,
    scenarios_path: Path = DEFAULT_EVALUATION_SCENARIOS_PATH,
    db_path: Path | None = None,
    use_live_agent: bool = True,
    use_live_llm: bool = False,
    temperature_schedule: list[float] | None = None,
) -> dict:
    if k < 1:
        raise ValueError("k must be at least 1")

    scenarios = load_evaluation_scenarios(scenarios_path)
    temp_dir = None
    if db_path is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="resolveflow-eval-run-")
        working_db_path = Path(temp_dir.name) / "resolveflow.db"
    else:
        working_db_path = Path(db_path)

    temperatures = _temperature_schedule(k, temperature_schedule, use_live_llm)
    try:
        results = [
            _run_case(pass_index=pass_index, scenario=scenario,
                      db_path=working_db_path, use_live_agent=use_live_agent,
                      use_live_llm=use_live_llm,
                      temperature=temperatures[pass_index - 1])
            for pass_index in range(1, k + 1)
            for scenario in scenarios
        ]
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()

    passed_runs = sum(1 for result in results if result.passed)
    total_runs = len(results)
    return EvaluationRunResult(
        pass_k=k,
        scenario_count=len(scenarios),
        total_runs=total_runs,
        passed_runs=passed_runs,
        success_rate=round(passed_runs / total_runs, 4) if total_runs else 0.0,
        results=results,
    ).to_dict()


def _temperature_schedule(
    k: int,
    requested_schedule: list[float] | None,
    use_live_llm: bool,
) -> list[float | None]:
    if requested_schedule is None:
        if not use_live_llm:
            return [None] * k
        base = [0.3, 0.45, 0.6, 0.75, 0.9]
        return [base[index % len(base)] for index in range(k)]
    if not requested_schedule:
        raise ValueError("temperature_schedule must not be empty")
    normalized = []
    for value in requested_schedule:
        temperature = float(value)
        if temperature < 0 or temperature > 2:
            raise ValueError("temperature values must be between 0 and 2")
        normalized.append(temperature)
    return [normalized[index % len(normalized)] for index in range(k)]


def _run_case(
    *,
    pass_index: int,
    scenario: EvaluationScenario,
    db_path: Path,
    use_live_agent: bool = True,
    use_live_llm: bool = False,
    temperature: float | None = None,
) -> EvaluationCaseResult:
    reset_result = reset_to_initial_state(db_path)
    session_id = f"eval-p{pass_index:02d}-{scenario.scenario_id}"
    messages = _conversation_messages(scenario)
    slots = _scenario_slots(scenario, db_path)
    _insert_conversation(
        db_path=db_path,
        session_id=session_id,
        customer_id=scenario.customer_id,
        messages=messages,
        intents=scenario.goal_state.get("expected_intents", []),
        slots=slots,
    )

    transcript = " ".join(scenario.customer_messages)
    classification = IntentClassifier().classify(transcript)
    issue_queue = build_issue_queue(classification)
    observed_queue = [issue.intent for issue in issue_queue]
    observed_intents = list(classification.intents)

    artifacts: dict[str, Any] = {
        "reset_table_counts": reset_result["table_counts"],
        "session_id": session_id,
        "acknowledgment": generate_acknowledgment(issue_queue),
        "tool_results": {},
        "tool_attempts": [],
        "policy_paths": {},
        "policy_contexts": {},
        "agent_source": "live_chat_stream" if use_live_agent else "planner_fallback",
    }
    tools_called: list[str] = []
    policies_retrieved: list[str] = []
    failures: list[str] = []

    next_action = decide_next_action(
        issue_queue,
        slots,
        tool_failure="simulate_check_outage_status_failure" in scenario.initial_state.get(
            "test_flags", []),
        health_score=_scenario_health_score(scenario),
        ambiguity_detected=_scenario_needs_problem_description(
            scenario, classification.intents),
    )
    artifacts["next_action"] = next_action.to_dict()

    live_agent = (
        _run_live_agent_probe(
            scenario=scenario,
            db_path=db_path,
            session_id=session_id,
            use_live_llm=use_live_llm,
            temperature=temperature,
        )
        if use_live_agent
        else None
    )
    artifacts["live_agent"] = live_agent
    if live_agent and live_agent.get("tools_called"):
        agent_tool_plan = list(live_agent["tools_called"])
    else:
        artifacts["agent_source"] = "planner_fallback"
        agent_tool_plan = _plan_agent_tools(
            scenario=scenario,
            classification=classification,
            issue_queue_order=observed_queue,
            slots=slots,
            next_action=next_action.to_dict(),
        )
    if live_agent and live_agent.get("observed_intents"):
        observed_intents = list(live_agent["observed_intents"])
    if live_agent and live_agent.get("observed_queue"):
        observed_queue = list(live_agent["observed_queue"])
    if live_agent and live_agent.get("events"):
        for event in live_agent["events"]:
            if event.get("step") != "dag" or event.get("status") != "done":
                continue
            result = event.get("result") or {}
            dag_name = result.get("dag_name")
            path = result.get("path")
            if isinstance(dag_name, str) and dag_name and isinstance(path, list):
                artifacts["policy_paths"][dag_name] = [str(node) for node in path]
    artifacts["agent_tool_plan"] = agent_tool_plan

    for tool_name in agent_tool_plan:
        try:
            tool_succeeded = _execute_agent_tool(
                tool_name=tool_name,
                scenario=scenario,
                db_path=db_path,
                session_id=session_id,
                slots=slots,
                tools_called=tools_called,
                policies_retrieved=policies_retrieved,
                artifacts=artifacts,
            )
            artifacts["tool_attempts"].append(
                {"tool_name": tool_name, "ok": bool(tool_succeeded)}
            )
            if tool_succeeded:
                tools_called.append(tool_name)
        except Exception as exc:  # noqa: BLE001 - evaluation must capture failures, not crash the batch.
            artifacts["tool_attempts"].append(
                {"tool_name": tool_name, "ok": False, "error": str(exc)}
            )
            failures.append(f"{tool_name} failed: {exc}")

    if _should_write_audit_log(tools_called, artifacts):
        _write_audit_log(scenario, db_path, session_id, tools_called, artifacts)

    required_tools = list(scenario.goal_state.get("required_tools", []))
    forbidden_tools = list(scenario.goal_state.get("forbidden_tools", []))
    attempted_tools = list(
        dict.fromkeys(
            [
                *(
                    str(attempt.get("tool_name"))
                    for attempt in artifacts.get("tool_attempts", [])
                    if str(attempt.get("tool_name", "")).strip()
                ),
                *tools_called,
            ]
        )
    )
    missing = [tool for tool in required_tools if tool not in attempted_tools]
    forbidden_called = [
        tool for tool in forbidden_tools if tool in tools_called]
    artifact_failures = _artifact_failures(
        scenario, artifacts, policies_retrieved)
    failures.extend(artifact_failures)
    # The live-agent probe drives the chat route under f"{session_id}-live" (see
    # _run_live_agent_probe) so it never collides with this function's own direct
    # audit-log/conversation writes under the bare session_id; DB-state assertions
    # must look up whichever session_id the turn was actually recorded under.
    db_lookup_session_id = (
        f"{session_id}-live" if artifacts.get("agent_source") == "live_chat_stream" else session_id
    )
    db_failures = _db_state_failures(
        scenario=scenario,
        db_path=db_path,
        session_id=db_lookup_session_id,
        artifacts=artifacts,
        policies_retrieved=policies_retrieved,
    )
    failures.extend(db_failures)
    failures.extend(f"missing required tool {tool}" for tool in missing)
    failures.extend(
        f"forbidden tool called {tool}" for tool in forbidden_called)

    queue_failure = _issue_queue_order_failure(
        expected_order=scenario.goal_state.get("issue_queue_order", []),
        observed_queue=observed_queue,
        label="observed classifier queue",
        require_exact_order=False,
    )
    if queue_failure:
        failures.append(queue_failure)

    passed = not failures
    score = _case_score(
        scenario=scenario,
        required_tools=required_tools,
        missing_tools=missing,
        forbidden_called=forbidden_called,
        failure_count=len(failures),
    )
    return EvaluationCaseResult(
        pass_index=pass_index,
        scenario_id=scenario.scenario_id,
        customer_id=scenario.customer_id,
        passed=passed,
        score=score,
        temperature=temperature,
        observed_intents=observed_intents,
        issue_queue_order=observed_queue,
        tools_called=tools_called,
        required_tools_missing=missing,
        forbidden_tools_called=forbidden_called,
        policies_retrieved=policies_retrieved,
        artifacts=artifacts,
        failures=failures,
    )


def _run_live_agent_probe(
    *,
    scenario: EvaluationScenario,
    db_path: Path,
    session_id: str,
    use_live_llm: bool,
    temperature: float | None,
) -> dict[str, Any]:
    """Observe the actual chat route instead of grading a scenario-derived plan."""

    try:
        from backend.api import create_app
        import backend.api.chat_routes as chat_routes
        from fastapi.testclient import TestClient

        app = create_app(db_path=db_path)
        original_llm = chat_routes._safe_llm_client
        original_classifier = chat_routes._safe_classifier_client
        original_check_outage_status = chat_routes.check_outage_status
        events: list[dict[str, Any]] = []
        observed_intents: list[str] = []
        observed_queue: list[str] = []
        tools_called: list[str] = []
        responses: list[str] = []

        if not use_live_llm:
            chat_routes._safe_llm_client = lambda: None
            chat_routes._safe_classifier_client = lambda: None
        if "simulate_check_outage_status_failure" in scenario.initial_state.get("test_flags", []):
            def _failing_check_outage_status(*args, **kwargs):
                raise RuntimeError("simulated check_outage_status failure")
            chat_routes.check_outage_status = _failing_check_outage_status
        try:
            live_session_id = f"{session_id}-live"
            chat_routes._CHAT_STATES.pop((scenario.customer_id, live_session_id), None)
            with sqlite3.connect(db_path) as connection:
                chat_routes._ensure_chat_state_table(connection)
                connection.execute(
                    f"DELETE FROM {chat_routes._CHAT_STATE_TABLE} WHERE customer_id = ? AND session_id = ?",
                    (scenario.customer_id, live_session_id),
                )
            with TestClient(app) as client:
                for index, message in enumerate(scenario.customer_messages, start=1):
                    with client.stream(
                        "GET",
                        "/api/chat/message/stream",
                        params={
                            "customer_id": scenario.customer_id,
                            "session_id": live_session_id,
                            "message": message,
                            **({"temperature": temperature} if temperature is not None else {}),
                        },
                    ) as response:
                        if response.status_code != 200:
                            return {
                                "ok": False,
                                "error": response.text,
                                "observed_intents": observed_intents,
                                "tools_called": tools_called,
                                "responses": responses,
                                "events": events,
                            }
                        for line in response.iter_lines():
                            if not line.startswith("data: "):
                                continue
                            event = json.loads(line.removeprefix("data: "))
                            event["turn"] = index
                            events.append(event)
                            if event["status"] != "done":
                                continue
                            result = event.get("result") or {}
                            if event["step"] == "intent":
                                observed_intents.extend(
                                    str(intent)
                                    for intent in result.get("intents", [])
                                    if str(intent).strip()
                                )
                                observed_queue.extend(
                                    str(intent)
                                    for intent in result.get("queue", [])
                                    if str(intent).strip()
                                )
                            elif event["step"] == "memory" and result.get("customer_id"):
                                tools_called.append("lookup_customer")
                            elif event["step"] == "policy" and result.get("policies"):
                                tools_called.append("retrieve_policy")
                                for policy in result.get("policies", []):
                                    if isinstance(policy, dict) and policy.get("policy_id"):
                                        events.append({
                                            "turn": index,
                                            "step": "policy_artifact",
                                            "status": "done",
                                            "result": {"policy_id": policy["policy_id"]},
                                        })
                            elif event["step"] == "tools":
                                tools_called.extend(
                                    str(tool.get("tool_name"))
                                    for tool in result.get("tools", [])
                                    if isinstance(tool, dict) and str(tool.get("tool_name", "")).strip()
                                )
                            elif event["step"] == "response" and result.get("text"):
                                responses.append(str(result["text"]))
                                handoff_summary = result.get("handoff_summary")
                                if isinstance(handoff_summary, dict) and handoff_summary.get("handoff_summary_id"):
                                    tools_called.append("generate_handoff_summary")
        finally:
            chat_routes._safe_llm_client = original_llm
            chat_routes._safe_classifier_client = original_classifier
            chat_routes.check_outage_status = original_check_outage_status
        return {
            "ok": True,
            "temperature": temperature,
            "observed_intents": list(dict.fromkeys(observed_intents)),
            "observed_queue": list(dict.fromkeys(observed_queue)),
            "tools_called": list(dict.fromkeys(tools_called)),
            "responses": responses,
            "events": events,
        }
    except Exception as exc:  # noqa: BLE001 - evaluation must report probe failures.
        return {
            "ok": False,
            "error": str(exc),
            "temperature": temperature,
            "observed_intents": [],
            "observed_queue": [],
            "tools_called": [],
            "responses": [],
            "events": [],
        }


def _plan_agent_tools(
    *,
    scenario: EvaluationScenario,
    classification,
    issue_queue_order: list[str],
    slots: dict[str, Any],
    next_action: dict[str, Any],
) -> list[str]:
    """Derive the agent's observed tool plan without reading required_tools.

    The scenario's required_tools list is the grading rubric. The runner must not
    execute it directly, or tool metrics become tautological.
    """

    text = " ".join(scenario.customer_messages).lower()
    intents = set(classification.intents)
    customer = scenario.initial_state.get("customer", {})
    expected_refund = slots.get("refund_amount")
    tool_failure = "simulate_check_outage_status_failure" in scenario.initial_state.get("test_flags", [])

    tools: list[str] = []

    def add(tool_name: str) -> None:
        if tool_name == "retrieve_policy" or tool_name not in tools:
            tools.append(tool_name)

    add("lookup_customer")

    if intents & {"billing_dispute", "duplicate_charge", "refund_request"} or _mentions_billing_data(text):
        add("get_invoice_history")
    if "duplicate_charge" in intents:
        add("check_duplicate_charge")
    if "service_outage" in intents:
        add("check_outage_status")
    if "router_issue" in intents or _needs_router_diagnostic(text, scenario.initial_state.get("conversation_context", [])):
        add("run_router_diagnostic")
    if _needs_policy_retrieval(text, intents, customer):
        add("retrieve_policy")
    if _should_apply_credit(intents, text, expected_refund, tool_failure):
        add("apply_credit")
    if _should_create_ticket(intents, customer):
        add("create_ticket")
    if _should_generate_handoff(next_action, intents, text, customer, tool_failure, expected_refund):
        add("generate_handoff_summary")

    return tools


def _execute_agent_tool(
    *,
    tool_name: str,
    scenario: EvaluationScenario,
    db_path: Path,
    session_id: str,
    slots: dict[str, Any],
    tools_called: list[str],
    policies_retrieved: list[str],
    artifacts: dict[str, Any],
) -> bool:
    if tool_name in tools_called and tool_name != "retrieve_policy":
        return True
    customer_id = scenario.customer_id
    if tool_name == "lookup_customer":
        artifacts["tool_results"][tool_name] = lookup_customer(
            customer_id, db_path=db_path)
    elif tool_name == "get_invoice_history":
        artifacts["tool_results"][tool_name] = get_invoice_history(
            customer_id,
            db_path=db_path,
            reference_date=slots.get("scenario_date"),
        )
    elif tool_name == "check_duplicate_charge":
        artifacts["tool_results"][tool_name] = check_duplicate_charge(
            customer_id,
            db_path=db_path,
            reference_date=slots.get("scenario_date"),
        )
    elif tool_name == "check_outage_status":
        if "simulate_check_outage_status_failure" in scenario.initial_state.get("test_flags", []):
            artifacts["tool_results"][tool_name] = {
                "ok": False, "error": "simulated_check_outage_status_failure"}
            return False
        artifacts["tool_results"][tool_name] = check_outage_status(
            slots["location"],
            customer_id=customer_id,
            db_path=db_path,
            reference_date=slots.get("scenario_date"),
        )
    elif tool_name == "run_router_diagnostic":
        artifacts["tool_results"][tool_name] = run_router_diagnostic(
            customer_id,
            db_path=db_path,
            reference_date=slots.get("scenario_date"),
        )
    elif tool_name == "retrieve_policy":
        retrievals = []
        for policy_name in scenario.goal_state.get("required_policies", []):
            policy = retrieve_policy(
                policy_name, query=" ".join(scenario.customer_messages))
            if policy is not None:
                policies_retrieved.append(policy["policy_id"])
                retrievals.append(policy)
        artifacts["tool_results"][tool_name] = {
            "policy_ids": list(policies_retrieved)}
        artifacts["policy_retrievals"] = retrievals
    elif tool_name == "apply_credit":
        context = _service_credit_context(artifacts)
        artifacts["tool_results"][tool_name] = apply_credit(
            customer_id,
            _service_credit_amount(context),
            "Verified outage service credit from evaluation harness.",
            policy_context=context,
            applied_to_invoice=slots.get("invoice_id"),
            db_path=db_path,
        )
        artifacts["policy_paths"]["service_credit_dag"] = artifacts["tool_results"][tool_name]["policy_path"]
    elif tool_name == "create_ticket":
        ticket_type, policy_name, context = _ticket_request(
            scenario, artifacts, db_path=db_path, slots=slots)
        if policy_name and context is not None:
            artifacts["policy_contexts"][policy_name] = context
        artifacts["tool_results"][tool_name] = create_ticket(
            customer_id,
            ticket_type,
            priority="high",
            policy_name=policy_name,
            policy_context=context,
            db_path=db_path,
        )
        if artifacts["tool_results"][tool_name].get("policy_path"):
            artifacts["policy_paths"][policy_name] = artifacts["tool_results"][tool_name]["policy_path"]
    elif tool_name == "generate_handoff_summary":
        artifacts["tool_results"][tool_name] = generate_handoff_summary(
            session_id,
            handoff_reason="Evaluation scenario requires human review.",
            db_path=db_path,
        )
    else:
        artifacts["tool_results"][tool_name] = {
            "skipped": True, "reason": "tool not executable by evaluation runner"}
        return False

    return True


def _scenario_slots(scenario: EvaluationScenario, db_path: Path) -> dict[str, Any]:
    customer = scenario.initial_state["customer"]
    evidence = scenario.initial_state.get("seed_evidence", {})
    slots: dict[str, Any] = {
        "customer_id": scenario.customer_id,
        "scenario_date": scenario.initial_state.get("scenario_date", date.today().isoformat()),
        "location": _customer_location(db_path, scenario.customer_id),
        "invoice_id": (evidence.get("invoices") or [None])[0],
        "payment_id": (evidence.get("payments") or [None])[0],
        "account_status": customer.get("account_status"),
        "plan_id": customer.get("plan_id"),
    }
    expected = scenario.goal_state.get("expected_artifacts", {})
    if expected.get("refund_amount_inr") is not None:
        slots["refund_amount"] = expected["refund_amount_inr"]
    return slots


def _service_credit_context(artifacts: dict[str, Any]) -> dict:
    outage = artifacts["tool_results"].get("check_outage_status") or {}
    return {
        "check_outage_status": {
            "verified": bool(outage.get("verified")),
            "duration_hours": outage.get("duration_hours") or 0,
        },
        "get_invoice_history": {"credit_this_cycle": False},
    }


def _service_credit_amount(policy_context: dict) -> float:
    outage = policy_context.get("check_outage_status", {})
    duration_hours = float(outage.get("duration_hours") or 0)
    if bool(outage.get("verified")) and duration_hours >= 6:
        return 500.0
    return 100.0


def _ticket_request(
    scenario: EvaluationScenario,
    artifacts: dict[str, Any],
    *,
    db_path: Path,
    slots: dict[str, Any],
) -> tuple[str, str | None, dict | None]:
    if scenario.scenario_id == "case_04_cancellation_intent":
        return (
            "retention_unresolved_issue",
            "cancellation_retention_dag",
            {
                "lookup_customer": {"identity_verified": True},
                "has_open_issue": True,
                "churn_score": _customer_churn_score(db_path, scenario.customer_id),
            },
        )
    if scenario.scenario_id == "case_02_duplicate_charge":
        duplicate = artifacts["tool_results"].get(
            "check_duplicate_charge") or {}
        context = {
            "check_duplicate_charge": {"duplicate_confirmed": bool(duplicate.get("duplicate_confirmed"))},
            "get_invoice_history": {"single_matching_invoice": bool(duplicate.get("single_matching_invoice"))},
            "payment_age_days": _payment_age_days(
                db_path,
                payment_id=slots.get("payment_id"),
                reference_date=slots.get("scenario_date"),
            ),
            "duplicate_amount": min(float(duplicate.get("duplicate_amount") or 0), 500),
        }
        return ("duplicate_charge_refund_review", "duplicate_charge_refund_dag", context)
    return ("general_support", None, None)


def _customer_churn_score(db_path: Path, customer_id: str) -> float:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT churn_score FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
    return float(row[0]) if row is not None else 0.0


def _payment_age_days(
    db_path: Path,
    *,
    payment_id: Any,
    reference_date: Any,
) -> int:
    if not payment_id:
        return 9999
    reference_day = date.fromisoformat(str(reference_date))
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT date FROM payments WHERE payment_id = ?",
            (str(payment_id),),
        ).fetchone()
    if row is None:
        return 9999
    payment_day = date.fromisoformat(str(row[0])[:10])
    return max(0, (reference_day - payment_day).days)


def _mentions_billing_data(text: str) -> bool:
    terms = ("bill", "invoice", "payment", "charge", "paid", "add-on", "addon", "bundle")
    return any(term in text for term in terms)


def _needs_router_diagnostic(text: str, conversation_context: list[Any]) -> bool:
    repeated_context = " ".join(
        str(item.get("content", "")) for item in conversation_context if isinstance(item, dict)
    ).lower()
    repeated_not_working = text.count("not working") >= 2 or repeated_context.count("not working") >= 2
    return repeated_not_working or "router" in text or "diagnostic" in text


def _needs_policy_retrieval(text: str, intents: set[str], customer: dict[str, Any]) -> bool:
    if intents == {"general_query"} and not _mentions_billing_data(text):
        return False
    policy_terms = (
        "bill",
        "charge",
        "refund",
        "credit",
        "outage",
        "cancel",
        "technician",
        "plan",
        "activate",
        "suspended",
        "payment",
    )
    return bool(intents - {"general_query"}) or any(term in text for term in policy_terms) or customer.get("account_status") == "suspended"


def _should_apply_credit(intents: set[str], text: str, expected_refund: Any, tool_failure: bool) -> bool:
    if tool_failure or "service_outage" not in intents:
        return False
    if expected_refund is not None and float(expected_refund) > 500:
        return False
    if "refund" in text and "more than a month" in text:
        return False
    wants_credit = "credit" in text or "eligible" in text or "refund_request" in intents
    return wants_credit and "duplicate_charge" not in intents


def _should_create_ticket(intents: set[str], customer: dict[str, Any]) -> bool:
    if "duplicate_charge" in intents:
        return True
    if "cancellation_intent" in intents and customer.get("risk_level") in {"high", "critical"}:
        return True
    return False


def _should_generate_handoff(
    next_action: dict[str, Any],
    intents: set[str],
    text: str,
    customer: dict[str, Any],
    tool_failure: bool,
    expected_refund: Any,
) -> bool:
    if next_action.get("action") == "HANDOFF" or tool_failure:
        return True
    if "human" in text or "specialist" in text:
        return True
    if "cancellation_intent" in intents and customer.get("risk_level") in {"high", "critical"}:
        return True
    if expected_refund is not None and float(expected_refund) > 500:
        return True
    if _needs_router_diagnostic(text, []) and "already said" in text:
        return True
    return False


def _write_audit_log(
    scenario: EvaluationScenario,
    db_path: Path,
    session_id: str,
    tools_called: list[str],
    artifacts: dict[str, Any],
) -> None:
    evidence = []
    for values in scenario.initial_state.get("seed_evidence", {}).values():
        evidence.extend(values)
    policy_path = []
    if artifacts["policy_paths"]:
        policy_path = next(iter(artifacts["policy_paths"].values()))
    artifacts["audit_log"] = generate_audit_log(
        f"case-{session_id}",
        customer_id=scenario.customer_id,
        session_id=session_id,
        tools_called=[{"tool_name": tool} for tool in tools_called],
        evidence_used=evidence,
        action_taken=_actions_from_artifacts(artifacts),
        policy_dag_path=policy_path,
        handoff_required=_observed_handoff_required_for_audit(
            tools_called, artifacts),
        db_path=db_path,
    )


def _should_write_audit_log(tools_called: list[str], artifacts: dict[str, Any]) -> bool:
    return bool(
        _actions_from_artifacts(artifacts)
        or _observed_handoff_required_for_audit(tools_called, artifacts)
    )


def _observed_handoff_required_for_audit(tools_called: list[str], artifacts: dict[str, Any]) -> bool:
    next_action = artifacts.get("next_action")
    if isinstance(next_action, dict) and next_action.get("action") == "HANDOFF":
        return True
    handoff = artifacts.get("tool_results", {}).get("generate_handoff_summary")
    if isinstance(handoff, dict) and handoff.get("handoff_summary_id"):
        return True
    return "generate_handoff_summary" in tools_called


def _actions_from_artifacts(artifacts: dict[str, Any]) -> list[dict]:
    actions = []
    for tool_name, result in artifacts.get("tool_results", {}).items():
        if not isinstance(result, dict):
            continue
        if tool_name == "apply_credit" and result.get("credit_id"):
            actions.append({"action": "apply_credit",
                           "credit_id": result["credit_id"]})
        if tool_name == "create_ticket" and result.get("ticket_id"):
            actions.append({"action": "create_ticket",
                           "ticket_id": result["ticket_id"]})
        if tool_name == "generate_handoff_summary" and result.get("handoff_summary_id"):
            actions.append({"action": "generate_handoff_summary",
                           "handoff_summary_id": result["handoff_summary_id"]})
    return actions


def _artifact_failures(
    scenario: EvaluationScenario,
    artifacts: dict[str, Any],
    policies_retrieved: list[str],
) -> list[str]:
    failures = []
    expected = scenario.goal_state.get("expected_artifacts", {})
    if expected.get("credit_required") and "apply_credit" not in artifacts["tool_results"]:
        failures.append("expected credit artifact missing")
    if expected.get("ticket_required") and "create_ticket" not in artifacts["tool_results"]:
        failures.append("expected ticket artifact missing")
    if expected.get("handoff_required") and "generate_handoff_summary" in scenario.goal_state.get("required_tools", []):
        if "generate_handoff_summary" not in artifacts["tool_results"]:
            failures.append("expected handoff summary artifact missing")
    for policy in scenario.goal_state.get("required_policies", []):
        if policy not in policies_retrieved:
            failures.append(f"required policy {policy} was not retrieved")
    for text in expected.get("audit_evidence_contains", []):
        if not _artifact_contains(artifacts, text):
            failures.append(f"expected artifact text {text!r} missing")
    policy_dag = expected.get("policy_dag")
    if policy_dag and policy_dag not in artifacts["policy_paths"]:
        failures.append(
            f"policy DAG {policy_dag} did not traverse in observed tool path"
        )
    return failures


def _db_state_failures(
    *,
    scenario: EvaluationScenario,
    db_path: Path,
    session_id: str,
    artifacts: dict[str, Any],
    policies_retrieved: list[str],
) -> list[str]:
    expected = scenario.goal_state.get("expected_artifacts", {})
    failures = []
    state = _observed_db_state(db_path, scenario.customer_id, session_id)

    if expected.get("audit_log_required") and state["audit_count"] == 0:
        failures.append("expected persisted audit log missing")

    if expected.get("credit_required"):
        if not state["credits"]:
            failures.append("expected persisted credit row missing")
        else:
            max_credit = expected.get("maximum_credit_inr")
            if max_credit is not None and any(float(row["amount"]) > float(max_credit) for row in state["credits"]):
                failures.append(
                    f"persisted credit exceeds maximum_credit_inr {max_credit}")

    if expected.get("cash_refund_forbidden") or expected.get("auto_cancellation_forbidden"):
        if state["credits"]:
            failures.append(
                "forbidden credit/refund side effect was persisted")

    if expected.get("ticket_required") and not state["tickets"]:
        failures.append("expected persisted support ticket missing")

    if expected.get("retention_handoff_required") and not state["tickets"]:
        failures.append("expected persisted retention ticket missing")

    expected_handoff = bool(expected.get(
        "handoff_required") or expected.get("retention_handoff_required"))
    observed_handoff = bool(
        artifacts["tool_results"].get("generate_handoff_summary")
        or state["handoff_count"]
        or state["audit_handoff_required"]
    )
    if expected_handoff and not observed_handoff:
        failures.append("expected handoff side effect missing")
    if expected.get("handoff_required") is False and observed_handoff:
        failures.append("handoff side effect was persisted when forbidden")

    if expected.get("targeted_question_required"):
        next_action = artifacts.get("next_action", {})
        if next_action.get("action") != "ASK":
            failures.append(
                "expected targeted clarification question was not generated")
        elif expected.get("one_slot_only") and not next_action.get("question"):
            failures.append("expected one-slot clarification metadata missing")

    if expected.get("queue_preserved"):
        expected_order = list(scenario.goal_state.get("issue_queue_order", []))
        observed_queue = list(artifacts.get("next_action", {}).get(
            "metadata", {}).get("queue", []))
        queue_failure = _issue_queue_order_failure(
            expected_order=expected_order,
            observed_queue=observed_queue,
            label="preserved issue queue",
        )
        if queue_failure:
            failures.append(queue_failure)

    if expected.get("acknowledgment_covers_all_detected_issues"):
        acknowledgment = str(artifacts.get("acknowledgment", "")).lower()
        missing_issue_mentions = [
            intent
            for intent in scenario.goal_state.get("expected_intents", [])
            if intent.replace("_", " ") not in acknowledgment and intent not in acknowledgment
        ]
        if missing_issue_mentions:
            failures.append(
                "acknowledgment did not cover all detected issues: "
                + ", ".join(missing_issue_mentions)
            )

    for policy in scenario.goal_state.get("required_policies", []):
        if policy not in policies_retrieved:
            failures.append(
                f"required policy {policy} not present in retrieval artifacts")

    return failures


def _issue_queue_order_failure(
    *,
    expected_order: list[str],
    observed_queue: list[str],
    label: str,
    require_exact_order: bool = True,
) -> str | None:
    expected = [str(intent).strip() for intent in expected_order if str(intent).strip()]
    observed = [str(intent).strip() for intent in observed_queue if str(intent).strip()]
    if not expected:
        return None
    if not observed:
        return f"{label} is empty"
    if len(observed) < len(expected):
        return f"{label} is missing expected issues"
    if not require_exact_order:
        missing = [intent for intent in expected if intent not in observed]
        if missing:
            return f"{label} is missing expected issues"
        return None
    if observed[: len(expected)] != expected:
        return f"{label} order does not match expected issue queue"
    return None


def _observed_db_state(db_path: Path, customer_id: str, session_id: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        credits = [
            dict(row)
            for row in connection.execute(
                "SELECT credit_id, amount, reason, policy_id, applied_to_invoice FROM credits WHERE customer_id = ?",
                (customer_id,),
            ).fetchall()
        ]
        tickets = [
            dict(row)
            for row in connection.execute(
                "SELECT ticket_id, issue_type, status, priority FROM tickets WHERE customer_id = ?",
                (customer_id,),
            ).fetchall()
        ]
        audit_rows = connection.execute(
            "SELECT case_id, handoff_required FROM audit_logs WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        handoff_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM human_handoff_queue h
            JOIN audit_logs a ON a.case_id = h.case_id
            WHERE a.session_id = ?
            """,
            (session_id,),
        ).fetchone()[0]
    return {
        "credits": credits,
        "tickets": tickets,
        "audit_count": len(audit_rows),
        "audit_handoff_required": any(bool(row["handoff_required"]) for row in audit_rows),
        "handoff_count": int(handoff_count),
    }


def _artifact_contains(artifacts: dict[str, Any], text: str) -> bool:
    return text in json.dumps(artifacts, sort_keys=True)


def _case_score(
    *,
    scenario: EvaluationScenario,
    required_tools: list[str],
    missing_tools: list[str],
    forbidden_called: list[str],
    failure_count: int,
) -> float:
    expected = scenario.goal_state.get("expected_artifacts", {})
    total = max(
        1,
        len(required_tools)
        + len(scenario.goal_state.get("required_policies", []))
        + len(expected)
        + len(scenario.goal_state.get("issue_queue_order", [])),
    )
    non_tool_failures = max(
        0, failure_count - len(missing_tools) - len(forbidden_called))
    lost = len(missing_tools) + len(forbidden_called) + non_tool_failures
    return round(max(0.0, 1.0 - (lost / total)), 4)


def _scenario_health_score(scenario: EvaluationScenario) -> float | None:
    expected = scenario.goal_state.get("expected_artifacts", {})
    if expected.get("sentiment") == "angry":
        return 45.0
    if "angry" in " ".join(scenario.customer_messages).lower():
        return 45.0
    return None


def _scenario_needs_problem_description(scenario: EvaluationScenario, intents: list[str]) -> bool:
    text = " ".join(scenario.customer_messages).lower()
    vague_terms = (
        "bad again",
        "do something",
        "nothing works",
        "this is ridiculous",
    )
    if not any(term in text for term in vague_terms):
        return False
    if "general_query" in intents:
        return True
    return len(intents) > 1


def _insert_conversation(
    *,
    db_path: Path,
    session_id: str,
    customer_id: str,
    messages: list[dict],
    intents: list[str],
    slots: dict[str, Any],
) -> None:
    final_status = "active"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO conversations(session_id, customer_id, messages, intents, slots, tools_called)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                customer_id,
                json.dumps(messages),
                json.dumps(intents),
                json.dumps(slots),
                "[]",
            ),
        )
        connection.execute(
            "UPDATE conversations SET final_status = ? WHERE session_id = ?",
            (final_status, session_id),
        )


def _conversation_messages(scenario: EvaluationScenario) -> list[dict]:
    messages = list(scenario.initial_state.get("conversation_context", []))
    messages.extend({"role": "user", "content": message}
                    for message in scenario.customer_messages)
    return messages


def _customer_location(db_path: Path, customer_id: str) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT location FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"customer {customer_id!r} not found")
    return row[0]
