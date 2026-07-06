from __future__ import annotations

from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.memory_graph import (
    add_synonymy_edges,
    cosine_similarity,
    get_memory_graph_node,
    initialize_memory_graph,
    list_memory_graph_nodes,
    node_id_for_label,
    ppr_retrieve,
    update_memory_graph,
)
from backend.agent.openie import OpenIETriple


def make_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def assert_creates_memory_graph_table() -> None:
    connection = make_connection()
    initialize_memory_graph(connection)

    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memory_graph'"
    ).fetchone()
    if table is None:
        raise AssertionError("memory_graph table was not created")

    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(memory_graph)").fetchall()
    }
    expected_columns = {
        "customer_id",
        "node_id",
        "node_type",
        "label",
        "passages",
        "edges",
        "created_at",
        "updated_at",
    }
    if columns != expected_columns:
        raise AssertionError(f"unexpected memory_graph columns: {columns}")


def assert_upserts_nodes_and_edges() -> None:
    connection = make_connection()
    triples = [
        OpenIETriple(
            subject="CUST-1001",
            relation="was_charged_for",
            object="invoice INV-8821",
            confidence=0.92,
            evidence="CUST-1001 was charged twice for invoice INV-8821",
        ),
        OpenIETriple(
            subject="internet",
            relation="was_down_in",
            object="Chennai Zone 04",
            confidence=0.93,
            evidence="internet was down yesterday in Chennai Zone 04",
        ),
    ]

    update = update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-001",
        triples=triples,
    )
    if update.nodes_upserted != 4 or update.edges_upserted != 2:
        raise AssertionError(f"unexpected update counts: {update.to_dict()}")

    nodes = list_memory_graph_nodes(connection, "CUST-1001")
    if len(nodes) != 4:
        raise AssertionError(f"expected 4 graph nodes, got {nodes}")

    customer_node = get_memory_graph_node(connection, "CUST-1001", "cust_1001")
    if customer_node is None:
        raise AssertionError("customer node missing")
    if customer_node["passages"] != ["mem-001"]:
        raise AssertionError(f"customer node passages wrong: {customer_node}")
    if customer_node["edges"] != [
        {
            "evidence": ["CUST-1001 was charged twice for invoice INV-8821"],
            "passages": ["mem-001"],
            "relation": "was_charged_for",
            "target_node": "invoice_inv_8821",
            "weight": 0.92,
        }
    ]:
        raise AssertionError(f"customer node edges wrong: {customer_node['edges']}")


def assert_updates_are_idempotent_and_merge_passages() -> None:
    connection = make_connection()
    triple = {
        "subject": "CUST-1001",
        "relation": "was charged for",
        "object": "invoice INV-8821",
        "confidence": 0.7,
        "evidence": "first evidence",
    }

    update_memory_graph(connection, customer_id="CUST-1001", memory_id="mem-001", triples=[triple])
    update_memory_graph(connection, customer_id="CUST-1001", memory_id="mem-001", triples=[triple])
    update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-002",
        triples=[
            {
                **triple,
                "confidence": 0.95,
                "evidence": "second evidence",
            }
        ],
    )

    customer_node = get_memory_graph_node(connection, "CUST-1001", "cust_1001")
    if customer_node is None:
        raise AssertionError("customer node missing after idempotent update")

    if customer_node["passages"] != ["mem-001", "mem-002"]:
        raise AssertionError(f"passages should be deduped and merged: {customer_node['passages']}")
    if len(customer_node["edges"]) != 1:
        raise AssertionError(f"edge should be deduped: {customer_node['edges']}")

    edge = customer_node["edges"][0]
    if edge["passages"] != ["mem-001", "mem-002"]:
        raise AssertionError(f"edge passages should be merged: {edge}")
    if edge["evidence"] != ["first evidence", "second evidence"]:
        raise AssertionError(f"edge evidence should be merged: {edge}")
    if edge["weight"] != 0.95:
        raise AssertionError(f"edge weight should retain max confidence: {edge}")


