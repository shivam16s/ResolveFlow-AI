from __future__ import annotations

from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.policy_store import ChromaPolicyStore, PolicyDocument, chunk_policy_document, load_policy_documents


POLICY_DIR = ROOT / "docs" / "policies"


def test_loads_all_policy_documents_with_metadata() -> None:
    documents = load_policy_documents(POLICY_DIR)

    assert len(documents) == 8
    policy_ids = {document.policy_id for document in documents}
    assert policy_ids == {
        "cancellation_policy",
        "duplicate_charge_policy",
        "escalation_policy",
        "payment_failure_policy",
        "plan_change_policy",
        "refund_policy",
        "service_credit_policy",
        "technician_visit_policy",
    }
    assert all(document.title.endswith("Policy") for document in documents)
    assert all(document.version == 1 for document in documents)
    assert all(document.effective_date == "2026-01-01" for document in documents)
    assert all(document.owner for document in documents)
    assert all(document.text.startswith("# ") for document in documents)


def test_chunks_policy_documents_to_token_windows_with_overlap() -> None:
    document = PolicyDocument(
        policy_id="synthetic_policy",
        title="Synthetic Policy",
        version=1,
        effective_date="2026-01-01",
        owner="Test",
        source_path="synthetic.md",
        text=" ".join(f"token{i}" for i in range(25)),
    )

    chunks = chunk_policy_document(document, max_tokens=10, overlap_tokens=3)
    assert [chunk.token_count for chunk in chunks] == [10, 10, 10, 4]
    assert all(chunk.chunk_count == 4 for chunk in chunks)

    # Each chunk is prefixed with a "# {title}" header for retrieval context, so
    # the token-overlap invariant is checked on the content body (after the header).
    def _body_tokens(chunk):
        return chunk.text.split("\n", 1)[-1].split()

    assert _body_tokens(chunks[0])[-3:] == _body_tokens(chunks[1])[:3]
    assert _body_tokens(chunks[1])[-3:] == _body_tokens(chunks[2])[:3]
    assert _body_tokens(chunks[2])[-3:] == _body_tokens(chunks[3])[:3]
    assert chunks[0].chunk_id != chunks[1].chunk_id

    real_chunks = [
        chunk
        for policy in load_policy_documents(POLICY_DIR)
        for chunk in chunk_policy_document(policy)
    ]
    assert len(real_chunks) == 8
    assert all(chunk.token_count <= 300 for chunk in real_chunks)
    assert all(chunk.chunk_index == 0 and chunk.chunk_count == 1 for chunk in real_chunks)


def test_ingests_8_policy_docs_into_chromadb_collection() -> None:
    persist_path = ROOT / "data" / f"chroma_test_policies_{uuid4().hex}"
    store = ChromaPolicyStore(
        persist_path=persist_path,
        collection_name="resolveflow_policies_test",
    )

    summary = store.ingest_policy_docs(POLICY_DIR)
    assert summary.collection_name == "resolveflow_policies_test"
    assert summary.policy_count == 8
    assert summary.chunk_count == 8
    assert len(summary.ids) == 8
    assert store.collection.count() == 8

    second_summary = store.ingest_policy_docs(POLICY_DIR)
    assert second_summary.ids == summary.ids
    assert store.collection.count() == 8

    stored = store.collection.get(ids=summary.ids, include=["documents", "metadatas"])
    assert len(stored["documents"]) == 8
    assert {metadata["policy_id"] for metadata in stored["metadatas"]} == {
        "cancellation_policy",
        "duplicate_charge_policy",
        "escalation_policy",
        "payment_failure_policy",
        "plan_change_policy",
        "refund_policy",
        "service_credit_policy",
        "technician_visit_policy",
    }
    assert all(metadata["document_type"] == "policy" for metadata in stored["metadatas"])
    assert all(metadata["chunk_index"] == 0 for metadata in stored["metadatas"])
    assert all(metadata["chunk_count"] == 1 for metadata in stored["metadatas"])
    assert all(metadata["token_count"] <= 300 for metadata in stored["metadatas"])
    assert all(metadata["chunk_token_limit"] == 300 for metadata in stored["metadatas"])
    assert all(metadata["chunk_overlap"] == 50 for metadata in stored["metadatas"])

    results = store.query("duplicate payment charged twice refund", top_k=2)
    result_policy_ids = [metadata["policy_id"] for metadata in results["metadatas"][0]]
    assert "duplicate_charge_policy" in result_policy_ids


def test_rejects_wrong_expected_policy_count_and_bad_query() -> None:
    persist_path = ROOT / "data" / f"chroma_test_policies_bad_{uuid4().hex}"
    store = ChromaPolicyStore(
        persist_path=persist_path,
        collection_name="resolveflow_policies_bad_count_test",
    )

    try:
        store.ingest_policy_docs(POLICY_DIR, expected_count=7)
    except ValueError as exc:
        assert "expected 7 policy docs" in str(exc)
    else:
        raise AssertionError("wrong expected policy count was accepted")

    try:
        store.query("")
    except ValueError:
        pass
    else:
        raise AssertionError("empty policy query was accepted")


def main() -> None:
    test_loads_all_policy_documents_with_metadata()
    test_chunks_policy_documents_to_token_windows_with_overlap()
    test_ingests_8_policy_docs_into_chromadb_collection()
    test_rejects_wrong_expected_policy_count_and_bad_query()
    print("policy ingestion tests passed")


if __name__ == "__main__":
    main()
