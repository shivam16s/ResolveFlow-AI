from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping

from backend.db.init_db import DEFAULT_DB_PATH, initialize_database

from .health import compute_relationship_score
from .memory import MemoryUnit, TranscriptInput, decompose_to_memory_units, fact_augmented_expansion
from .memory_graph import (
    PPRMemoryResult,
    SynonymyGraphUpdate,
    add_synonymy_edges,
    initialize_memory_graph,
    node_id_for_label,
    ppr_retrieve,
    update_memory_graph,
)
from .memory_store import ChromaMemoryStore, MemorySearchResult
from .openie import OpenIETriple, extract_openie_triples


FINAL_STATUSES = ("active", "resolved", "escalated", "abandoned")
RRF_K = 60


@dataclass(frozen=True)
class MemoryIndexSummary:
    customer_id: str
    session_id: str
    memory_ids: list[str]
    units_indexed: int
    triples_indexed: int
    extraction_errors: list[dict]
    graph_nodes_upserted: int
    graph_edges_upserted: int
    synonymy_edges_upserted: int
    session_closed: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MergedMemoryResult:
    memory_id: str
    document: str
    metadata: dict
    fused_score: float
    sources: list[str]
    vector_rank: int | None
    graph_rank: int | None
    vector_score: float | None
    graph_score: float | None
    supporting_nodes: list[str]
    query_nodes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MemoryCitationContext:
    memory_id: str
    citation_id: str
    text: str
    metadata: dict
    sources: list[str]
    fused_score: float

    def to_dict(self) -> dict:
        return asdict(self)