def assert_adds_synonymy_edges_at_threshold() -> None:
    connection = make_connection()
    update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-001",
        triples=[
            {
                "subject": "duplicate charge",
                "relation": "matches",
                "object": "billing dispute",
                "confidence": 0.8,
                "evidence": "duplicate charge billing dispute",
            },
            {
                "subject": "double billing",
                "relation": "reported_by",
                "object": "CUST-1001",
                "confidence": 0.8,
                "evidence": "double billing reported",
            },
            {
                "subject": "service outage",
                "relation": "affected",
                "object": "internet",
                "confidence": 0.8,
                "evidence": "service outage affected internet",
            },
        ],
    )

    vectors = {
        "duplicate charge": [1.0, 0.0, 0.0],
        "double billing": [0.9, 0.1, 0.0],
        "billing dispute": [0.7, 0.2, 0.0],
        "service outage": [0.0, 1.0, 0.0],
        "internet": [0.0, 0.0, 1.0],
        "CUST-1001": [0.0, -1.0, 0.0],
    }

    def fake_embeddings(labels: list[str]) -> list[list[float]]:
        return [vectors[label] for label in labels]

    update = add_synonymy_edges(
        connection,
        customer_id="CUST-1001",
        threshold=0.8,
        embedding_function=fake_embeddings,
    )
    if update.nodes_considered != 6 or update.edges_upserted != 6:
        raise AssertionError(f"unexpected synonymy update counts: {update.to_dict()}")

    duplicate_node = get_memory_graph_node(connection, "CUST-1001", "duplicate_charge")
    if duplicate_node is None:
        raise AssertionError("duplicate charge node missing")

    synonym_targets = {
        edge["target_node"]: edge
        for edge in duplicate_node["edges"]
        if edge["relation"] == "synonymy"
    }
    if set(synonym_targets) != {"billing_dispute", "double_billing"}:
        raise AssertionError(f"synonymy targets wrong: {synonym_targets}")

    reverse_node = get_memory_graph_node(connection, "CUST-1001", "double_billing")
    if reverse_node is None:
        raise AssertionError("double billing node missing")
    if not any(edge["relation"] == "synonymy" and edge["target_node"] == "duplicate_charge" for edge in reverse_node["edges"]):
        raise AssertionError(f"reverse synonymy edge missing: {reverse_node['edges']}")

    add_synonymy_edges(
        connection,
        customer_id="CUST-1001",
        threshold=0.8,
        embedding_function=fake_embeddings,
    )
    duplicate_node_after_retry = get_memory_graph_node(connection, "CUST-1001", "duplicate_charge")
    retry_synonymy = [
        edge
        for edge in duplicate_node_after_retry["edges"]
        if edge["relation"] == "synonymy" and edge["target_node"] == "double_billing"
    ]
    if len(retry_synonymy) != 1:
        raise AssertionError(f"synonymy edge should be idempotent: {retry_synonymy}")


def assert_synonymy_edges_can_be_scoped_to_touched_nodes() -> None:
    connection = make_connection()
    update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-001",
        triples=[
            {
                "subject": "duplicate charge",
                "relation": "matches",
                "object": "billing dispute",
                "confidence": 0.8,
                "evidence": "duplicate charge billing dispute",
            },
            {
                "subject": "plan downgrade",
                "relation": "resembles",
                "object": "plan change",
                "confidence": 0.8,
                "evidence": "plan downgrade plan change",
            },
        ],
    )

    vectors = {
        "duplicate charge": [1.0, 0.0],
        "billing dispute": [0.95, 0.05],
        "plan downgrade": [0.0, 1.0],
        "plan change": [0.0, 0.95],
    }

    def fake_embeddings(labels: list[str]) -> list[list[float]]:
        return [vectors[label] for label in labels]

    update = add_synonymy_edges(
        connection,
        customer_id="CUST-1001",
        threshold=0.8,
        embedding_function=fake_embeddings,
        candidate_node_ids=["duplicate_charge"],
    )
    if update.edges_upserted != 2:
        raise AssertionError(f"only candidate-to-neighbor edges should be upserted: {update.to_dict()}")

    duplicate_node = get_memory_graph_node(connection, "CUST-1001", "duplicate_charge")
    if duplicate_node is None:
        raise AssertionError("duplicate charge node missing")
    if not any(edge["relation"] == "synonymy" and edge["target_node"] == "billing_dispute" for edge in duplicate_node["edges"]):
        raise AssertionError(f"candidate synonymy edge missing: {duplicate_node['edges']}")

    plan_node = get_memory_graph_node(connection, "CUST-1001", "plan_downgrade")
    if plan_node is None:
        raise AssertionError("plan downgrade node missing")
    if any(edge["relation"] == "synonymy" and edge["target_node"] == "plan_change" for edge in plan_node["edges"]):
        raise AssertionError(f"untouched historical nodes should not be rescored together: {plan_node['edges']}")


def assert_cosine_similarity_math() -> None:
    if round(cosine_similarity([1, 0], [1, 0]), 4) != 1.0:
        raise AssertionError("identical vectors should have similarity 1")
    if round(cosine_similarity([1, 0], [0, 1]), 4) != 0.0:
        raise AssertionError("orthogonal vectors should have similarity 0")
    if cosine_similarity([0, 0], [1, 1]) != 0.0:
        raise AssertionError("zero vector similarity should be 0")

    try:
        cosine_similarity([1], [1, 2])
    except ValueError:
        pass
    else:
        raise AssertionError("dimension mismatch was accepted")


