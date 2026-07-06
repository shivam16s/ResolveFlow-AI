from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.memory_graph import get_memory_graph_node, list_memory_graph_nodes  # noqa: E402
from backend.agent.memory_manager import (  # noqa: E402
    MemoryManager,
    build_memory_citation_context,
    format_memory_citation_context,
)
from backend.agent.memory_store import MemorySearchResult  # noqa: E402


class FakeVectorStore:
    def __init__(self) -> None:
        self.calls = []
        self.hybrid_calls = []
        self.documents = {
            "mem-vector": {
                "document": "Customer asked about duplicate billing last month.",
                "metadata": {"source": "vector"},
            },
            "mem-shared": {
                "document": "Customer reported a duplicate charge for invoice INV-8821.",
                "metadata": {"source": "shared"},
            },
            "mem-graph": {
                "document": "Invoice INV-8821 refund review was linked to the duplicate charge.",
                "metadata": {"source": "graph"},
            },
        }

    def store_units(self, *, units, customer_id: str, session_id: str):
        self.calls.append(
            {
                "units": units,
                "customer_id": customer_id,
                "session_id": session_id,
            }
        )
        return [f"mem-{index + 1:03d}" for index, _ in enumerate(units)]

    def hybrid_search(self, *, query_text: str, customer_id: str, top_k: int, memory_type=None):
        self.hybrid_calls.append(
            {
                "query_text": query_text,
                "customer_id": customer_id,
                "top_k": top_k,
                "memory_type": memory_type,
            }
        )
        return [
            MemorySearchResult(
                memory_id="mem-vector",
                document=self.documents["mem-vector"]["document"],
                metadata=self.documents["mem-vector"]["metadata"],
                fused_score=0.03,
                vector_rank=1,
                bm25_rank=2,
            ),
            MemorySearchResult(
                memory_id="mem-shared",
                document=self.documents["mem-shared"]["document"],
                metadata=self.documents["mem-shared"]["metadata"],
                fused_score=0.02,
                vector_rank=2,
                bm25_rank=1,
            ),
        ][:top_k]

    def get_by_ids(self, memory_ids: list[str]) -> dict[str, dict]:
        return {
            memory_id: self.documents[memory_id]
            for memory_id in memory_ids
            if memory_id in self.documents
        }


class FakeLLM:
    def __init__(self) -> None:
        self.prompts = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        lower = prompt.lower()
        if "charged twice" in lower:
            return json.dumps(
                {
                    "triples": [
                        {
                            "subject": "customer",
                            "relation": "reported",
                            "object": "duplicate charge",
                            "confidence": 0.93,
                            "evidence": "Customer said charged twice",
                        },
                        {
                            "subject": "duplicate charge",
                            "relation": "involved",
                            "object": "invoice INV-8821",
                            "confidence": 0.9,
                            "evidence": "charged twice for invoice INV-8821",
                        },
                    ]
                }
            )
        if "internet was down" in lower:
            return json.dumps(
                {
                    "triples": [
                        {
                            "subject": "internet",
                            "relation": "was_down_in",
                            "object": "Chennai Zone 04",
                            "confidence": 0.92,
                            "evidence": "internet was down in Chennai Zone 04",
                        }
                    ]
                }
            )
        return json.dumps({"triples": []})


class PartiallyMalformedLLM(FakeLLM):
    def __call__(self, prompt: str) -> str:
        if "internet was down" in prompt.lower():
            self.prompts.append(prompt)
            return "not valid json"
        return super().__call__(prompt)


class PunctuationOnlyTripleLLM(FakeLLM):
    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(
            {
                "triples": [
                    {
                        "subject": "!!!",
                        "relation": "mentioned",
                        "object": "...",
                        "confidence": 0.8,
                        "evidence": "malformed but valid JSON labels",
                    }
                ]
            }
        )


