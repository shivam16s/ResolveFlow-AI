from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import ChromaMemoryStore, decompose_to_memory_units  # noqa: E402


def test_store_units_embeds_and_persists_metadata_in_chromadb() -> None:
    persist_path = ROOT / "data" / "chroma_test_memory"
    if persist_path.exists():
        shutil.rmtree(persist_path)

    units = decompose_to_memory_units(
        [
            {
                "role": "customer",
                "content": "I was charged twice this month and my internet is still not working.",
            }
        ]
    )
    store = ChromaMemoryStore(
        persist_path=persist_path,
        collection_name="resolveflow_memory_test",
    )

    ids = store.store_units(
        units=units,
        customer_id="CUST-1001",
        session_id="SESSION-001",
        created_at=datetime(2026, 5, 21, tzinfo=timezone.utc),
    )

    assert len(ids) == 2
    assert store.collection.count() == 2

    stored = store.collection.get(ids=ids, include=["documents", "metadatas"])
    assert set(stored["documents"]) == {
        "Customer said: I was charged twice this month",
        "Customer said: my internet is still not working",
    }
    assert all(metadata["customer_id"] == "CUST-1001" for metadata in stored["metadatas"])
    assert all(metadata["session_id"] == "SESSION-001" for metadata in stored["metadatas"])
    assert {metadata["memory_type"] for metadata in stored["metadatas"]} == {"episodic"}
    assert {metadata["topic"] for metadata in stored["metadatas"]} == {"billing", "service"}
    assert all(metadata["created_at"].startswith("2026-05-21T00:00:00") for metadata in stored["metadatas"])

    by_id = store.get_by_ids([ids[0], "missing-id"])
    assert set(by_id) == {ids[0]}
    assert by_id[ids[0]]["document"] in stored["documents"]
    assert by_id[ids[0]]["metadata"]["customer_id"] == "CUST-1001"

    results = store.query("duplicate billing payment", customer_id="CUST-1001", top_k=1)
    assert results["ids"][0][0] in ids
    assert "charged twice" in results["documents"][0][0]


def test_store_units_upsert_is_idempotent() -> None:
    persist_path = ROOT / "data" / "chroma_test_memory_idempotent"
    if persist_path.exists():
        shutil.rmtree(persist_path)

    units = decompose_to_memory_units("My preferred language is Tamil and my plan is Fiber Plus 200.")
    store = ChromaMemoryStore(
        persist_path=persist_path,
        collection_name="resolveflow_memory_test_idempotent",
    )

    first_ids = store.store_units(units, customer_id="CUST-1002", session_id="SESSION-002")
    second_ids = store.store_units(units, customer_id="CUST-1002", session_id="SESSION-002")

    assert first_ids == second_ids
    assert store.collection.count() == len(units)
    results = store.query("Tamil language preference", customer_id="CUST-1002", top_k=1, memory_type="stable")
    assert results["metadatas"][0][0]["memory_type"] == "stable"


def test_hybrid_search_combines_vector_and_bm25_rankings() -> None:
    persist_path = ROOT / "data" / "chroma_test_memory_hybrid"
    if persist_path.exists():
        shutil.rmtree(persist_path)

    transcript = [
        {"role": "customer", "content": "I was charged twice this month."},
        {"role": "customer", "content": "My router signal is weak."},
        {"role": "customer", "content": "I want to cancel service."},
    ]
    units = decompose_to_memory_units(transcript)
    store = ChromaMemoryStore(
        persist_path=persist_path,
        collection_name="resolveflow_memory_test_hybrid",
    )
    store.store_units(units, customer_id="CUST-1001", session_id="SESSION-003")

    results = store.hybrid_search("duplicate charge payment", customer_id="CUST-1001", top_k=2)

    assert len(results) == 2
    assert "charged twice" in results[0].document
    assert results[0].vector_rank is not None
    assert results[0].bm25_rank == 1
    assert results[0].bm25_score is not None
    assert results[0].fused_score > 0


