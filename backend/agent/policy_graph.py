from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


CONDITION_TYPES = ("tool_check", "llm_check", "threshold_check")
THRESHOLD_OPERATORS = ("<", "<=", "==", "!=", ">=", ">")


class PolicyActionBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class PolicyNode:
    node_id: str
    description: str = ""
    condition_type: str | None = None
    condition_args: dict[str, Any] = field(default_factory=dict)
    edges: dict[str, str] = field(default_factory=dict)
    action: str | None = None
    args: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        node_id = self.node_id.strip()
        description = self.description.strip()
        condition_type = self.condition_type.strip() if isinstance(
            self.condition_type, str) else self.condition_type
        action = self.action.strip() if isinstance(self.action, str) else self.action
        condition_args = dict(self.condition_args or {})
        edges = {str(result).strip(): str(target).strip()
                 for result, target in dict(self.edges or {}).items()}
        args = dict(self.args or {})

        if not node_id:
            raise ValueError("node_id must not be empty")
        if condition_type is not None and condition_type not in CONDITION_TYPES:
            raise ValueError(
                f"condition_type must be one of {CONDITION_TYPES}")
        if action is not None and not action:
            raise ValueError("action must not be empty when provided")
        if any(not result or not target for result, target in edges.items()):
            raise ValueError(
                "edges must map non-empty condition results to non-empty node IDs")

        is_leaf = action is not None
        if is_leaf and (condition_type is not None or edges):
            raise ValueError(
                "leaf action nodes must not define condition_type or edges")
        if not is_leaf and condition_type is None:
            raise ValueError("non-leaf nodes must define condition_type")
        if not is_leaf and not edges:
            raise ValueError("non-leaf nodes must define at least one edge")

        object.__setattr__(self, "node_id", node_id)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "condition_type", condition_type)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "condition_args", condition_args)
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "args", args)

    @property
    def is_leaf(self) -> bool:
        return self.action is not None

    def next_node(self, condition_result: str) -> str:
        result = condition_result.strip()
        try:
            return self.edges[result]
        except KeyError as exc:
            raise KeyError(
                f"node {self.node_id} has no edge for result {result!r}") from exc

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> PolicyNode:
        return cls(
            node_id=str(payload.get("node_id", "")),
            description=str(payload.get("description", "")),
            condition_type=payload.get("condition_type"),
            condition_args=dict(payload.get("condition_args") or {}),
            edges=dict(payload.get("edges") or {}),
            action=payload.get("action"),
            args=dict(payload.get("args") or {}),
        )


@dataclass(frozen=True)
class PolicyDAG:
    name: str
    start_node: str
    nodes: dict[str, PolicyNode]

    def __post_init__(self) -> None:
        name = self.name.strip()
        start_node = self.start_node.strip()
        nodes = dict(self.nodes or {})

        if not name:
            raise ValueError("name must not be empty")
        if not start_node:
            raise ValueError("start_node must not be empty")
        if not nodes:
            raise ValueError("nodes must not be empty")
        if start_node not in nodes:
            raise ValueError(
                f"start_node {start_node!r} is not present in nodes")

        for node_id, node in nodes.items():
            if node_id != node.node_id:
                raise ValueError(
                    f"node map key {node_id!r} must match PolicyNode.node_id {node.node_id!r}")
            for target in node.edges.values():
                if target not in nodes:
                    raise ValueError(
                        f"node {node.node_id} points to missing node {target!r}")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "start_node", start_node)
        object.__setattr__(self, "nodes", nodes)

    def __getitem__(self, node_id: str) -> PolicyNode:
        return self.nodes[node_id]

    def __len__(self) -> int:
        return len(self.nodes)

    @property
    def leaf_nodes(self) -> list[PolicyNode]:
        return [node for node in self.nodes.values() if node.is_leaf]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "start_node": self.start_node,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
        }