def assert_ppr_retrieves_multi_hop_passages() -> None:
    connection = make_connection()
    update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-rahul-outage",
        triples=[
            {
                "subject": "Rahul",
                "relation": "reported",
                "object": "outage",
                "confidence": 0.9,
                "evidence": "Rahul reported outage",
            }
        ],
    )
    update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-outage-credit",
        triples=[
            {
                "subject": "outage",
                "relation": "qualified_for",
                "object": "service credit",
                "confidence": 0.9,
                "evidence": "outage qualified for service credit",
            }
        ],
    )
    update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-router",
        triples=[
            {
                "subject": "router",
                "relation": "had",
                "object": "weak signal",
                "confidence": 0.9,
                "evidence": "router had weak signal",
            }
        ],
    )

    no_walk_results = ppr_retrieve(
        connection,
        customer_id="CUST-1001",
        query="Rahul",
        query_node_ids=["rahul"],
        damping=0.0,
        top_k=5,
    )
    if [result.memory_id for result in no_walk_results] != ["mem-rahul-outage"]:
        raise AssertionError(f"damping=0 should only return directly personalized memory: {no_walk_results}")

    ppr_results = ppr_retrieve(
        connection,
        customer_id="CUST-1001",
        query="Rahul credit",
        query_node_ids=["rahul"],
        damping=0.5,
        top_k=5,
    )
    result_ids = [result.memory_id for result in ppr_results]
    if "mem-outage-credit" not in result_ids:
        raise AssertionError(f"PPR did not retrieve the multi-hop credit memory: {result_ids}")
    if "mem-router" in result_ids:
        raise AssertionError(f"PPR should not retrieve disconnected router memory: {result_ids}")

    credit_result = next(result for result in ppr_results if result.memory_id == "mem-outage-credit")
    if "outage" not in credit_result.supporting_nodes or "service_credit" not in credit_result.supporting_nodes:
        raise AssertionError(f"supporting nodes should explain graph retrieval: {credit_result.to_dict()}")
    if credit_result.query_nodes != ["rahul"]:
        raise AssertionError(f"query nodes should be preserved: {credit_result.to_dict()}")


def assert_ppr_uses_embedding_query_nodes() -> None:
    connection = make_connection()
    update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-duplicate",
        triples=[
            {
                "subject": "duplicate charge",
                "relation": "related_to",
                "object": "refund",
                "confidence": 0.9,
                "evidence": "duplicate charge related to refund",
            }
        ],
    )
    update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-outage",
        triples=[
            {
                "subject": "service outage",
                "relation": "affected",
                "object": "internet",
                "confidence": 0.9,
                "evidence": "service outage affected internet",
            }
        ],
    )

    vectors = {
        "double billed": [1.0, 0.0],
        "duplicate charge": [0.95, 0.05],
        "refund": [0.7, 0.2],
        "service outage": [0.0, 1.0],
        "internet": [0.0, 0.9],
    }

    def fake_embeddings(labels: list[str]) -> list[list[float]]:
        return [vectors[label] for label in labels]

    results = ppr_retrieve(
        connection,
        customer_id="CUST-1001",
        query="double billed",
        top_k=1,
        damping=0.5,
        embedding_function=fake_embeddings,
    )
    if not results or results[0].memory_id != "mem-duplicate":
        raise AssertionError(f"embedding query node matching failed: {[result.to_dict() for result in results]}")
    if results[0].query_nodes[0] != "duplicate_charge":
        raise AssertionError(f"wrong query node selected: {results[0].to_dict()}")


def assert_ppr_rejects_bad_arguments() -> None:
    connection = make_connection()
    for kwargs in (
        {"customer_id": "", "query": "x"},
        {"customer_id": "CUST-1001", "query": ""},
        {"customer_id": "CUST-1001", "query": "x", "top_k": 0},
        {"customer_id": "CUST-1001", "query": "x", "damping": 1.1},
    ):
        try:
            ppr_retrieve(connection, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad PPR arguments were accepted: {kwargs}")


def assert_node_id_normalization() -> None:
    if node_id_for_label("Invoice INV-8821") != "invoice_inv_8821":
        raise AssertionError("node id normalization failed")

    try:
        node_id_for_label("!!!")
    except ValueError:
        pass
    else:
        raise AssertionError("empty normalized node label was accepted")


def assert_rejects_missing_identifiers() -> None:
    connection = make_connection()
    try:
        update_memory_graph(connection, customer_id="", memory_id="mem-001", triples=[])
    except ValueError:
        pass
    else:
        raise AssertionError("empty customer_id was accepted")

    try:
        update_memory_graph(connection, customer_id="CUST-1001", memory_id="", triples=[])
    except ValueError:
        pass
    else:
        raise AssertionError("empty memory_id was accepted")


def main() -> None:
    assert_creates_memory_graph_table()
    assert_upserts_nodes_and_edges()
    assert_updates_are_idempotent_and_merge_passages()
    assert_adds_synonymy_edges_at_threshold()
    assert_synonymy_edges_can_be_scoped_to_touched_nodes()
    assert_cosine_similarity_math()
    assert_ppr_retrieves_multi_hop_passages()
    assert_ppr_uses_embedding_query_nodes()
    assert_ppr_rejects_bad_arguments()
    assert_node_id_normalization()
    assert_rejects_missing_identifiers()
    print("memory graph tests passed")


if __name__ == "__main__":
    main()
