from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from backend.db.init_db import DEFAULT_DB_PATH, initialize_database

from .memory import MemoryUnit, TranscriptInput, decompose_to_memory_units, fact_augmented_expansion
from .memory_graph import (
    PPRMemoryResult,
    SynonymyGraphUpdate,
    add_synonymy_edges,
    initialize_memory_graph,
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


class MemoryManager:
    """Coordinates session-close memory indexing across vector and graph stores."""

    def __init__(
        self,
        *,
        vector_store: ChromaMemoryStore | None = None,
        graph_connection: sqlite3.Connection | None = None,
        db_path: Path = DEFAULT_DB_PATH,
        llm_client: Callable[[str], str] | None = None,
        synonymy_embedding_function: Callable[[list[str]], list[list[float]]] | None = None,
        synonymy_threshold: float = 0.8,
    ) -> None:
        self.vector_store = vector_store or ChromaMemoryStore()
        self.graph_connection = graph_connection or _connect_graph_database(db_path)
        self.llm_client = llm_client
        self.synonymy_embedding_function = synonymy_embedding_function
        self.synonymy_threshold = synonymy_threshold
        initialize_memory_graph(self.graph_connection)

    def index_session(
        self,
        *,
        session_transcript: TranscriptInput,
        customer_id: str,
        session_id: str,
        final_status: str = "resolved",
        close_session: bool = True,
    ) -> MemoryIndexSummary:
        customer_id = customer_id.strip()
        session_id = session_id.strip()
        if not customer_id:
            raise ValueError("customer_id must not be empty")
        if not session_id:
            raise ValueError("session_id must not be empty")
        if final_status not in FINAL_STATUSES:
            raise ValueError(f"final_status must be one of {FINAL_STATUSES}")

        units = decompose_to_memory_units(session_transcript)
        if not units:
            session_closed = self._mark_session_closed(customer_id, session_id, final_status) if close_session else False
            return MemoryIndexSummary(
                customer_id=customer_id,
                session_id=session_id,
                memory_ids=[],
                units_indexed=0,
                triples_indexed=0,
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
            raise ValueError("vector_store.store_units must return one memory_id per memory unit")

        triples_indexed = 0
        graph_nodes_upserted = 0
        graph_edges_upserted = 0
        for unit, memory_id in zip(units, memory_ids):
            triples = self._extract_triples(unit)
            triples_indexed += len(triples)
            graph_update = update_memory_graph(
                self.graph_connection,
                customer_id=customer_id,
                memory_id=memory_id,
                triples=triples,
            )
            graph_nodes_upserted += graph_update.nodes_upserted
            graph_edges_upserted += graph_update.edges_upserted

        synonymy_update = self._add_synonymy_edges(customer_id)
        session_closed = self._mark_session_closed(customer_id, session_id, final_status) if close_session else False

        return MemoryIndexSummary(
            customer_id=customer_id,
            session_id=session_id,
            memory_ids=memory_ids,
            units_indexed=len(units),
            triples_indexed=triples_indexed,
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
            documents_by_id=self._documents_for_graph_results(vector_results, graph_results),
            top_k=top_k,
        )

    def _extract_triples(self, unit: MemoryUnit) -> list[OpenIETriple]:
        return extract_openie_triples(unit.content, self.llm_client)

    def _add_synonymy_edges(self, customer_id: str) -> SynonymyGraphUpdate:
        return add_synonymy_edges(
            self.graph_connection,
            customer_id=customer_id,
            threshold=self.synonymy_threshold,
            embedding_function=self.synonymy_embedding_function,
        )

    def _mark_session_closed(self, customer_id: str, session_id: str, final_status: str) -> bool:
        if not _table_exists(self.graph_connection, "conversations"):
            return False

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


def _merge_memory_results(
    *,
    vector_results: list[MemorySearchResult],
    graph_results: list[PPRMemoryResult],
    documents_by_id: dict[str, dict],
    top_k: int,
) -> list[MergedMemoryResult]:
    vector_by_id = {result.memory_id: result for result in vector_results}
    graph_by_id = {result.memory_id: result for result in graph_results}
    vector_ranks = {result.memory_id: index + 1 for index, result in enumerate(vector_results)}
    graph_ranks = {result.memory_id: index + 1 for index, result in enumerate(graph_results)}
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