@dataclass(frozen=True)
class PolicyTraversalStep:
    node_id: str
    condition_type: str | None
    condition_result: str | None
    next_node: str | None
    action: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PolicyValidationResult:
    policy_name: str
    path: list[str]
    steps: list[PolicyTraversalStep]
    action: str
    action_args: dict[str, Any]
    ujcs: float
    completed: bool
    blocked_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "policy_name": self.policy_name,
            "path": self.path,
            "steps": [step.to_dict() for step in self.steps],
            "action": self.action,
            "action_args": self.action_args,
            "ujcs": self.ujcs,
            "completed": self.completed,
            "blocked_reason": self.blocked_reason,
        }


class PolicyGraphValidator:
    """Dynamic-prompt action validator backed by deterministic policy DAG traversal."""

    def __init__(self, dags: dict[str, PolicyDAG] | None = None) -> None:
        self.dags = dict(dags or default_policy_dags())

    def run(self, policy_name: str, context: dict[str, Any]) -> PolicyValidationResult:
        policy_name = policy_name.strip()
        if not policy_name:
            raise ValueError("policy_name must not be empty")
        if policy_name not in self.dags:
            raise ValueError(f"unknown policy DAG: {policy_name}")
        if not isinstance(context, dict):
            raise ValueError("context must be a dict")

        dag = self.dags[policy_name]
        current_node_id = dag.start_node
        path = [current_node_id]
        steps: list[PolicyTraversalStep] = []
        visited = set()

        while True:
            if current_node_id in visited:
                raise ValueError(
                    f"cycle detected at policy node {current_node_id}")
            visited.add(current_node_id)
            node = dag[current_node_id]

            if node.is_leaf:
                steps.append(
                    PolicyTraversalStep(
                        node_id=node.node_id,
                        condition_type=None,
                        condition_result=None,
                        next_node=None,
                        action=node.action,
                    )
                )
                return PolicyValidationResult(
                    policy_name=dag.name,
                    path=path,
                    steps=steps,
                    action=node.action or "",
                    action_args=node.args,
                    ujcs=compute_ujcs(path, dag),
                    completed=True,
                )

            condition_result = evaluate_policy_condition(node, context)
            next_node = node.next_node(condition_result)
            steps.append(
                PolicyTraversalStep(
                    node_id=node.node_id,
                    condition_type=node.condition_type,
                    condition_result=condition_result,
                    next_node=next_node,
                    action=None,
                )
            )
            current_node_id = next_node
            path.append(current_node_id)

    def authorize_action(self, policy_name: str, action: str, context: dict[str, Any]) -> PolicyValidationResult:
        action = action.strip()
        if not action:
            raise ValueError("action must not be empty")

        result = self.run(policy_name, context)
        if result.action != action:
            raise PolicyActionBlocked(
                f"action {action!r} blocked by {policy_name}; DAG reached {result.action!r} via path {result.path}"
            )
        return result


def build_service_credit_dag() -> PolicyDAG:
    nodes = {
        "check_outage_verified": PolicyNode(
            node_id="check_outage_verified",
            description="Confirm that the reported outage is verified for the customer's location.",
            condition_type="tool_check",
            condition_args={
                "tool": "check_outage_status", "field": "verified"},
            edges={"true": "check_outage_duration",
                   "false": "manual_review_credit"},
        ),
        "check_outage_duration": PolicyNode(
            node_id="check_outage_duration",
            description="Confirm that the verified outage crossed the automatic-credit duration threshold.",
            condition_type="threshold_check",
            condition_args={
                "tool": "check_outage_status",
                "field": "duration_hours",
                "operator": ">=",
                "value": 6,
            },
            edges={"true": "check_prior_credit",
                   "false": "apply_partial_credit"},
        ),
        "check_prior_credit": PolicyNode(
            node_id="check_prior_credit",
            description="Check whether a service credit was already applied in the current billing cycle.",
            condition_type="tool_check",
            condition_args={"tool": "get_invoice_history",
                            "field": "credit_this_cycle"},
            edges={"false": "auto_apply_credit",
                   "true": "manual_review_credit"},
        ),
        "auto_apply_credit": PolicyNode(
            node_id="auto_apply_credit",
            description="Apply the standard outage service credit.",
            action="apply_credit",
            args={"credit_type": "service_outage", "max_amount": 500},
        ),
        "apply_partial_credit": PolicyNode(
            node_id="apply_partial_credit",
            description="Apply a capped goodwill credit for a verified short outage.",
            action="apply_credit",
            args={"credit_type": "short_outage_goodwill", "max_amount": 100},
        ),
        "manual_review_credit": PolicyNode(
            node_id="manual_review_credit",
            description="Escalate credit requests that fail verification or already have credit this cycle.",
            action="handoff_human",
            args={"reason": "service_credit_manual_review"},
        ),
    }
    return PolicyDAG(
        name="service_credit_dag",
        start_node="check_outage_verified",
        nodes=nodes,
    )