def make_connection_with_conversation() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """
        CREATE TABLE conversations (
          session_id TEXT PRIMARY KEY,
          customer_id TEXT NOT NULL,
          messages TEXT NOT NULL DEFAULT '[]',
          intents TEXT NOT NULL DEFAULT '[]',
          slots TEXT NOT NULL DEFAULT '{}',
          tools_called TEXT NOT NULL DEFAULT '[]',
          health_scores TEXT NOT NULL DEFAULT '[]',
          final_status TEXT NOT NULL DEFAULT 'active'
            CHECK (final_status IN ('active', 'resolved', 'escalated', 'abandoned')),
          relationship_score_start REAL,
          relationship_score_end REAL,
          relationship_delta REAL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at DATETIME
        )
        """
    )
    connection.execute(
        """
        INSERT INTO conversations(
          session_id, customer_id, messages, health_scores, final_status,
          relationship_score_start, relationship_score_end, relationship_delta, completed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("sess-prior-001", "CUST-1001", "[]", "[45, 50]", "resolved", 50, 50, 0, "2026-05-20T10:00:00+00:00"),
    )
    connection.execute(
        """
        INSERT INTO conversations(
          session_id, customer_id, messages, health_scores, final_status,
          relationship_score_start, relationship_score_end, relationship_delta, completed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("sess-prior-002", "CUST-1001", "[]", "[65, 70]", "resolved", 61.76, 70, 8.24, "2026-05-21T10:00:00+00:00"),
    )
    connection.execute(
        "INSERT INTO conversations(session_id, customer_id, messages, health_scores) VALUES (?, ?, ?, ?)",
        ("sess-001", "CUST-1001", "[]", "[62, 80]"),
    )
    return connection


def fake_synonymy_embeddings(labels: list[str]) -> list[list[float]]:
    vectors = {
        "customer": [1.0, 0.0, 0.0],
        "duplicate charge": [0.0, 1.0, 0.0],
        "invoice INV-8821": [0.0, 0.9, 0.1],
        "internet": [0.0, 0.0, 1.0],
        "Chennai Zone 04": [0.0, 0.0, 0.95],
    }
    return [vectors.get(label, [0.2, 0.2, 0.2]) for label in labels]


def assert_indexes_session_at_close() -> None:
    connection = make_connection_with_conversation()
    vector_store = FakeVectorStore()
    llm = FakeLLM()
    manager = MemoryManager(
        vector_store=vector_store,
        graph_connection=connection,
        llm_client=llm,
        synonymy_embedding_function=fake_synonymy_embeddings,
    )

    summary = manager.index_session(
        customer_id="CUST-1001",
        session_id="sess-001",
        session_transcript=[
            {
                "role": "customer",
                "content": "I was charged twice for invoice INV-8821 and my internet was down in Chennai Zone 04.",
            },
            {
                "role": "assistant",
                "content": "I found a possible duplicate charge and outage record.",
            },
        ],
        final_status="resolved",
    )

    if summary.customer_id != "CUST-1001" or summary.session_id != "sess-001":
        raise AssertionError(f"summary identifiers wrong: {summary.to_dict()}")
    if summary.units_indexed < 3:
        raise AssertionError(f"expected atomic memory units from the transcript: {summary.to_dict()}")
    if summary.triples_indexed != 3:
        raise AssertionError(f"unexpected triple count: {summary.to_dict()}")
    if summary.extraction_errors:
        raise AssertionError(f"happy path should not report extraction errors: {summary.to_dict()}")
    if len(summary.memory_ids) != summary.units_indexed:
        raise AssertionError(f"memory ids must align to units: {summary.to_dict()}")
    if not summary.session_closed:
        raise AssertionError(f"conversation should be marked closed: {summary.to_dict()}")
    if len(vector_store.calls) != 1:
        raise AssertionError("vector store should be called once")
    if len(llm.prompts) != summary.units_indexed:
        raise AssertionError("OpenIE should run once per memory unit")

    duplicate_node = get_memory_graph_node(connection, "CUST-1001", "duplicate_charge")
    if duplicate_node is None:
        raise AssertionError("duplicate charge node missing")
    if not any(edge["target_node"] == "invoice_inv_8821" for edge in duplicate_node["edges"]):
        raise AssertionError(f"duplicate charge graph edge missing: {duplicate_node}")

    nodes = list_memory_graph_nodes(connection, "CUST-1001")
    if not any(edge["relation"] == "synonymy" for node in nodes for edge in node["edges"]):
        raise AssertionError("synonymy enrichment should run after graph indexing")

    row = connection.execute(
        """
        SELECT final_status, completed_at, relationship_score_start, relationship_score_end, relationship_delta
        FROM conversations
        WHERE session_id = ?
        """,
        ("sess-001",),
    ).fetchone()
    if row[0] != "resolved" or not row[1]:
        raise AssertionError(f"conversation close fields wrong: {row}")
    if (row[2], row[3], row[4]) != (61.76, 70.09, 8.33):
        raise AssertionError(f"relationship scores should persist on close: {row}")


def assert_malformed_openie_does_not_strand_vector_writes() -> None:
    connection = make_connection_with_conversation()
    vector_store = FakeVectorStore()
    llm = PartiallyMalformedLLM()
    manager = MemoryManager(
        vector_store=vector_store,
        graph_connection=connection,
        llm_client=llm,
        synonymy_embedding_function=fake_synonymy_embeddings,
    )

    summary = manager.index_session(
        customer_id="CUST-1001",
        session_id="sess-001",
        session_transcript=[
            {
                "role": "customer",
                "content": "I was charged twice for invoice INV-8821.",
            },
            {
                "role": "customer",
                "content": "My internet was down in Chennai Zone 04.",
            },
        ],
        final_status="resolved",
    )

    if len(vector_store.calls) != 1:
        raise AssertionError("vector store should still receive all memory units")
    if len(summary.memory_ids) != summary.units_indexed:
        raise AssertionError(f"memory ids should remain aligned after OpenIE failure: {summary.to_dict()}")
    if not summary.extraction_errors:
        raise AssertionError(f"malformed OpenIE response should be reported: {summary.to_dict()}")
    if summary.extraction_errors[0]["error_type"] != "ValueError":
        raise AssertionError(f"OpenIE parse error details missing: {summary.to_dict()}")
    if not summary.session_closed:
        raise AssertionError(f"session should still close after partial OpenIE failure: {summary.to_dict()}")

    duplicate_node = get_memory_graph_node(connection, "CUST-1001", "duplicate_charge")
    if duplicate_node is None:
        raise AssertionError("valid memory units should still update the graph")
    internet_node = get_memory_graph_node(connection, "CUST-1001", "internet")
    if internet_node is not None:
        raise AssertionError(f"malformed OpenIE unit should not create graph nodes: {internet_node}")


def assert_punctuation_only_triples_do_not_abort_indexing() -> None:
    connection = make_connection_with_conversation()
    vector_store = FakeVectorStore()
    manager = MemoryManager(
        vector_store=vector_store,
        graph_connection=connection,
        llm_client=PunctuationOnlyTripleLLM(),
        synonymy_embedding_function=fake_synonymy_embeddings,
    )

    summary = manager.index_session(
        customer_id="CUST-1001",
        session_id="sess-001",
        session_transcript=[
            {
                "role": "customer",
                "content": "Please remember this odd note.",
            }
        ],
        final_status="resolved",
    )

    if len(vector_store.calls) != 1:
        raise AssertionError("vector writes should happen even when graph labels are unusable")
    if summary.graph_nodes_upserted != 0 or summary.graph_edges_upserted != 0:
        raise AssertionError(f"punctuation-only triples should be skipped by the graph: {summary.to_dict()}")
    if summary.extraction_errors:
        raise AssertionError(f"valid JSON with unusable labels should be skipped, not reported as extraction failure: {summary.to_dict()}")
    if not summary.session_closed:
        raise AssertionError(f"session should close after punctuation-only triples: {summary.to_dict()}")


def assert_empty_session_closes_without_indexing() -> None:
    connection = make_connection_with_conversation()
    vector_store = FakeVectorStore()
    manager = MemoryManager(
        vector_store=vector_store,
        graph_connection=connection,
        llm_client=FakeLLM(),
        synonymy_embedding_function=fake_synonymy_embeddings,
    )
    summary = manager.index_session(
        customer_id="CUST-1001",
        session_id="sess-001",
        session_transcript=[],
        final_status="abandoned",
    )
    if summary.units_indexed != 0 or summary.memory_ids:
        raise AssertionError(f"empty transcript should not index memory: {summary.to_dict()}")
    if vector_store.calls:
        raise AssertionError("vector store should not be called for empty sessions")
    if not summary.session_closed:
        raise AssertionError("empty session should still close the conversation")
    row = connection.execute(
        """
        SELECT relationship_score_start, relationship_score_end, relationship_delta
        FROM conversations
        WHERE session_id = ?
        """,
        ("sess-001",),
    ).fetchone()
    if (row[0], row[1], row[2]) != (61.76, 70.09, 8.33):
        raise AssertionError(f"empty session should still persist relationship scores: {row}")


def assert_retrieve_merges_vector_and_graph_results() -> None:
    connection = make_connection_with_conversation()
    vector_store = FakeVectorStore()
    manager = MemoryManager(
        vector_store=vector_store,
        graph_connection=connection,
        llm_client=FakeLLM(),
        synonymy_embedding_function=fake_synonymy_embeddings,
    )
    from backend.agent.memory_graph import update_memory_graph

    update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-shared",
        triples=[
            {
                "subject": "duplicate charge",
                "relation": "involved",
                "object": "invoice INV-8821",
                "confidence": 0.9,
                "evidence": "duplicate charge involved invoice INV-8821",
            }
        ],
    )
    update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-graph",
        triples=[
            {
                "subject": "invoice INV-8821",
                "relation": "linked_to",
                "object": "refund review",
                "confidence": 0.9,
                "evidence": "invoice INV-8821 linked to refund review",
            }
        ],
    )

    results = manager.retrieve(
        customer_id="CUST-1001",
        query="duplicate charge refund",
        top_k=3,
    )
    result_by_id = {result.memory_id: result for result in results}
    if set(result_by_id) != {"mem-shared", "mem-vector", "mem-graph"}:
        raise AssertionError(f"unexpected merged retrieval ids: {[result.to_dict() for result in results]}")

    shared = result_by_id["mem-shared"]
    if shared.sources != ["vector", "graph"]:
        raise AssertionError(f"shared result should merge both sources: {shared.to_dict()}")
    if shared.vector_rank is None or shared.graph_rank is None:
        raise AssertionError(f"shared result should retain ranks: {shared.to_dict()}")

    graph_only = result_by_id["mem-graph"]
    if graph_only.sources != ["graph"] or not graph_only.document:
        raise AssertionError(f"graph-only result should hydrate document from vector store: {graph_only.to_dict()}")
    if "invoice_inv_8821" not in graph_only.supporting_nodes:
        raise AssertionError(f"graph-only result should keep supporting nodes: {graph_only.to_dict()}")

    if vector_store.hybrid_calls[0]["top_k"] != 6:
        raise AssertionError(f"retrieve should overfetch vector results: {vector_store.hybrid_calls}")
    if "duplicate charge" not in vector_store.hybrid_calls[0]["query_text"]:
        raise AssertionError(f"retrieve should use fact-augmented query: {vector_store.hybrid_calls}")


