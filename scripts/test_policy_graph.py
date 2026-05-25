from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import (  # noqa: E402
    PolicyActionBlocked,
    PolicyDAG,
    PolicyGraphValidator,
    PolicyNode,
    assert_action_allowed,
    build_cancellation_retention_dag,
    build_duplicate_charge_refund_dag,
    build_plan_downgrade_dag,
    build_refund_exception_dag,
    build_service_credit_dag,
    build_technician_dispatch_dag,
    cancellation_retention_dag,
    compute_ujcs,
    default_policy_dags,
    duplicate_charge_refund_dag,
    evaluate_policy_condition,
    plan_downgrade_dag,
    refund_exception_dag,
    service_credit_dag,
    technician_dispatch_dag,
)


def assert_policy_node_supports_condition_nodes() -> None:
    node = PolicyNode(
        node_id=" check_outage_verified ",
        description=" Verify outage status ",
        condition_type="tool_check",
        condition_args={"tool": "check_outage_status", "field": "verified"},
        edges={" true ": " check_outage_duration ", "false": "deny_credit"},
    )

    if node.node_id != "check_outage_verified":
        raise AssertionError(f"node_id was not normalized: {node.to_dict()}")
    if node.description != "Verify outage status":
        raise AssertionError(f"description was not normalized: {node.to_dict()}")
    if node.is_leaf:
        raise AssertionError("condition node should not be a leaf")
    if node.next_node("true") != "check_outage_duration":
        raise AssertionError(f"true edge failed: {node.to_dict()}")
    if node.next_node("false") != "deny_credit":
        raise AssertionError(f"false edge failed: {node.to_dict()}")


def assert_policy_node_supports_leaf_actions() -> None:
    node = PolicyNode(
        node_id="apply_partial_credit",
        action="apply_credit",
        args={"max": 100},
    )
    payload = node.to_dict()
    restored = PolicyNode.from_dict(payload)

    if not node.is_leaf:
        raise AssertionError("action node should be a leaf")
    if node.condition_type is not None or node.edges:
        raise AssertionError(f"leaf node should not have condition metadata: {node.to_dict()}")
    if restored != node:
        raise AssertionError(f"from_dict round trip failed: {restored.to_dict()}")


