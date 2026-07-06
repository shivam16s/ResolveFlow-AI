from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

import chromadb

from .memory import MemoryUnit


DEFAULT_CHROMA_PATH = Path(__file__).resolve().parents[2] / "data" / "chroma"
DEFAULT_MEMORY_COLLECTION = "resolveflow_memory"
RRF_K = 60
BM25_MIN_CANDIDATE_LIMIT = 24
BM25_MAX_TERMS = 8


@dataclass(frozen=True)
class MemorySearchResult:
    memory_id: str
    document: str
    metadata: dict
    fused_score: float
    vector_rank: int | None
    bm25_rank: int | None
    vector_distance: float | None = None
    bm25_score: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class ChromaMemoryStore:
    """Persistent ChromaDB store for real embedded memory units."""

    def __init__(
        self,
        persist_path: Path = DEFAULT_CHROMA_PATH,
        collection_name: str = DEFAULT_MEMORY_COLLECTION,
    ) -> None:
        self.persist_path = Path(persist_path)
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.persist_path))
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def store_units(
        self,
        units: list[MemoryUnit],
        customer_id: str,
        session_id: str,
        created_at: datetime | None = None,
    ) -> list[str]:
        if not units:
            return []

        timestamp = (created_at or datetime.now(timezone.utc)).isoformat()
        ids = [
            _memory_id(customer_id=customer_id,
                       session_id=session_id, unit=unit)
            for unit in units
        ]
        documents = [unit.content for unit in units]
        metadatas = [
            {
                "customer_id": customer_id,
                "session_id": session_id,
                "memory_type": unit.memory_type,
                "topic": unit.topic,
                "source_role": unit.source_role,
                "source_turn_index": unit.source_turn_index,
                "confidence": unit.confidence,
                "entity_tags": json.dumps(unit.entity_tags),
                "created_at": timestamp,
            }
            for unit in units
        ]

        # ChromaDB embeds documents here using the collection embedding function.
        self.collection.upsert(
            ids=ids, documents=documents, metadatas=metadatas)
        return ids

    def query(
        self,
        query_text: str,
        customer_id: str,
        top_k: int = 5,
        memory_type: str | None = None,
    ) -> dict:
        where: dict[str, object]
        if memory_type is None:
            where = {"customer_id": customer_id}
        else:
            where = {
                "$and": [
                    {"customer_id": {"$eq": customer_id}},
                    {"memory_type": {"$eq": memory_type}},
                ]
            }

        return self.collection.query(
            query_texts=[query_text],
            n_results=top_k,
            where=where,
        )

    def hybrid_search(
        self,
        query_text: str,
        customer_id: str,
        top_k: int = 5,
        memory_type: str | None = None,
    ) -> list[MemorySearchResult]:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")

        normalized_query = re.sub(r"\s+", " ", query_text.strip())
        if not normalized_query:
            return []

        where = _where_filter(customer_id=customer_id, memory_type=memory_type)
        vector_results = self.collection.query(
            query_texts=[normalized_query],
            n_results=min(max(top_k * 3, top_k),
                          max(self.collection.count(), 1)),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        vector_rankings = _vector_rankings(vector_results)

        corpus = _bm25_candidate_corpus(
            collection=self.collection,
            query_text=normalized_query,
            where=where,
            top_k=top_k,
            vector_rankings=vector_rankings,
        )
        bm25_rankings = _bm25_rankings(
            query_text=normalized_query, corpus=corpus)

        fused_ids = set(vector_rankings) | set(bm25_rankings)
        fused_results = []
        for memory_id in fused_ids:
            vector_item = vector_rankings.get(memory_id)
            bm25_item = bm25_rankings.get(memory_id)
            source_item = vector_item or bm25_item
            assert source_item is not None
            vector_rank = vector_item["rank"] if vector_item else None
            bm25_rank = bm25_item["rank"] if bm25_item else None
            fused_score = 0.0
            if vector_rank is not None:
                fused_score += 1 / (RRF_K + vector_rank)
            if bm25_rank is not None:
                fused_score += 1 / (RRF_K + bm25_rank)

            fused_results.append(
                MemorySearchResult(
                    memory_id=memory_id,
                    document=source_item["document"],
                    metadata=source_item["metadata"],
                    fused_score=round(fused_score, 6),
                    vector_rank=vector_rank,
                    bm25_rank=bm25_rank,
                    vector_distance=vector_item["distance"] if vector_item else None,
                    bm25_score=bm25_item["score"] if bm25_item else None,
                )
            )

        return sorted(
            fused_results,
            key=lambda result: (
                -result.fused_score,
                result.vector_rank or 9999,
                result.bm25_rank or 9999,
                result.memory_id,
            ),
        )[:top_k]

    def get_by_ids(self, memory_ids: list[str]) -> dict[str, dict]:
        if not memory_ids:
            return {}

        results = self.collection.get(
            ids=memory_ids,
            include=["documents", "metadatas"],
        )
        ids = results.get("ids", [])
        documents = results.get("documents", [])
        metadatas = results.get("metadatas", [])
        return {
            memory_id: {
                "document": documents[index],
                "metadata": metadatas[index],
            }
            for index, memory_id in enumerate(ids)
        }


def _memory_id(customer_id: str, session_id: str, unit: MemoryUnit) -> str:
    key = "|".join(
        [
            customer_id,
            session_id,
            str(unit.source_turn_index),
            unit.source_role,
            unit.content,
        ]
    )
    return f"mem-{uuid5(NAMESPACE_URL, key)}"


def _where_filter(customer_id: str, memory_type: str | None = None) -> dict[str, object]:
    if memory_type is None:
        return {"customer_id": customer_id}
    return {
        "$and": [
            {"customer_id": {"$eq": customer_id}},
            {"memory_type": {"$eq": memory_type}},
        ]
    }


def _vector_rankings(results: dict) -> dict[str, dict]:
    ids = results.get("ids", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    rankings = {}
    for index, memory_id in enumerate(ids):
        rankings[memory_id] = {
            "rank": index + 1,
            "document": documents[index],
            "metadata": metadatas[index],
            "distance": distances[index],
        }
    return rankings


def _bm25_candidate_corpus(
    *,
    collection,
    query_text: str,
    where: dict[str, object],
    top_k: int,
    vector_rankings: dict[str, dict],
) -> dict:
    candidate_limit = max(top_k * 8, BM25_MIN_CANDIDATE_LIMIT)
    candidate_terms = _expand_bm25_query_terms(_bm25_query_terms(query_text))[:BM25_MAX_TERMS]
    merged: dict[str, dict] = {
        memory_id: {
            "document": item["document"],
            "metadata": item["metadata"],
        }
        for memory_id, item in vector_rankings.items()
    }

    for term in candidate_terms:
        try:
            results = collection.get(
                where=where,
                where_document={"$contains": term},
                include=["documents", "metadatas"],
                limit=candidate_limit,
            )
        except Exception:  # noqa: BLE001 - older Chroma builds may not support where_document; keep retrieval bounded.
            continue
        for memory_id, document, metadata in _iter_flat_chroma_results(results):
            merged.setdefault(
                memory_id,
                {
                    "document": document,
                    "metadata": metadata,
                },
            )

    return {
        "ids": list(merged),
        "documents": [item["document"] for item in merged.values()],
        "metadatas": [item["metadata"] for item in merged.values()],
    }


def _iter_flat_chroma_results(results: dict):
    ids = results.get("ids", [])
    documents = results.get("documents", [])
    metadatas = results.get("metadatas", [])
    if ids and isinstance(ids[0], list):
        ids = ids[0]
        documents = documents[0] if documents and isinstance(documents[0], list) else documents
        metadatas = metadatas[0] if metadatas and isinstance(metadatas[0], list) else metadatas

    for index, memory_id in enumerate(ids):
        if index >= len(documents):
            continue
        metadata = metadatas[index] if index < len(metadatas) else {}
        yield memory_id, documents[index], metadata


def _bm25_rankings(query_text: str, corpus: dict) -> dict[str, dict]:
    ids = corpus.get("ids", [])
    documents = corpus.get("documents", [])
    metadatas = corpus.get("metadatas", [])
    if not ids:
        return {}

    tokenized_docs = [_tokenize(document) for document in documents]
    query_terms = _expand_bm25_query_terms(_bm25_query_terms(query_text))
    if not query_terms:
        return {}

    doc_count = len(tokenized_docs)
    avg_doc_len = sum(len(doc) for doc in tokenized_docs) / max(doc_count, 1)
    document_frequencies = {
        term: sum(1 for doc in tokenized_docs if term in doc)
        for term in set(query_terms)
    }

    scored = []
    for index, doc_terms in enumerate(tokenized_docs):
        score = _bm25_score(
            query_terms=query_terms,
            doc_terms=doc_terms,
            document_frequencies=document_frequencies,
            doc_count=doc_count,
            avg_doc_len=avg_doc_len,
        )
        if score <= 0:
            continue
        scored.append(
            {
                "id": ids[index],
                "document": documents[index],
                "metadata": metadatas[index],
                "score": round(score, 6),
            }
        )

    scored.sort(key=lambda item: (-item["score"], item["id"]))
    return {
        item["id"]: {
            "rank": index + 1,
            "document": item["document"],
            "metadata": item["metadata"],
            "score": item["score"],
        }
        for index, item in enumerate(scored)
    }


def _bm25_score(
    query_terms: list[str],
    doc_terms: list[str],
    document_frequencies: dict[str, int],
    doc_count: int,
    avg_doc_len: float,
) -> float:
    k1 = 1.5
    b = 0.75
    doc_len = len(doc_terms)
    if doc_len == 0:
        return 0.0

    score = 0.0
    for term in query_terms:
        term_frequency = doc_terms.count(term)
        if term_frequency == 0:
            continue
        doc_frequency = document_frequencies.get(term, 0)
        idf = math.log(
            1 + ((doc_count - doc_frequency + 0.5) / (doc_frequency + 0.5)))
        denominator = term_frequency + k1 * \
            (1 - b + b * (doc_len / max(avg_doc_len, 1e-9)))
        score += idf * ((term_frequency * (k1 + 1)) / denominator)
    return score


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9-]+", text.lower())


def _bm25_query_terms(query_text: str) -> list[str]:
    return [term for term in _tokenize(query_text) if term not in {"and", "or"}]


def _expand_bm25_query_terms(query_terms: list[str]) -> list[str]:
    expanded = list(query_terms)
    query_set = set(query_terms)
    if "duplicate" in query_set:
        expanded.extend(["charged", "twice", "double", "payment"])
    if "charge" in query_set:
        expanded.extend(["charged", "billing", "invoice"])
    if "payment" in query_set:
        expanded.extend(["paid", "charged", "invoice"])
    if "outage" in query_set:
        expanded.extend(["internet", "down", "connection"])
    if "router" in query_set:
        expanded.extend(["signal", "modem"])
    return _dedupe(expanded)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