def test_hybrid_search_filters_by_customer_and_memory_type() -> None:
    persist_path = ROOT / "data" / "chroma_test_memory_hybrid_filters"
    if persist_path.exists():
        shutil.rmtree(persist_path)

    store = ChromaMemoryStore(
        persist_path=persist_path,
        collection_name="resolveflow_memory_test_hybrid_filters",
    )
    store.store_units(
        decompose_to_memory_units("My preferred language is Tamil."),
        customer_id="CUST-1002",
        session_id="SESSION-004",
    )
    store.store_units(
        decompose_to_memory_units("My preferred language is Hindi."),
        customer_id="CUST-1003",
        session_id="SESSION-005",
    )

    results = store.hybrid_search(
        "language preference",
        customer_id="CUST-1002",
        top_k=3,
        memory_type="stable",
    )

    assert len(results) == 1
    assert results[0].metadata["customer_id"] == "CUST-1002"
    assert results[0].metadata["memory_type"] == "stable"
    assert "Tamil" in results[0].document


def test_hybrid_search_bounds_bm25_candidate_fetches() -> None:
    class FakeCollection:
        def __init__(self) -> None:
            self.get_calls = []

        def count(self) -> int:
            return 1000

        def query(self, **kwargs):
            return {
                "ids": [["mem-vector"]],
                "documents": [["Customer said: router diagnostic completed"]],
                "metadatas": [[{"customer_id": "CUST-1001", "memory_type": "episodic"}]],
                "distances": [[0.42]],
            }

        def get(self, **kwargs):
            self.get_calls.append(kwargs)
            if kwargs.get("limit") is None:
                raise AssertionError(f"unbounded Chroma get call: {kwargs}")
            if not kwargs.get("where_document"):
                raise AssertionError(f"BM25 candidate fetch must use where_document: {kwargs}")
            return {
                "ids": ["mem-bm25"],
                "documents": ["Customer said: duplicate charge payment appeared twice"],
                "metadatas": [{"customer_id": "CUST-1001", "memory_type": "episodic"}],
            }

    fake_collection = FakeCollection()
    store = object.__new__(ChromaMemoryStore)
    store.collection = fake_collection

    results = store.hybrid_search(
        "duplicate charge payment",
        customer_id="CUST-1001",
        top_k=2,
    )

    if not fake_collection.get_calls:
        raise AssertionError("BM25 candidate fetch was not attempted")
    if any("limit" not in call or call["limit"] < 2 for call in fake_collection.get_calls):
        raise AssertionError(f"BM25 calls should be bounded: {fake_collection.get_calls}")
    if not any(result.memory_id == "mem-bm25" and result.bm25_rank == 1 for result in results):
        raise AssertionError(f"bounded BM25 candidate was not fused: {[result.to_dict() for result in results]}")


def test_hybrid_search_does_not_search_boolean_joiners_as_bm25_terms() -> None:
    class FakeCollection:
        def __init__(self) -> None:
            self.get_terms = []

        def count(self) -> int:
            return 10

        def query(self, **kwargs):
            if " OR " not in kwargs["query_texts"][0]:
                raise AssertionError(f"vector query should preserve expanded text: {kwargs}")
            return {
                "ids": [["mem-vector"]],
                "documents": [["Customer said: duplicate charge payment appeared twice"]],
                "metadatas": [[{"customer_id": "CUST-1001", "memory_type": "episodic"}]],
                "distances": [[0.31]],
            }

        def get(self, **kwargs):
            term = kwargs.get("where_document", {}).get("$contains")
            self.get_terms.append(term)
            if term == "or":
                raise AssertionError(f"BM25 should not search Boolean joiner terms: {self.get_terms}")
            return {"ids": [], "documents": [], "metadatas": []}

    fake_collection = FakeCollection()
    store = object.__new__(ChromaMemoryStore)
    store.collection = fake_collection

    store.hybrid_search(
        "duplicate charge OR payment OR invoice",
        customer_id="CUST-1001",
        top_k=2,
    )

    if "or" in fake_collection.get_terms:
        raise AssertionError(f"BM25 candidate terms should omit 'or': {fake_collection.get_terms}")


def main() -> None:
    test_store_units_embeds_and_persists_metadata_in_chromadb()
    test_store_units_upsert_is_idempotent()
    test_hybrid_search_combines_vector_and_bm25_rankings()
    test_hybrid_search_filters_by_customer_and_memory_type()
    test_hybrid_search_bounds_bm25_candidate_fetches()
    test_hybrid_search_does_not_search_boolean_joiners_as_bm25_terms()
    print("PASS ChromaDB memory store tests")


if __name__ == "__main__":
    main()