class MemoryManager:
    """Coordinates session-close memory indexing across vector and graph stores."""

    def __init__(
        self,
        *,
        vector_store: ChromaMemoryStore | None = None,
        graph_connection: sqlite3.Connection | None = None,
        db_path: Path = DEFAULT_DB_PATH,
        llm_client: Callable[[str], str] | None = None,
        synonymy_embedding_function: Callable[[
            list[str]], list[list[float]]] | None = None,
        synonymy_threshold: float = 0.8,
    ) -> None:
        self.vector_store = vector_store or ChromaMemoryStore()
        self._owns_graph_connection = graph_connection is None
        self._closed = False
        self.graph_connection = graph_connection or _connect_graph_database(
            db_path)
        self.llm_client = llm_client
        self.synonymy_embedding_function = synonymy_embedding_function
        self.synonymy_threshold = synonymy_threshold
        initialize_memory_graph(self.graph_connection)

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._owns_graph_connection:
                self.graph_connection.close()
        finally:
            self._closed = True

    def __enter__(self) -> "MemoryManager":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def index_session(
        self,
        *,
        session_transcript: TranscriptInput,
        customer_id: str,
        session_id: str,
        final_status: str = "resolved",
        close_session: bool = True,
    ) -> MemoryIndexSummary:
        self._ensure_open()
        customer_id = customer_id.strip()
        session_id = session_id.strip()
        if not customer_id:
            raise ValueError("customer_id must not be empty")
        if not session_id:
            raise ValueError("session_id must not be empty")
        if final_status not in FINAL_STATUSES:
            raise ValueError(f"final_status must be one of {FINAL_STATUSES}")

        self._persist_relationship_start(customer_id, session_id)
        units = decompose_to_memory_units(session_transcript)
        if not units:
            session_closed = self._mark_session_closed(
                customer_id, session_id, final_status) if close_session else False
            return MemoryIndexSummary(
                customer_id=customer_id,
                session_id=session_id,
                memory_ids=[],
                units_indexed=0,
                triples_indexed=0,
                extraction_errors=[],
                graph_nodes_upserted=0,
                graph_edges_upserted=0,
                synonymy_edges_upserted=0,
                session_closed=session_closed,
            )

        memory_ids = self.vector_store.store_units(
            units=units,
            customer_id=customer_id,
            session_id=session_id,
        )
        if len(memory_ids) != len(units):
            raise ValueError(
                "vector_store.store_units must return one memory_id per memory unit")

        triples_indexed = 0
        extraction_errors = []
        graph_nodes_upserted = 0
        graph_edges_upserted = 0
        synonymy_candidate_node_ids: set[str] = set()
        for unit, memory_id in zip(units, memory_ids):
            try:
                triples = self._extract_triples(unit)
            except Exception as exc:  # noqa: BLE001 - one malformed OpenIE response must not strand vector writes.
                triples = []
                extraction_errors.append(
                    {
                        "memory_id": memory_id,
                        "memory_type": unit.memory_type,
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                    }
                )
            triples_indexed += len(triples)
            synonymy_candidate_node_ids.update(_node_ids_for_triples(triples))
            graph_update = update_memory_graph(
                self.graph_connection,
                customer_id=customer_id,
                memory_id=memory_id,
                triples=triples,
            )
            graph_nodes_upserted += graph_update.nodes_upserted
            graph_edges_upserted += graph_update.edges_upserted

        synonymy_update = self._add_synonymy_edges(
            customer_id,
            candidate_node_ids=synonymy_candidate_node_ids,
        )
        session_closed = self._mark_session_closed(
            customer_id, session_id, final_status) if close_session else False

        return MemoryIndexSummary(
            customer_id=customer_id,
            session_id=session_id,
            memory_ids=memory_ids,
            units_indexed=len(units),
            triples_indexed=triples_indexed,
            extraction_errors=extraction_errors,
            graph_nodes_upserted=graph_nodes_upserted,
            graph_edges_upserted=graph_edges_upserted,
            synonymy_edges_upserted=synonymy_update.edges_upserted,
            session_closed=session_closed,
        )

    def retrieve(
        self,
        *,
        query: str,
        customer_id: str,
        top_k: int = 5,
        memory_type: str | None = None,
        graph_damping: float = 0.5,
    ) -> list[MergedMemoryResult]:
        self._ensure_open()
        query = query.strip()
        customer_id = customer_id.strip()
        if not query:
            raise ValueError("query must not be empty")
        if not customer_id:
            raise ValueError("customer_id must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")

        vector_query = fact_augmented_expansion(query)
        vector_results = self.vector_store.hybrid_search(
            query_text=vector_query,
            customer_id=customer_id,
            top_k=top_k * 2,
            memory_type=memory_type,
        )
        graph_results = ppr_retrieve(
            self.graph_connection,
            customer_id=customer_id,
            query=query,
            top_k=top_k * 2,
            damping=graph_damping,
        )
        return _merge_memory_results(
            vector_results=vector_results,
            graph_results=graph_results,
            documents_by_id=self._documents_for_graph_results(
                vector_results, graph_results),
            top_k=top_k,
        )

    def retrieve_citation_context(
        self,
        *,
        query: str,
        customer_id: str,
        top_k: int = 5,
        max_chars_per_memory: int = 320,
        memory_type: str | None = None,
    ) -> list[MemoryCitationContext]:
        results = self.retrieve(
            query=query,
            customer_id=customer_id,
            top_k=top_k,
            memory_type=memory_type,
        )
        return build_memory_citation_context(
            results,
            max_items=top_k,
            max_chars_per_memory=max_chars_per_memory,
        )

    def _extract_triples(self, unit: MemoryUnit) -> list[OpenIETriple]:
        return extract_openie_triples(unit.content, self.llm_client)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("MemoryManager is closed")

    def _add_synonymy_edges(
        self,
        customer_id: str,
        *,
        candidate_node_ids: Iterable[str] | None = None,
    ) -> SynonymyGraphUpdate:
        return add_synonymy_edges(
            self.graph_connection,
            customer_id=customer_id,
            threshold=self.synonymy_threshold,
            embedding_function=self.synonymy_embedding_function,
            candidate_node_ids=candidate_node_ids,
        )

    def _mark_session_closed(self, customer_id: str, session_id: str, final_status: str) -> bool:
        if not _table_exists(self.graph_connection, "conversations"):
            return False
        if not _has_columns(
            self.graph_connection,
            "conversations",
            {"relationship_score_start", "relationship_score_end", "relationship_delta"},
        ):
            return self._mark_session_closed_without_relationship_scores(customer_id, session_id, final_status)

        completed_at = datetime.now(timezone.utc).isoformat()
        start_score = self._relationship_score_start(customer_id, session_id)
        end_score = self._relationship_score_end(
            customer_id, session_id, start_score)
        relationship_delta = round(end_score - start_score, 2)
        with self.graph_connection:
            cursor = self.graph_connection.execute(
                """
                UPDATE conversations
                SET final_status = ?,
                    completed_at = ?,
                    relationship_score_start = COALESCE(relationship_score_start, ?),
                    relationship_score_end = ?,
                    relationship_delta = ?
                WHERE customer_id = ? AND session_id = ?
                """,
                (
                    final_status,
                    completed_at,
                    start_score,
                    end_score,
                    relationship_delta,
                    customer_id,
                    session_id,
                ),
            )
        return cursor.rowcount > 0

    def _mark_session_closed_without_relationship_scores(
        self,
        customer_id: str,
        session_id: str,
        final_status: str,
    ) -> bool:
        completed_at = datetime.now(timezone.utc).isoformat()
        with self.graph_connection:
            cursor = self.graph_connection.execute(
                """
                UPDATE conversations
                SET final_status = ?, completed_at = ?
                WHERE customer_id = ? AND session_id = ?
                """,
                (final_status, completed_at, customer_id, session_id),
            )
        return cursor.rowcount > 0

    def _persist_relationship_start(self, customer_id: str, session_id: str) -> None:
        if not _table_exists(self.graph_connection, "conversations"):
            return
        if not _has_columns(self.graph_connection, "conversations", {"relationship_score_start"}):
            return
        start_score = self._relationship_score_start(customer_id, session_id)
        with self.graph_connection:
            self.graph_connection.execute(
                """
                UPDATE conversations
                SET relationship_score_start = COALESCE(relationship_score_start, ?)
                WHERE customer_id = ? AND session_id = ?
                """,
                (start_score, customer_id, session_id),
            )

    def _relationship_score_start(self, customer_id: str, session_id: str) -> float:
        row = self.graph_connection.execute(
            """
            SELECT relationship_score_start
            FROM conversations
            WHERE customer_id = ? AND session_id = ?
            """,
            (customer_id, session_id),
        ).fetchone()
        if row is not None and row[0] is not None:
            return round(float(row[0]), 2)
        return compute_relationship_score(self._past_session_scores(customer_id, exclude_session_id=session_id)).score

    def _relationship_score_end(self, customer_id: str, session_id: str, start_score: float) -> float:
        current_score = self._current_session_health_score(
            customer_id, session_id)
        if current_score is None:
            return start_score
        return compute_relationship_score(
            self._past_session_scores(
                customer_id, exclude_session_id=session_id) + [current_score]
        ).score

    def _past_session_scores(self, customer_id: str, *, exclude_session_id: str) -> list[float]:
        rows = self.graph_connection.execute(
            """
            SELECT relationship_score_end, health_scores
            FROM conversations
            WHERE customer_id = ?
              AND session_id <> ?
              AND final_status <> 'active'
            ORDER BY COALESCE(completed_at, created_at), created_at, session_id
            """,
            (customer_id, exclude_session_id),
        ).fetchall()
        scores = []
        for row in rows:
            relationship_score = _optional_score(row[0])
            if relationship_score is not None:
                scores.append(relationship_score)
                continue
            health_score = _latest_health_score(row[1])
            if health_score is not None:
                scores.append(health_score)
        return scores

    def _current_session_health_score(self, customer_id: str, session_id: str) -> float | None:
        row = self.graph_connection.execute(
            """
            SELECT health_scores
            FROM conversations
            WHERE customer_id = ? AND session_id = ?
            """,
            (customer_id, session_id),
        ).fetchone()
        if row is None:
            return None
        return _latest_health_score(row[0])

    def _documents_for_graph_results(
        self,
        vector_results: list[MemorySearchResult],
        graph_results: list[PPRMemoryResult],
    ) -> dict[str, dict]:
        documents_by_id = {
            result.memory_id: {
                "document": result.document,
                "metadata": result.metadata,
            }
            for result in vector_results
        }
        missing_ids = [
            result.memory_id
            for result in graph_results
            if result.memory_id not in documents_by_id
        ]
        if missing_ids and hasattr(self.vector_store, "get_by_ids"):
            documents_by_id.update(self.vector_store.get_by_ids(missing_ids))
        return documents_by_id