def build_duplicate_charge_refund_dag() -> PolicyDAG:
    nodes = {
        "check_duplicate_confirmed": PolicyNode(
            node_id="check_duplicate_confirmed",
            description="Confirm duplicate payment evidence using billing records, not only the customer statement.",
            condition_type="tool_check",
            condition_args={"tool": "check_duplicate_charge",
                            "field": "duplicate_confirmed"},
            edges={"true": "check_invoice_match",
                   "false": "deny_duplicate_refund"},
        ),
        "check_invoice_match": PolicyNode(
            node_id="check_invoice_match",
            description="Confirm the duplicate payment maps to one matching invoice for the billing period.",
            condition_type="tool_check",
            condition_args={"tool": "get_invoice_history",
                            "field": "single_matching_invoice"},
            edges={"true": "check_refund_window",
                   "false": "manual_refund_review"},
        ),
        "check_refund_window": PolicyNode(
            node_id="check_refund_window",
            description="Confirm the refund request was raised within the 30-day refund window.",
            condition_type="threshold_check",
            condition_args={"field": "payment_age_days",
                            "operator": "<=", "value": 30},
            edges={"true": "check_duplicate_amount",
                   "false": "manual_refund_review"},
        ),
        "check_duplicate_amount": PolicyNode(
            node_id="check_duplicate_amount",
            description="Confirm the duplicate amount is within the automatic account-credit limit.",
            condition_type="threshold_check",
            condition_args={"field": "duplicate_amount",
                            "operator": "<=", "value": 500},
            edges={"true": "create_refund_review_ticket",
                   "false": "manual_refund_review"},
        ),
        "create_refund_review_ticket": PolicyNode(
            node_id="create_refund_review_ticket",
            description="Create a billing ticket for duplicate-charge refund review and allow account credit validation.",
            action="create_ticket",
            args={"ticket_type": "duplicate_charge_refund_review",
                  "allow_account_credit": True},
        ),
        "manual_refund_review": PolicyNode(
            node_id="manual_refund_review",
            description="Escalate duplicate-charge refund cases that need human review.",
            action="handoff_human",
            args={"reason": "duplicate_charge_refund_manual_review"},
        ),
        "deny_duplicate_refund": PolicyNode(
            node_id="deny_duplicate_refund",
            description="Deny duplicate refund flow when duplicate payment evidence is not confirmed.",
            action="generate_denial_response",
            args={"reason": "duplicate_charge_not_confirmed"},
        ),
    }
    return PolicyDAG(
        name="duplicate_charge_refund_dag",
        start_node="check_duplicate_confirmed",
        nodes=nodes,
    )