def assert_builds_memory_citation_context() -> None:
    connection = make_connection_with_conversation()
    vector_store = FakeVectorStore()
    manager = MemoryManager(
        vector_store=vector_store,
        graph_connection=connection,
        llm_client=FakeLLM(),
        synonymy_embedding_function=fake_synonymy_embeddings,
    )
    from backend.agent.memory_graph import update_memory_graph

    update_memory_graph(
        connection,
        customer_id="CUST-1001",
        memory_id="mem-shared",
        triples=[
            {
                "subject": "duplicate charge",
                "relation": "involved",
                "object": "invoice INV-8821",
                "confidence": 0.9,
                "evidence": "duplicate charge involved invoice INV-8821",
            }
        ],
    )

    contexts = manager.retrieve_citation_context(
        customer_id="CUST-1001",
        query="duplicate charge refund",
        top_k=2,
        max_chars_per_memory=80,
    )
    if [context.citation_id for context in contexts] != ["M1", "M2"]:
        raise AssertionError(f"citation ids wrong: {[context.to_dict() for context in contexts]}")
    if any(len(context.text) > 80 for context in contexts):
        raise AssertionError(f"citation text was not truncated: {[context.to_dict() for context in contexts]}")

    formatted = format_memory_citation_context(contexts)
    if "[M1]" not in formatted or "memory_id=" not in formatted or "score=" not in formatted:
        raise AssertionError(f"formatted citation context missing fields: {formatted}")

    manual = build_memory_citation_context(manager.retrieve(customer_id="CUST-1001", query="duplicate charge", top_k=1))
    if len(manual) != 1 or manual[0].citation_id != "M1":
        raise AssertionError(f"manual citation context wrong: {[context.to_dict() for context in manual]}")