def assert_policy_node_rejects_invalid_shapes() -> None:
    invalid_nodes = [
        {"node_id": "", "action": "apply_credit"},
        {"node_id": "node", "condition_type": "unknown", "edges": {"true": "next"}},
        {"node_id": "node", "condition_type": "tool_check"},
        {"node_id": "node", "edges": {"true": "next"}},
        {"node_id": "node", "condition_type": "tool_check", "edges": {"": "next"}},
        {"node_id": "node", "condition_type": "tool_check", "edges": {"true": ""}},
        {"node_id": "node", "action": "apply_credit", "edges": {"true": "next"}},
        {"node_id": "node", "action": "apply_credit", "condition_type": "tool_check"},
    ]
    for kwargs in invalid_nodes:
        try:
            PolicyNode(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid PolicyNode was accepted: {kwargs}")

    node = PolicyNode(node_id="check_amount", condition_type="threshold_check", edges={"true": "approve"})
    try:
        node.next_node("false")
    except KeyError as exc:
        if "has no edge" not in str(exc):
            raise AssertionError(f"wrong missing-edge error: {exc}")
    else:
        raise AssertionError("missing edge lookup should fail")


def assert_policy_dag_validates_node_graph() -> None:
    start = PolicyNode(node_id="start", condition_type="tool_check", edges={"true": "done"})
    done = PolicyNode(node_id="done", action="apply_credit")
    dag = PolicyDAG(name="sample_dag", start_node="start", nodes={"start": start, "done": done})

    if len(dag) != 2:
        raise AssertionError(f"dag length wrong: {dag.to_dict()}")
    if dag["start"] is not start:
        raise AssertionError("dag lookup failed")
    if [node.node_id for node in dag.leaf_nodes] != ["done"]:
        raise AssertionError(f"leaf nodes wrong: {[node.to_dict() for node in dag.leaf_nodes]}")

    invalid_dags = [
        {"name": "", "start_node": "start", "nodes": {"start": start}},
        {"name": "dag", "start_node": "", "nodes": {"start": start}},
        {"name": "dag", "start_node": "missing", "nodes": {"start": start}},
        {"name": "dag", "start_node": "start", "nodes": {}},
        {"name": "dag", "start_node": "start", "nodes": {"wrong": start}},
        {"name": "dag", "start_node": "start", "nodes": {"start": PolicyNode(node_id="start", condition_type="tool_check", edges={"true": "missing"})}},
    ]
    for kwargs in invalid_dags:
        try:
            PolicyDAG(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid PolicyDAG was accepted: {kwargs}")


def assert_service_credit_dag_has_six_policy_nodes() -> None:
    dag = build_service_credit_dag()
    expected_node_ids = {
        "check_outage_verified",
        "check_outage_duration",
        "check_prior_credit",
        "auto_apply_credit",
        "apply_partial_credit",
        "manual_review_credit",
    }

    if dag.name != "service_credit_dag":
        raise AssertionError(f"wrong DAG name: {dag.to_dict()}")
    if dag.start_node != "check_outage_verified":
        raise AssertionError(f"wrong start node: {dag.to_dict()}")
    if set(dag.nodes) != expected_node_ids or len(dag) != 6:
        raise AssertionError(f"service credit DAG should have exactly 6 nodes: {dag.to_dict()}")
    if service_credit_dag.to_dict() != dag.to_dict():
        raise AssertionError("module-level service_credit_dag should match the builder")

    verified = dag["check_outage_verified"]
    duration = dag["check_outage_duration"]
    prior_credit = dag["check_prior_credit"]
    if verified.condition_args != {"tool": "check_outage_status", "field": "verified"}:
        raise AssertionError(f"outage verification args wrong: {verified.to_dict()}")
    if verified.next_node("true") != "check_outage_duration":
        raise AssertionError(f"verified outage should continue to duration: {verified.to_dict()}")
    if verified.next_node("false") != "manual_review_credit":
        raise AssertionError(f"unverified outage should require review: {verified.to_dict()}")
    if duration.condition_args["field"] != "duration_hours" or duration.condition_args["value"] != 6:
        raise AssertionError(f"duration threshold wrong: {duration.to_dict()}")
    if duration.next_node("true") != "check_prior_credit":
        raise AssertionError(f"long outage should continue to prior credit check: {duration.to_dict()}")
    if duration.next_node("false") != "apply_partial_credit":
        raise AssertionError(f"short outage should lead to partial credit: {duration.to_dict()}")
    if prior_credit.condition_args != {"tool": "get_invoice_history", "field": "credit_this_cycle"}:
        raise AssertionError(f"prior credit args wrong: {prior_credit.to_dict()}")
    if prior_credit.next_node("false") != "auto_apply_credit":
        raise AssertionError(f"no prior credit should auto apply: {prior_credit.to_dict()}")
    if prior_credit.next_node("true") != "manual_review_credit":
        raise AssertionError(f"prior credit should require review: {prior_credit.to_dict()}")

    leaves = {node.node_id: node for node in dag.leaf_nodes}
    if leaves["auto_apply_credit"].action != "apply_credit":
        raise AssertionError(f"auto apply action wrong: {leaves['auto_apply_credit'].to_dict()}")
    if leaves["apply_partial_credit"].args.get("max_amount") != 100:
        raise AssertionError(f"partial credit cap wrong: {leaves['apply_partial_credit'].to_dict()}")
    if leaves["manual_review_credit"].action != "handoff_human":
        raise AssertionError(f"manual review action wrong: {leaves['manual_review_credit'].to_dict()}")


def assert_duplicate_charge_refund_dag_matches_policy_docs() -> None:
    dag = build_duplicate_charge_refund_dag()
    expected_node_ids = {
        "check_duplicate_confirmed",
        "check_invoice_match",
        "check_refund_window",
        "check_duplicate_amount",
        "create_refund_review_ticket",
        "manual_refund_review",
        "deny_duplicate_refund",
    }

    if dag.name != "duplicate_charge_refund_dag":
        raise AssertionError(f"wrong DAG name: {dag.to_dict()}")
    if dag.start_node != "check_duplicate_confirmed":
        raise AssertionError(f"wrong start node: {dag.to_dict()}")
    if set(dag.nodes) != expected_node_ids:
        raise AssertionError(f"duplicate charge DAG nodes wrong: {dag.to_dict()}")
    if duplicate_charge_refund_dag.to_dict() != dag.to_dict():
        raise AssertionError("module-level duplicate_charge_refund_dag should match the builder")

    duplicate_check = dag["check_duplicate_confirmed"]
    invoice_check = dag["check_invoice_match"]
    window_check = dag["check_refund_window"]
    amount_check = dag["check_duplicate_amount"]

    if duplicate_check.condition_args != {"tool": "check_duplicate_charge", "field": "duplicate_confirmed"}:
        raise AssertionError(f"duplicate confirmation args wrong: {duplicate_check.to_dict()}")
    if duplicate_check.next_node("true") != "check_invoice_match":
        raise AssertionError(f"confirmed duplicate should check invoice match: {duplicate_check.to_dict()}")
    if duplicate_check.next_node("false") != "deny_duplicate_refund":
        raise AssertionError(f"unconfirmed duplicate should deny: {duplicate_check.to_dict()}")

    if invoice_check.condition_args != {"tool": "get_invoice_history", "field": "single_matching_invoice"}:
        raise AssertionError(f"invoice match args wrong: {invoice_check.to_dict()}")
    if invoice_check.next_node("true") != "check_refund_window":
        raise AssertionError(f"matching invoice should continue to refund window: {invoice_check.to_dict()}")
    if invoice_check.next_node("false") != "manual_refund_review":
        raise AssertionError(f"invoice mismatch should require review: {invoice_check.to_dict()}")

    if window_check.condition_args != {"field": "payment_age_days", "operator": "<=", "value": 30}:
        raise AssertionError(f"refund window args wrong: {window_check.to_dict()}")
    if window_check.next_node("true") != "check_duplicate_amount":
        raise AssertionError(f"in-window refund should continue to amount cap: {window_check.to_dict()}")
    if window_check.next_node("false") != "manual_refund_review":
        raise AssertionError(f"older refund should require review: {window_check.to_dict()}")

    if amount_check.condition_args != {"field": "duplicate_amount", "operator": "<=", "value": 500}:
        raise AssertionError(f"amount cap args wrong: {amount_check.to_dict()}")
    if amount_check.next_node("true") != "create_refund_review_ticket":
        raise AssertionError(f"small duplicate amount should create review ticket: {amount_check.to_dict()}")
    if amount_check.next_node("false") != "manual_refund_review":
        raise AssertionError(f"large duplicate amount should require review: {amount_check.to_dict()}")

    leaves = {node.node_id: node for node in dag.leaf_nodes}
    if leaves["create_refund_review_ticket"].action != "create_ticket":
        raise AssertionError(f"refund review leaf action wrong: {leaves['create_refund_review_ticket'].to_dict()}")
    if leaves["create_refund_review_ticket"].args.get("allow_account_credit") is not True:
        raise AssertionError(f"refund review should allow account credit validation: {leaves['create_refund_review_ticket'].to_dict()}")
    if leaves["manual_refund_review"].action != "handoff_human":
        raise AssertionError(f"manual review action wrong: {leaves['manual_refund_review'].to_dict()}")
    if leaves["deny_duplicate_refund"].action != "generate_denial_response":
        raise AssertionError(f"deny action wrong: {leaves['deny_duplicate_refund'].to_dict()}")


def assert_remaining_feature4_dags_match_policy_docs() -> None:
    cancellation = build_cancellation_retention_dag()
    technician = build_technician_dispatch_dag()
    downgrade = build_plan_downgrade_dag()
    refund = build_refund_exception_dag()

    if cancellation_retention_dag.to_dict() != cancellation.to_dict():
        raise AssertionError("module-level cancellation_retention_dag should match builder")
    if technician_dispatch_dag.to_dict() != technician.to_dict():
        raise AssertionError("module-level technician_dispatch_dag should match builder")
    if plan_downgrade_dag.to_dict() != downgrade.to_dict():
        raise AssertionError("module-level plan_downgrade_dag should match builder")
    if refund_exception_dag.to_dict() != refund.to_dict():
        raise AssertionError("module-level refund_exception_dag should match builder")

    if cancellation["check_open_issues"].next_node("true") != "create_retention_ticket":
        raise AssertionError(f"open cancellation issue should create retention ticket: {cancellation.to_dict()}")
    if cancellation["check_churn_risk"].condition_args != {"field": "churn_score", "operator": ">=", "value": 0.7}:
        raise AssertionError(f"churn threshold wrong: {cancellation.to_dict()}")

    if technician["check_router_diagnostic"].condition_args != {
        "tool": "run_router_diagnostic",
        "field": "diagnostic_failure",
    }:
        raise AssertionError(f"technician diagnostic args wrong: {technician.to_dict()}")
    if technician["check_appointment_slot"].next_node("true") != "schedule_technician_visit":
        raise AssertionError(f"selected slot should schedule technician: {technician.to_dict()}")

    if downgrade["check_overdue_invoice"].next_node("false") != "check_plan_available":
        raise AssertionError(f"no overdue invoice should continue downgrade: {downgrade.to_dict()}")
    if downgrade["check_promo_lockin"].next_node("true") != "disclose_fee_and_schedule":
        raise AssertionError(f"promo lock-in should disclose fee: {downgrade.to_dict()}")

    if refund["check_refund_reason"].condition_type != "llm_check":
        raise AssertionError(f"refund reason should be an llm_check: {refund.to_dict()}")
    if refund["check_refund_window"].condition_args != {"field": "payment_age_days", "operator": "<=", "value": 30}:
        raise AssertionError(f"refund window wrong: {refund.to_dict()}")
    if refund["check_refund_amount"].next_node("false") != "manual_refund_exception_review":
        raise AssertionError(f"large refund should require human review: {refund.to_dict()}")


def assert_default_policy_dags_are_registered() -> None:
    dags = default_policy_dags()
    expected = {
        "service_credit_dag",
        "duplicate_charge_refund_dag",
        "cancellation_retention_dag",
        "technician_dispatch_dag",
        "plan_downgrade_dag",
        "refund_exception_dag",
    }
    if set(dags) != expected:
        raise AssertionError(f"default DAG registry wrong: {sorted(dags)}")


def assert_policy_condition_evaluation_uses_context() -> None:
    tool_node = PolicyNode(
        node_id="check",
        condition_type="tool_check",
        condition_args={"tool": "lookup_customer", "field": "account_active"},
        edges={"true": "yes", "false": "no"},
    )
    threshold_node = PolicyNode(
        node_id="amount",
        condition_type="threshold_check",
        condition_args={"field": "amount", "operator": "<=", "value": 500},
        edges={"true": "yes", "false": "no"},
    )
    llm_node = PolicyNode(
        node_id="reason",
        condition_type="llm_check",
        condition_args={"field": "refund_reason_eligible"},
        edges={"true": "yes", "false": "no"},
    )
    if evaluate_policy_condition(tool_node, {"lookup_customer": {"account_active": True}}) != "true":
        raise AssertionError("tool_check should read nested tool payload")
    if evaluate_policy_condition(threshold_node, {"amount": 600}) != "false":
        raise AssertionError("threshold_check should compare numeric values")
    if evaluate_policy_condition(llm_node, {"refund_reason_eligible": False}) != "false":
        raise AssertionError("llm_check should use supplied decision field")

    try:
        evaluate_policy_condition(tool_node, {})
    except ValueError as exc:
        if "context missing field" not in str(exc):
            raise AssertionError(f"wrong missing-context error: {exc}")
    else:
        raise AssertionError("missing context should fail condition evaluation")


def assert_policy_graph_validator_runs_traversals_and_ujcs() -> None:
    validator = PolicyGraphValidator()
    result = validator.run(
        "service_credit_dag",
        {
            "check_outage_status": {"verified": True, "duration_hours": 7},
            "get_invoice_history": {"credit_this_cycle": False},
        },
    )
    if result.path != [
        "check_outage_verified",
        "check_outage_duration",
        "check_prior_credit",
        "auto_apply_credit",
    ]:
        raise AssertionError(f"service credit path wrong: {result.to_dict()}")
    if result.action != "apply_credit" or result.action_args.get("max_amount") != 500:
        raise AssertionError(f"service credit action wrong: {result.to_dict()}")
    if result.ujcs != round(4 / 6, 4) or compute_ujcs(result.path, service_credit_dag) != result.ujcs:
        raise AssertionError(f"UJCS wrong: {result.to_dict()}")
    if result.steps[-1].action != "apply_credit":
        raise AssertionError(f"leaf step should record action: {result.to_dict()}")

    duplicate = validator.run(
        "duplicate_charge_refund_dag",
        {
            "check_duplicate_charge": {"duplicate_confirmed": True},
            "get_invoice_history": {"single_matching_invoice": True},
            "payment_age_days": 12,
            "duplicate_amount": 499,
        },
    )
    if duplicate.action != "create_ticket":
        raise AssertionError(f"duplicate DAG should create ticket: {duplicate.to_dict()}")

    technician = validator.run(
        "technician_dispatch_dag",
        {
            "lookup_customer": {"account_active": True},
            "run_router_diagnostic": {"diagnostic_failure": True},
            "check_outage_status": {"outage_cleared": True},
            "appointment_slot_selected": True,
        },
    )
    if technician.action != "schedule_technician":
        raise AssertionError(f"technician DAG should schedule: {technician.to_dict()}")

    downgrade = validator.run(
        "plan_downgrade_dag",
        {
            "lookup_customer": {"account_active": True},
            "has_overdue_invoice": False,
            "requested_plan_available": True,
            "price_speed_confirmed": True,
            "promo_lockin": True,
        },
    )
    if downgrade.action != "change_plan" or not downgrade.action_args.get("fee_disclosure_required"):
        raise AssertionError(f"plan downgrade fee disclosure path wrong: {downgrade.to_dict()}")

    refund = validator.run(
        "refund_exception_dag",
        {
            "refund_reason_eligible": True,
            "payment_ownership_verified": True,
            "payment_age_days": 45,
            "refund_amount": 300,
        },
    )
    if refund.action != "handoff_human":
        raise AssertionError(f"old refund should handoff: {refund.to_dict()}")


def assert_policy_graph_blocks_actions_without_prerequisites() -> None:
    validator = PolicyGraphValidator()
    allowed = validator.authorize_action(
        "service_credit_dag",
        "apply_credit",
        {
            "check_outage_status": {"verified": True, "duration_hours": 8},
            "get_invoice_history": {"credit_this_cycle": False},
        },
    )
    if allowed.action != "apply_credit":
        raise AssertionError(f"valid action should be allowed: {allowed.to_dict()}")

    blocked_context = {
        "check_outage_status": {"verified": False, "duration_hours": 8},
        "get_invoice_history": {"credit_this_cycle": False},
    }
    try:
        validator.authorize_action("service_credit_dag", "apply_credit", blocked_context)
    except PolicyActionBlocked as exc:
        if "blocked" not in str(exc):
            raise AssertionError(f"wrong blocked-action error: {exc}")
    else:
        raise AssertionError("apply_credit should be blocked when outage verification prerequisite fails")

    try:
        assert_action_allowed("refund_exception_dag", "create_ticket", {"refund_reason_eligible": False})
    except PolicyActionBlocked:
        pass
    else:
        raise AssertionError("refund ticket should be blocked when eligibility prerequisite fails")


def main() -> None:
    assert_policy_node_supports_condition_nodes()
    assert_policy_node_supports_leaf_actions()
    assert_policy_node_rejects_invalid_shapes()
    assert_policy_dag_validates_node_graph()
    assert_service_credit_dag_has_six_policy_nodes()
    assert_duplicate_charge_refund_dag_matches_policy_docs()
    assert_remaining_feature4_dags_match_policy_docs()
    assert_default_policy_dags_are_registered()
    assert_policy_condition_evaluation_uses_context()
    assert_policy_graph_validator_runs_traversals_and_ujcs()
    assert_policy_graph_blocks_actions_without_prerequisites()
    print("policy graph node tests passed")


if __name__ == "__main__":
    main()