def build_cancellation_retention_dag() -> PolicyDAG:
    nodes = {
        "check_identity_status": PolicyNode(
            node_id="check_identity_status",
            description="Confirm customer identity and active account status before cancellation guidance.",
            condition_type="tool_check",
            condition_args={"tool": "lookup_customer",
                            "field": "identity_verified"},
            edges={"true": "check_open_issues", "false": "handoff_retention"},
        ),
        "check_open_issues": PolicyNode(
            node_id="check_open_issues",
            description="Check unresolved service issues, billing disputes, or outage-linked complaints.",
            condition_type="tool_check",
            condition_args={"field": "has_open_issue"},
            edges={"true": "create_retention_ticket",
                   "false": "check_churn_risk"},
        ),
        "check_churn_risk": PolicyNode(
            node_id="check_churn_risk",
            description="Escalate high-risk cancellation customers to retention.",
            condition_type="threshold_check",
            condition_args={"field": "churn_score",
                            "operator": ">=", "value": 0.7},
            edges={"true": "handoff_retention",
                   "false": "explain_cancellation_steps"},
        ),
        "create_retention_ticket": PolicyNode(
            node_id="create_retention_ticket",
            description="Create a retention ticket when cancellation intent is tied to unresolved issues.",
            action="create_ticket",
            args={"ticket_type": "retention_unresolved_issue"},
        ),
        "handoff_retention": PolicyNode(
            node_id="handoff_retention",
            description="Handoff cancellation request to a retention specialist.",
            action="handoff_human",
            args={"reason": "cancellation_retention_required"},
        ),
        "explain_cancellation_steps": PolicyNode(
            node_id="explain_cancellation_steps",
            description="Explain cancellation steps without completing cancellation automatically.",
            action="generate_cancellation_guidance",
            args={"complete_cancellation": False},
        ),
    }
    return PolicyDAG(
        name="cancellation_retention_dag",
        start_node="check_identity_status",
        nodes=nodes,
    )


def build_technician_dispatch_dag() -> PolicyDAG:
    nodes = {
        "check_account_active": PolicyNode(
            node_id="check_account_active",
            description="Confirm the customer account can receive field support.",
            condition_type="tool_check",
            condition_args={"tool": "lookup_customer",
                            "field": "account_active"},
            edges={"true": "check_router_diagnostic",
                   "false": "manual_dispatch_review"},
        ),
        "check_router_diagnostic": PolicyNode(
            node_id="check_router_diagnostic",
            description="Confirm diagnostic failure before offering technician dispatch.",
            condition_type="tool_check",
            condition_args={"tool": "run_router_diagnostic",
                            "field": "diagnostic_failure"},
            edges={"true": "check_outage_cleared",
                   "false": "manual_dispatch_review"},
        ),
        "check_outage_cleared": PolicyNode(
            node_id="check_outage_cleared",
            description="Avoid dispatch while an active outage is still unresolved.",
            condition_type="tool_check",
            condition_args={"tool": "check_outage_status",
                            "field": "outage_cleared"},
            edges={"true": "check_appointment_slot",
                   "false": "manual_dispatch_review"},
        ),
        "check_appointment_slot": PolicyNode(
            node_id="check_appointment_slot",
            description="Confirm the customer selected an available appointment slot.",
            condition_type="tool_check",
            condition_args={"field": "appointment_slot_selected"},
            edges={"true": "schedule_technician_visit",
                   "false": "manual_dispatch_review"},
        ),
        "schedule_technician_visit": PolicyNode(
            node_id="schedule_technician_visit",
            description="Schedule the technician visit and link or create a field-support ticket.",
            action="schedule_technician",
            args={"requires_ticket": True},
        ),
        "manual_dispatch_review": PolicyNode(
            node_id="manual_dispatch_review",
            description="Escalate technician dispatch requests that fail required checks.",
            action="handoff_human",
            args={"reason": "technician_dispatch_manual_review"},
        ),
    }
    return PolicyDAG(
        name="technician_dispatch_dag",
        start_node="check_account_active",
        nodes=nodes,
    )


