from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping, Sequence

from .openie import OpenIETriple


NODE_TYPES = ("entity", "event", "policy", "preference", "value")

CREATE_MEMORY_GRAPH_SQL = """
CREATE TABLE IF NOT EXISTS memory_graph (
  customer_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  node_type TEXT NOT NULL CHECK (node_type IN ('entity', 'event', 'policy', 'preference', 'value')),
  label TEXT NOT NULL,
  passages TEXT NOT NULL DEFAULT '[]',
  edges TEXT NOT NULL DEFAULT '[]',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (customer_id, node_id)
);
"""

CREATE_MEMORY_GRAPH_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_memory_graph_customer_type
ON memory_graph(customer_id, node_type);
"""


@dataclass(frozen=True)
class MemoryGraphEdge:
    target_node: str
    relation: str
    weight: float
    passages: list[str]
    evidence: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MemoryGraphUpdate:
    customer_id: str
    memory_id: str
    nodes_upserted: int
    edges_upserted: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SynonymyGraphUpdate:
    customer_id: str
    threshold: float
    nodes_considered: int
    edges_upserted: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PPRMemoryResult:
    memory_id: str
    score: float
    supporting_nodes: list[str]
    query_nodes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def initialize_memory_graph(connection: sqlite3.Connection) -> None:
    connection.execute(CREATE_MEMORY_GRAPH_SQL)
    connection.execute(CREATE_MEMORY_GRAPH_INDEX_SQL)


def update_memory_graph(
    connection: sqlite3.Connection,
    *,
    customer_id: str,
    memory_id: str,
    triples: Iterable[OpenIETriple | Mapping[str, object]],
) -> MemoryGraphUpdate:
    customer_id = customer_id.strip()
    memory_id = memory_id.strip()
    if not customer_id:
        raise ValueError("customer_id must not be empty")
    if not memory_id:
        raise ValueError("memory_id must not be empty")

    normalized_triples = [_normalize_triple(triple) for triple in triples]
    normalized_triples = [
        triple for triple in normalized_triples if triple is not None]
    if not normalized_triples:
        initialize_memory_graph(connection)
        return MemoryGraphUpdate(customer_id=customer_id, memory_id=memory_id, nodes_upserted=0, edges_upserted=0)

    now = datetime.now(timezone.utc).isoformat()
    touched_nodes: set[str] = set()
    touched_edges: set[tuple[str, str, str]] = set()

    with connection:
        initialize_memory_graph(connection)
        for triple in normalized_triples:
            subject_id = node_id_for_label(triple.subject)
            object_id = node_id_for_label(triple.object)
            subject_type = infer_node_type(
                triple.subject, relation=triple.relation)
            object_type = infer_node_type(
                triple.object, relation=triple.relation)

            _upsert_node(
                connection,
                customer_id=customer_id,
                node_id=subject_id,
                label=triple.subject,
                node_type=subject_type,
                memory_id=memory_id,
                updated_at=now,
            )
            _upsert_node(
                connection,
                customer_id=customer_id,
                node_id=object_id,
                label=triple.object,
                node_type=object_type,
                memory_id=memory_id,
                updated_at=now,
            )
            _upsert_edge(
                connection,
                customer_id=customer_id,
                source_node_id=subject_id,
                target_node_id=object_id,
                relation=triple.relation,
                memory_id=memory_id,
                evidence=triple.evidence,
                confidence=triple.confidence,
                updated_at=now,
            )
            touched_nodes.update({subject_id, object_id})
            touched_edges.add((subject_id, triple.relation, object_id))

    return MemoryGraphUpdate(
        customer_id=customer_id,
        memory_id=memory_id,
        nodes_upserted=len(touched_nodes),
        edges_upserted=len(touched_edges),
    )


def add_synonymy_edges(
    connection: sqlite3.Connection,
    *,
    customer_id: str,
    threshold: float = 0.8,
    embedding_function: Callable[[list[str]],
                                 Sequence[Sequence[float]]] | None = None,
) -> SynonymyGraphUpdate:
    customer_id = customer_id.strip()
    if not customer_id:
        raise ValueError("customer_id must not be empty")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.0 and 1.0")

    initialize_memory_graph(connection)
    nodes = list_memory_graph_nodes(connection, customer_id)
    if len(nodes) < 2:
        return SynonymyGraphUpdate(
            customer_id=customer_id,
            threshold=threshold,
            nodes_considered=len(nodes),
            edges_upserted=0,
        )

    labels = [node["label"] for node in nodes]
    encoder = embedding_function or _default_embedding_function()
    embeddings = [list(map(float, vector)) for vector in encoder(labels)]
    if len(embeddings) != len(nodes):
        raise ValueError(
            "embedding_function must return one embedding per node label")

    now = datetime.now(timezone.utc).isoformat()
    touched_edges: set[tuple[str, str]] = set()

    with connection:
        for left_index in range(len(nodes)):
            for right_index in range(left_index + 1, len(nodes)):
                similarity = cosine_similarity(
                    embeddings[left_index], embeddings[right_index])
                if similarity < threshold:
                    continue

                left = nodes[left_index]
                right = nodes[right_index]
                passages = _dedupe(left["passages"] + right["passages"])
                evidence = [
                    f"cosine_similarity={similarity:.4f}",
                    f"{left['label']} ~ {right['label']}",
                ]
                weight = round(similarity, 4)
                _upsert_edge_values(
                    connection,
                    customer_id=customer_id,
                    source_node_id=left["node_id"],
                    target_node_id=right["node_id"],
                    relation="synonymy",
                    passages=passages,
                    evidence_items=evidence,
                    weight=weight,
                    updated_at=now,
                )
                _upsert_edge_values(
                    connection,
                    customer_id=customer_id,
                    source_node_id=right["node_id"],
                    target_node_id=left["node_id"],
                    relation="synonymy",
                    passages=passages,
                    evidence_items=evidence,
                    weight=weight,
                    updated_at=now,
                )
                touched_edges.add((left["node_id"], right["node_id"]))
                touched_edges.add((right["node_id"], left["node_id"]))

    return SynonymyGraphUpdate(
        customer_id=customer_id,
        threshold=threshold,
        nodes_considered=len(nodes),
        edges_upserted=len(touched_edges),
    )


def ppr_retrieve(
    connection: sqlite3.Connection,
    *,
    customer_id: str,
    query: str,
    top_k: int = 5,
    damping: float = 0.5,
    query_node_ids: Iterable[str] | None = None,
    query_node_count: int = 2,
    embedding_function: Callable[[list[str]],
                                 Sequence[Sequence[float]]] | None = None,
    max_iterations: int = 50,
    tolerance: float = 1e-8,
) -> list[PPRMemoryResult]:
    customer_id = customer_id.strip()
    query = query.strip()
    if not customer_id:
        raise ValueError("customer_id must not be empty")
    if not query:
        raise ValueError("query must not be empty")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if not 0.0 <= damping <= 1.0:
        raise ValueError("damping must be between 0.0 and 1.0")
    if query_node_count < 1:
        raise ValueError("query_node_count must be at least 1")

    initialize_memory_graph(connection)
    nodes = list_memory_graph_nodes(connection, customer_id)
    if not nodes:
        return []

    node_index = {node["node_id"]: index for index, node in enumerate(nodes)}
    query_nodes = _resolve_query_nodes(
        nodes=nodes,
        query=query,
        query_node_ids=query_node_ids,
        query_node_count=query_node_count,
        embedding_function=embedding_function,
    )
    if not query_nodes:
        return []

    personalization = _personalization_vector(
        nodes=nodes, node_index=node_index, query_nodes=query_nodes)
    if sum(personalization) == 0.0:
        return []

    node_scores = _run_personalized_pagerank(
        nodes=nodes,
        node_index=node_index,
        personalization=personalization,
        damping=damping,
        max_iterations=max_iterations,
        tolerance=tolerance,
    )
    passage_scores = _score_passages(nodes=nodes, node_scores=node_scores)
    query_node_ids_used = [node["node_id"] for node in query_nodes]

    results = []
    for memory_id, score in sorted(passage_scores.items(), key=lambda item: (-item[1], item[0]))[:top_k]:
        supporting_nodes = [
            node["node_id"]
            for index, node in enumerate(nodes)
            if memory_id in node["passages"] and node_scores[index] > 0.0
        ]
        supporting_nodes.sort(
            key=lambda node_id: (-node_scores[node_index[node_id]], node_id))
        results.append(
            PPRMemoryResult(
                memory_id=memory_id,
                score=round(score, 8),
                supporting_nodes=supporting_nodes[:8],
                query_nodes=query_node_ids_used,
            )
        )

    return results


def get_memory_graph_node(connection: sqlite3.Connection, customer_id: str, node_id: str) -> dict | None:
    row = connection.execute(
        """
        SELECT customer_id, node_id, node_type, label, passages, edges, created_at, updated_at
        FROM memory_graph
        WHERE customer_id = ? AND node_id = ?
        """,
        (customer_id, node_id),
    ).fetchone()
    if row is None:
        return None

    return {
        "customer_id": row[0],
        "node_id": row[1],
        "node_type": row[2],
        "label": row[3],
        "passages": _loads_json_list(row[4]),
        "edges": _loads_json_list(row[5]),
        "created_at": row[6],
        "updated_at": row[7],
    }


def list_memory_graph_nodes(connection: sqlite3.Connection, customer_id: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT customer_id, node_id, node_type, label, passages, edges, created_at, updated_at
        FROM memory_graph
        WHERE customer_id = ?
        ORDER BY node_id
        """,
        (customer_id,),
    ).fetchall()
    return [
        {
            "customer_id": row[0],
            "node_id": row[1],
            "node_type": row[2],
            "label": row[3],
            "passages": _loads_json_list(row[4]),
            "edges": _loads_json_list(row[5]),
            "created_at": row[6],
            "updated_at": row[7],
        }
        for row in rows
    ]