def _connect_graph_database(db_path: Path) -> sqlite3.Connection:
    initialize_database(db_path)
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    initialize_memory_graph(connection)
    return connection


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _has_columns(connection: sqlite3.Connection, table_name: str, column_names: set[str]) -> bool:
    columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    return column_names <= columns


def _optional_score(value) -> float | None:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 0 or score > 100:
        return None
    return round(score, 2)


def _latest_health_score(raw_health_scores) -> float | None:
    if raw_health_scores is None:
        return None
    try:
        values = json.loads(raw_health_scores) if isinstance(
            raw_health_scores, str) else raw_health_scores
    except json.JSONDecodeError:
        return None
    if not isinstance(values, list):
        return None
    for item in reversed(values):
        score = _score_from_health_item(item)
        if score is not None:
            return score
    return None


def _score_from_health_item(item) -> float | None:
    if isinstance(item, dict):
        for key in ("score", "health_score", "value"):
            if key in item:
                return _optional_score(item[key])
        return None
    return _optional_score(item)


def _merge_memory_results(
    *,
    vector_results: list[MemorySearchResult],
    graph_results: list[PPRMemoryResult],
    documents_by_id: dict[str, dict],
    top_k: int,
) -> list[MergedMemoryResult]:
    vector_by_id = {result.memory_id: result for result in vector_results}
    graph_by_id = {result.memory_id: result for result in graph_results}
    vector_ranks = {result.memory_id: index +
                    1 for index, result in enumerate(vector_results)}
    graph_ranks = {result.memory_id: index +
                   1 for index, result in enumerate(graph_results)}
    memory_ids = set(vector_by_id) | set(graph_by_id)

    merged = []
    for memory_id in memory_ids:
        document_record = documents_by_id.get(memory_id, {})
        document = str(document_record.get("document", "")).strip()
        if not document:
            continue

        vector_result = vector_by_id.get(memory_id)
        graph_result = graph_by_id.get(memory_id)
        vector_rank = vector_ranks.get(memory_id)
        graph_rank = graph_ranks.get(memory_id)
        sources = []
        fused_score = 0.0
        if vector_rank is not None:
            fused_score += 1 / (RRF_K + vector_rank)
            sources.append("vector")
        if graph_rank is not None:
            fused_score += 1 / (RRF_K + graph_rank)
            sources.append("graph")

        metadata = document_record.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        merged.append(
            MergedMemoryResult(
                memory_id=memory_id,
                document=document,
                metadata=metadata,
                fused_score=round(fused_score, 8),
                sources=sources,
                vector_rank=vector_rank,
                graph_rank=graph_rank,
                vector_score=vector_result.fused_score if vector_result else None,
                graph_score=graph_result.score if graph_result else None,
                supporting_nodes=graph_result.supporting_nodes if graph_result else [],
                query_nodes=graph_result.query_nodes if graph_result else [],
            )
        )

    return sorted(
        merged,
        key=lambda result: (
            -result.fused_score,
            result.vector_rank or 9999,
            result.graph_rank or 9999,
            result.memory_id,
        ),
    )[:top_k]