def build_plan_downgrade_dag() -> PolicyDAG:
    nodes = {
        "check_account_active": PolicyNode(
            node_id="check_account_active",
            description="Confirm the account is active before plan downgrade processing.",
            condition_type="tool_check",
            condition_args={"tool": "lookup_customer",
                            "field": "account_active"},
            edges={"true": "check_overdue_invoice",
                   "false": "manual_plan_review"},
        ),
        "check_overdue_invoice": PolicyNode(
            node_id="check_overdue_invoice",
            description="Confirm there is no unpaid overdue invoice.",
            condition_type="tool_check",
            condition_args={"field": "has_overdue_invoice"},
            edges={"false": "check_plan_available",
                   "true": "manual_plan_review"},
        ),
        "check_plan_available": PolicyNode(
            node_id="check_plan_available",
            description="Confirm the requested plan is available at the customer's service location.",
            condition_type="tool_check",
            condition_args={"field": "requested_plan_available"},
            edges={"true": "check_price_speed_confirmed",
                   "false": "manual_plan_review"},
        ),
        "check_price_speed_confirmed": PolicyNode(
            node_id="check_price_speed_confirmed",
            description="Confirm the customer accepted the new monthly price and speed.",
            condition_type="tool_check",
            condition_args={"field": "price_speed_confirmed"},
            edges={"true": "check_promo_lockin",
                   "false": "manual_plan_review"},
        ),
        "check_promo_lockin": PolicyNode(
            node_id="check_promo_lockin",
            description="Check whether downgrade fee disclosure is required for promotional lock-in.",
            condition_type="tool_check",
            condition_args={"field": "promo_lockin"},
            edges={"false": "schedule_plan_downgrade",
                   "true": "disclose_fee_and_schedule"},
        ),
        "schedule_plan_downgrade": PolicyNode(
            node_id="schedule_plan_downgrade",
            description="Schedule the downgrade for the next billing cycle.",
            action="change_plan",
            args={"change_type": "downgrade",
                  "effective": "next_billing_cycle"},
        ),
        "disclose_fee_and_schedule": PolicyNode(
            node_id="disclose_fee_and_schedule",
            description="Disclose lock-in fee before scheduling downgrade for next billing cycle.",
            action="change_plan",
            args={"change_type": "downgrade", "effective": "next_billing_cycle",
                  "fee_disclosure_required": True},
        ),
        "manual_plan_review": PolicyNode(
            node_id="manual_plan_review",
            description="Escalate downgrade requests with account, invoice, plan, or confirmation blockers.",
            action="handoff_human",
            args={"reason": "plan_downgrade_manual_review"},
        ),
    }
    return PolicyDAG(
        name="plan_downgrade_dag",
        start_node="check_account_active",
        nodes=nodes,
    )


def build_refund_exception_dag() -> PolicyDAG:
    nodes = {
        "check_refund_reason": PolicyNode(
            node_id="check_refund_reason",
            description="Confirm the refund reason is eligible for review.",
            condition_type="llm_check",
            condition_args={"field": "refund_reason_eligible"},
            edges={"true": "check_payment_ownership",
                   "false": "deny_refund_exception"},
        ),
        "check_payment_ownership": PolicyNode(
            node_id="check_payment_ownership",
            description="Confirm ownership of the payment and customer account.",
            condition_type="tool_check",
            condition_args={"field": "payment_ownership_verified"},
            edges={"true": "check_refund_window",
                   "false": "manual_refund_exception_review"},
        ),
        "check_refund_window": PolicyNode(
            node_id="check_refund_window",
            description="Confirm the payment is inside the 30-day refund window.",
            condition_type="threshold_check",
            condition_args={"field": "payment_age_days",
                            "operator": "<=", "value": 30},
            edges={"true": "check_refund_amount",
                   "false": "manual_refund_exception_review"},
        ),
        "check_refund_amount": PolicyNode(
            node_id="check_refund_amount",
            description="Confirm the refund amount is within the automatic review limit.",
            condition_type="threshold_check",
            condition_args={"field": "refund_amount",
                            "operator": "<=", "value": 500},
            edges={"true": "create_refund_review_ticket",
                   "false": "manual_refund_exception_review"},
        ),
        "create_refund_review_ticket": PolicyNode(
            node_id="create_refund_review_ticket",
            description="Create a refund-review ticket for policy-compliant refund requests.",
            action="create_ticket",
            args={"ticket_type": "refund_review"},
        ),
        "manual_refund_exception_review": PolicyNode(
            node_id="manual_refund_exception_review",
            description="Escalate refund requests that exceed automatic handling rules.",
            action="handoff_human",
            args={"reason": "refund_exception_manual_review"},
        ),
        "deny_refund_exception": PolicyNode(
            node_id="deny_refund_exception",
            description="Deny refund flow when the refund reason is not policy eligible.",
            action="generate_denial_response",
            args={"reason": "refund_reason_not_eligible"},
        ),
    }
    return PolicyDAG(
        name="refund_exception_dag",
        start_node="check_refund_reason",
        nodes=nodes,
    )