def node_id_for_label(label: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", label.strip().lower())
    normalized = normalized.strip("_")
    if not normalized:
        raise ValueError(
            "node label must contain at least one alphanumeric character")
    return normalized[:120]


def infer_node_type(label: str, relation: str = "") -> str:
    text = f"{label} {relation}".lower()
    if any(term in text for term in ("policy", "credit rule", "refund rule", "cancellation rule")):
        return "policy"
    if any(term in text for term in ("preferred", "preference", "language", "plan")):
        return "preference"
    if any(term in text for term in ("outage", "charged", "payment", "invoice", "ticket", "visit", "cancel")):
        return "event"
    if re.search(r"\b(?:rs|inr|₹)?\s*\d+(?:\.\d+)?\b", text) or any(term in text for term in ("yesterday", "today", "zone", "may ")):
        return "value"
    return "entity"


def _upsert_node(
    connection: sqlite3.Connection,
    *,
    customer_id: str,
    node_id: str,
    label: str,
    node_type: str,
    memory_id: str,
    updated_at: str,
) -> None:
    existing = connection.execute(
        "SELECT passages, edges, node_type, label FROM memory_graph WHERE customer_id = ? AND node_id = ?",
        (customer_id, node_id),
    ).fetchone()

    if existing is None:
        connection.execute(
            """
            INSERT INTO memory_graph(customer_id, node_id, node_type, label, passages, edges, updated_at)
            VALUES (?, ?, ?, ?, ?, '[]', ?)
            """,
            (customer_id, node_id, node_type, label,
             _json_dumps([memory_id]), updated_at),
        )
        return

    passages = _append_unique(_loads_json_list(existing[0]), memory_id)[-50:]
    stored_type = _more_specific_node_type(existing[2], node_type)
    stored_label = existing[3] or label
    connection.execute(
        """
        UPDATE memory_graph
        SET passages = ?, node_type = ?, label = ?, updated_at = ?
        WHERE customer_id = ? AND node_id = ?
        """,
        (_json_dumps(passages), stored_type,
         stored_label, updated_at, customer_id, node_id),
    )


def _upsert_edge(
    connection: sqlite3.Connection,
    *,
    customer_id: str,
    source_node_id: str,
    target_node_id: str,
    relation: str,
    memory_id: str,
    evidence: str,
    confidence: float,
    updated_at: str,
) -> None:
    _upsert_edge_values(
        connection,
        customer_id=customer_id,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        relation=relation,
        passages=[memory_id],
        evidence_items=[evidence] if evidence else [],
        weight=round(max(0.01, min(1.0, confidence)), 2),
        updated_at=updated_at,
    )


def _upsert_edge_values(
    connection: sqlite3.Connection,
    *,
    customer_id: str,
    source_node_id: str,
    target_node_id: str,
    relation: str,
    passages: Iterable[str],
    evidence_items: Iterable[str],
    weight: float,
    updated_at: str,
) -> None:
    row = connection.execute(
        "SELECT edges FROM memory_graph WHERE customer_id = ? AND node_id = ?",
        (customer_id, source_node_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"source node does not exist: {source_node_id}")

    edges = _loads_json_list(row[0])
    edge = _find_edge(edges, target_node_id=target_node_id, relation=relation)
    clean_passages = _dedupe([str(passage)
                             for passage in passages if str(passage).strip()])
    clean_evidence = _dedupe(
        [str(item) for item in evidence_items if str(item).strip()])[:5]
    clean_weight = round(max(0.01, min(1.0, float(weight))), 4)
    if edge is None:
        edges.append(
            MemoryGraphEdge(
                target_node=target_node_id,
                relation=relation,
                weight=clean_weight,
                passages=clean_passages,
                evidence=clean_evidence,
            ).to_dict()
        )
    else:
        existing_passages = _loads_json_list(edge.get("passages", []))
        for passage in clean_passages:
            existing_passages = _append_unique(existing_passages, passage)
        edge["passages"] = existing_passages[-50:]

        existing_evidence = _loads_json_list(edge.get("evidence", []))
        for evidence in clean_evidence:
            existing_evidence = _append_unique(existing_evidence, evidence)
        edge["evidence"] = existing_evidence[:5]

        prior_weight = float(edge.get("weight", 0.0) or 0.0)
        edge["weight"] = round(max(prior_weight, clean_weight), 4)

    edges.sort(key=lambda item: (str(item.get("relation", "")),
               str(item.get("target_node", ""))))
    connection.execute(
        """
        UPDATE memory_graph
        SET edges = ?, updated_at = ?
        WHERE customer_id = ? AND node_id = ?
        """,
        (_json_dumps(edges), updated_at, customer_id, source_node_id),
    )


def _normalize_triple(triple: OpenIETriple | Mapping[str, object]) -> OpenIETriple | None:
    if isinstance(triple, OpenIETriple):
        subject = triple.subject
        relation = triple.relation
        object_ = triple.object
        confidence = triple.confidence
        evidence = triple.evidence
    else:
        subject = str(triple.get("subject", ""))
        relation = str(triple.get("relation", ""))
        object_ = str(triple.get("object", ""))
        confidence = float(triple.get("confidence", 0.7) or 0.7)
        evidence = str(triple.get("evidence", ""))

    subject = _clean_node_label(subject)
    relation = _clean_relation(relation)
    object_ = _clean_node_label(object_)
    if not subject or not relation or not object_:
        return None

    return OpenIETriple(
        subject=subject,
        relation=relation,
        object=object_,
        confidence=round(max(0.0, min(1.0, confidence)), 2),
        evidence=re.sub(r"\s+", " ", evidence.strip())[:240],
    )


def _clean_node_label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).strip(" .,:;()[]{}")


def _clean_relation(value: str) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _find_edge(edges: list, *, target_node_id: str, relation: str) -> dict | None:
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if edge.get("target_node") == target_node_id and edge.get("relation") == relation:
            return edge
    return None


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have the same dimension")
    dot = sum(left_value * right_value for left_value,
              right_value in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _resolve_query_nodes(
    *,
    nodes: list[dict],
    query: str,
    query_node_ids: Iterable[str] | None,
    query_node_count: int,
    embedding_function: Callable[[list[str]], Sequence[Sequence[float]]] | None,
) -> list[dict]:
    node_by_id = {node["node_id"]: node for node in nodes}
    if query_node_ids is not None:
        resolved = []
        for node_id in query_node_ids:
            node = node_by_id.get(node_id_for_label(str(node_id)))
            if node is not None and node not in resolved:
                resolved.append(node)
        return resolved[:query_node_count]

    query_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
    exact_matches = [
        node
        for node in nodes
        if node["node_id"] in query_terms or set(re.findall(r"[a-z0-9]+", node["label"].lower())) <= query_terms
    ]
    if exact_matches:
        return exact_matches[:query_node_count]

    labels = [node["label"] for node in nodes]
    encoder = embedding_function or _default_embedding_function()
    embeddings = [list(map(float, vector))
                  for vector in encoder([query] + labels)]
    if len(embeddings) != len(nodes) + 1:
        raise ValueError(
            "embedding_function must return one query embedding plus one embedding per node label")

    query_embedding = embeddings[0]
    scored_nodes = [
        (cosine_similarity(query_embedding, node_embedding), node)
        for node, node_embedding in zip(nodes, embeddings[1:])
    ]
    scored_nodes.sort(key=lambda item: (-item[0], item[1]["node_id"]))
    return [node for similarity, node in scored_nodes if similarity > 0.0][:query_node_count]


def _personalization_vector(nodes: list[dict], node_index: dict[str, int], query_nodes: list[dict]) -> list[float]:
    personalization = [0.0 for _ in nodes]
    for node in query_nodes:
        passages_count = max(len(node["passages"]), 1)
        personalization[node_index[node["node_id"]]] = 1.0 / passages_count
    return _normalize_vector(personalization)


def _run_personalized_pagerank(
    *,
    nodes: list[dict],
    node_index: dict[str, int],
    personalization: list[float],
    damping: float,
    max_iterations: int,
    tolerance: float,
) -> list[float]:
    scores = list(personalization)
    transitions = _transition_rows(nodes=nodes, node_index=node_index)

    for _ in range(max_iterations):
        next_scores = [(1.0 - damping) * value for value in personalization]
        for source_index, outgoing in enumerate(transitions):
            if not outgoing:
                for target_index, probability in enumerate(personalization):
                    next_scores[target_index] += damping * \
                        scores[source_index] * probability
                continue
            for target_index, probability in outgoing:
                next_scores[target_index] += damping * \
                    scores[source_index] * probability

        drift = sum(abs(left - right)
                    for left, right in zip(next_scores, scores))
        scores = next_scores
        if drift <= tolerance:
            break

    return _normalize_vector(scores)


def _transition_rows(nodes: list[dict], node_index: dict[str, int]) -> list[list[tuple[int, float]]]:
    rows: list[list[tuple[int, float]]] = []
    for node in nodes:
        outgoing = []
        for edge in node["edges"]:
            if not isinstance(edge, dict):
                continue
            target = edge.get("target_node")
            if target not in node_index:
                continue
            weight = float(edge.get("weight", 1.0) or 1.0)
            outgoing.append((node_index[target], max(weight, 0.01)))

        total_weight = sum(weight for _, weight in outgoing)
        rows.append(
            [(target_index, weight / total_weight)
             for target_index, weight in outgoing]
            if total_weight > 0.0
            else []
        )
    return rows


def _score_passages(nodes: list[dict], node_scores: list[float]) -> dict[str, float]:
    passage_scores: dict[str, float] = {}
    for index, node in enumerate(nodes):
        if node_scores[index] <= 0.0:
            continue
        for memory_id in node["passages"]:
            passage_scores[memory_id] = passage_scores.get(
                memory_id, 0.0) + node_scores[index]
    return {memory_id: score for memory_id, score in passage_scores.items() if score > 0.0}


def _normalize_vector(values: list[float]) -> list[float]:
    total = sum(values)
    if total <= 0.0:
        return values
    return [value / total for value in values]


def _default_embedding_function() -> Callable[[list[str]], Sequence[Sequence[float]]]:
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    return DefaultEmbeddingFunction()


def _loads_json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def _json_dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _append_unique(values: list, value: object) -> list:
    if value not in values:
        values.append(value)
    return values


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _more_specific_node_type(existing: str, incoming: str) -> str:
    priority = {"entity": 0, "value": 1,
                "event": 2, "preference": 3, "policy": 4}
    return incoming if priority.get(incoming, 0) > priority.get(existing, 0) else existing