def build_memory_citation_context(
    results: list[MergedMemoryResult],
    *,
    max_items: int = 5,
    max_chars_per_memory: int = 320,
) -> list[MemoryCitationContext]:
    if max_items < 1:
        raise ValueError("max_items must be at least 1")
    if max_chars_per_memory < 40:
        raise ValueError("max_chars_per_memory must be at least 40")

    contexts = []
    for index, result in enumerate(results[:max_items], start=1):
        contexts.append(
            MemoryCitationContext(
                memory_id=result.memory_id,
                citation_id=f"M{index}",
                text=_truncate_text(result.document, max_chars_per_memory),
                metadata=result.metadata,
                sources=result.sources,
                fused_score=result.fused_score,
            )
        )
    return contexts


def format_memory_citation_context(contexts: list[MemoryCitationContext]) -> str:
    lines = []
    for context in contexts:
        source_text = "+".join(context.sources) if context.sources else "unknown"
        lines.append(
            f"[{context.citation_id}] {context.text} "
            f"(memory_id={context.memory_id}, source={source_text}, score={context.fused_score:.5f})"
        )
    return "\n".join(lines)


def _node_ids_for_triples(
    triples: Iterable[OpenIETriple | Mapping[str, object]],
) -> set[str]:
    node_ids: set[str] = set()
    for triple in triples:
        if isinstance(triple, Mapping):
            subject = triple.get("subject")
            object_value = triple.get("object")
        else:
            subject = getattr(triple, "subject", None)
            object_value = getattr(triple, "object", None)

        for label in (subject, object_value):
            if label is None or not str(label).strip():
                continue
            try:
                node_ids.add(node_id_for_label(str(label)))
            except ValueError:
                continue
    return node_ids


def _truncate_text(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."