def default_policy_dags() -> dict[str, PolicyDAG]:
    return {
        "service_credit_dag": service_credit_dag,
        "duplicate_charge_refund_dag": duplicate_charge_refund_dag,
        "cancellation_retention_dag": cancellation_retention_dag,
        "technician_dispatch_dag": technician_dispatch_dag,
        "plan_downgrade_dag": plan_downgrade_dag,
        "refund_exception_dag": refund_exception_dag,
    }


def evaluate_policy_condition(node: PolicyNode, context: dict[str, Any]) -> str:
    if node.condition_type == "threshold_check":
        return _bool_result(_evaluate_threshold(node, context))
    if node.condition_type in {"tool_check", "llm_check"}:
        value = _condition_value(node, context)
        expected = node.condition_args.get("expected", True)
        if isinstance(value, str) and value.strip().lower() in node.edges:
            return value.strip().lower()
        return _bool_result(value == expected if "expected" in node.condition_args else bool(value))
    raise ValueError(
        f"node {node.node_id} has unsupported condition_type {node.condition_type!r}")


def compute_ujcs(path: list[str], dag: PolicyDAG) -> float:
    if not path:
        return 0.0
    visited = len(set(path) & set(dag.nodes))
    return round(visited / max(len(dag), 1), 4)


def assert_action_allowed(policy_name: str, action: str, context: dict[str, Any]) -> PolicyValidationResult:
    return PolicyGraphValidator().authorize_action(policy_name, action, context)


def _evaluate_threshold(node: PolicyNode, context: dict[str, Any]) -> bool:
    args = node.condition_args
    operator = str(args.get("operator", "")).strip()
    if operator not in THRESHOLD_OPERATORS:
        raise ValueError(
            f"threshold node {node.node_id} has unsupported operator {operator!r}")
    left = _condition_value(node, context)
    right = args.get("value")
    try:
        left_value = float(left)
        right_value = float(right)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"threshold node {node.node_id} values must be numeric") from exc

    if operator == "<":
        return left_value < right_value
    if operator == "<=":
        return left_value <= right_value
    if operator == "==":
        return left_value == right_value
    if operator == "!=":
        return left_value != right_value
    if operator == ">=":
        return left_value >= right_value
    if operator == ">":
        return left_value > right_value
    raise ValueError(
        f"threshold node {node.node_id} has unsupported operator {operator!r}")


def _condition_value(node: PolicyNode, context: dict[str, Any]) -> Any:
    field_name = str(node.condition_args.get("field", "")).strip()
    if not field_name:
        raise ValueError(
            f"node {node.node_id} condition_args must include field")
    tool_name = str(node.condition_args.get("tool", "")).strip()
    if tool_name:
        tool_payload = context.get(tool_name)
        if isinstance(tool_payload, dict) and field_name in tool_payload:
            return tool_payload[field_name]
    if field_name in context:
        return context[field_name]
    raise ValueError(
        f"context missing field {field_name!r} for node {node.node_id}")


def _bool_result(value: bool) -> str:
    return "true" if value else "false"


service_credit_dag = build_service_credit_dag()
duplicate_charge_refund_dag = build_duplicate_charge_refund_dag()
cancellation_retention_dag = build_cancellation_retention_dag()
technician_dispatch_dag = build_technician_dispatch_dag()
plan_downgrade_dag = build_plan_downgrade_dag()
refund_exception_dag = build_refund_exception_dag()