def assert_rejects_bad_inputs() -> None:
    manager = MemoryManager(
        vector_store=FakeVectorStore(),
        graph_connection=sqlite3.connect(":memory:"),
        llm_client=FakeLLM(),
        synonymy_embedding_function=fake_synonymy_embeddings,
    )
    bad_calls = [
        {"customer_id": "", "session_id": "sess-001", "session_transcript": []},
        {"customer_id": "CUST-1001", "session_id": "", "session_transcript": []},
        {"customer_id": "CUST-1001", "session_id": "sess-001", "session_transcript": [], "final_status": "done"},
    ]
    for kwargs in bad_calls:
        try:
            manager.index_session(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad index_session inputs were accepted: {kwargs}")

    retrieve_bad_calls = [
        {"customer_id": "", "query": "billing"},
        {"customer_id": "CUST-1001", "query": ""},
        {"customer_id": "CUST-1001", "query": "billing", "top_k": 0},
    ]
    for kwargs in retrieve_bad_calls:
        try:
            manager.retrieve(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad retrieve inputs were accepted: {kwargs}")

    try:
        build_memory_citation_context([], max_items=0)
    except ValueError:
        pass
    else:
        raise AssertionError("bad citation max_items was accepted")


def assert_manager_closes_only_owned_graph_connection() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-memory-close-")) / "resolveflow.db"
    manager = MemoryManager(
        vector_store=FakeVectorStore(),
        db_path=db_path,
        llm_client=FakeLLM(),
        synonymy_embedding_function=fake_synonymy_embeddings,
    )
    manager.close()
    manager.close()

    try:
        manager.graph_connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError("owned graph connection should close with the manager")

    try:
        manager.retrieve(customer_id="CUST-1001", query="duplicate charge")
    except RuntimeError:
        pass
    else:
        raise AssertionError("closed manager should reject new operations")

    injected_connection = sqlite3.connect(":memory:")
    scoped = MemoryManager(
        vector_store=FakeVectorStore(),
        graph_connection=injected_connection,
        llm_client=FakeLLM(),
        synonymy_embedding_function=fake_synonymy_embeddings,
    )
    with scoped:
        pass
    injected_connection.execute("SELECT 1")
    injected_connection.close()


def main() -> None:
    assert_indexes_session_at_close()
    assert_malformed_openie_does_not_strand_vector_writes()
    assert_punctuation_only_triples_do_not_abort_indexing()
    assert_empty_session_closes_without_indexing()
    assert_retrieve_merges_vector_and_graph_results()
    assert_builds_memory_citation_context()
    assert_rejects_bad_inputs()
    assert_manager_closes_only_owned_graph_connection()
    print("memory manager tests passed")


if __name__ == "__main__":
    main()
