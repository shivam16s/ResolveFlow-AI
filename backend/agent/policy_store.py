from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import chromadb


DEFAULT_CHROMA_PATH = Path(__file__).resolve().parents[2] / "data" / "chroma"
DEFAULT_POLICY_COLLECTION = "resolveflow_policies"
DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[2] / "docs" / "policies"


@dataclass(frozen=True)
class PolicyDocument:
    policy_id: str
    title: str
    version: int
    effective_date: str
    owner: str
    source_path: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PolicyChunk:
    chunk_id: str
    policy_id: str
    title: str
    version: int
    effective_date: str
    owner: str
    source_path: str
    chunk_index: int
    chunk_count: int
    token_count: int
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PolicyIngestionSummary:
    collection_name: str
    policy_count: int
    chunk_count: int
    ids: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


class ChromaPolicyStore:
    """Persistent ChromaDB store for policy documents."""

    def __init__(
        self,
        persist_path: Path = DEFAULT_CHROMA_PATH,
        collection_name: str = DEFAULT_POLICY_COLLECTION,
    ) -> None:
        self.persist_path = Path(persist_path)
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.persist_path))
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def ingest_policy_docs(
        self,
        policy_dir: Path = DEFAULT_POLICY_DIR,
        *,
        expected_count: int | None = None,
        max_tokens: int = 300,
        overlap_tokens: int = 50,
    ) -> PolicyIngestionSummary:
        documents = load_policy_documents(policy_dir)
        if expected_count is not None and len(documents) != expected_count:
            raise ValueError(
                f"expected {expected_count} policy docs, found {len(documents)} in {policy_dir}")

        chunks = [
            chunk
            for document in documents
            for chunk in chunk_policy_document(
                document,
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
            )
        ]
        for policy_id in {document.policy_id for document in documents}:
            self._delete_policy_chunks(policy_id)

        ids = [chunk.chunk_id for chunk in chunks]
        self.collection.upsert(
            ids=ids,
            documents=[chunk.text for chunk in chunks],
            metadatas=[
                {
                    "policy_id": chunk.policy_id,
                    "title": chunk.title,
                    "version": chunk.version,
                    "effective_date": chunk.effective_date,
                    "owner": chunk.owner,
                    "source_path": chunk.source_path,
                    "document_type": "policy",
                    "chunk_index": chunk.chunk_index,
                    "chunk_count": chunk.chunk_count,
                    "token_count": chunk.token_count,
                    "chunk_token_limit": max_tokens,
                    "chunk_overlap": overlap_tokens,
                }
                for chunk in chunks
            ],
        )
        return PolicyIngestionSummary(
            collection_name=self.collection.name,
            policy_count=len(documents),
            chunk_count=len(chunks),
            ids=ids,
        )

    def query(self, query_text: str, top_k: int = 5, where: dict | None = None) -> dict:
        normalized = re.sub(r"\s+", " ", query_text.strip())
        if not normalized:
            raise ValueError("query_text must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")

        kwargs = {
            "query_texts": [normalized],
            "n_results": min(top_k, max(self.collection.count(), 1)),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        return self.collection.query(**kwargs)

    def _delete_policy_chunks(self, policy_id: str) -> None:
        existing = self.collection.get(
            where={"policy_id": policy_id},
            include=[],
        )
        ids = list(existing.get("ids") or [])
        if ids:
            self.collection.delete(ids=ids)


def load_policy_documents(policy_dir: Path = DEFAULT_POLICY_DIR) -> list[PolicyDocument]:
    policy_dir = Path(policy_dir)
    if not policy_dir.exists():
        raise FileNotFoundError(
            f"policy directory does not exist: {policy_dir}")

    documents = []
    for path in sorted(policy_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        documents.append(_parse_policy_document(path, text))
    return documents


def chunk_policy_document(
    document: PolicyDocument,
    *,
    max_tokens: int = 300,
    overlap_tokens: int = 50,
) -> list[PolicyChunk]:
    if max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    if overlap_tokens < 0:
        raise ValueError("overlap_tokens must not be negative")
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be smaller than max_tokens")

    tokens = _tokens(document.text)
    if not tokens:
        return []

    windows = []
    step = max_tokens - overlap_tokens
    start = 0
    while start < len(tokens):
        window_tokens = tokens[start: start + max_tokens]
        windows.append(window_tokens)
        if start + max_tokens >= len(tokens):
            break
        start += step

    chunk_count = len(windows)
    return [
        PolicyChunk(
            chunk_id=_policy_chunk_id(document, chunk_index=index),
            policy_id=document.policy_id,
            title=document.title,
            version=document.version,
            effective_date=document.effective_date,
            owner=document.owner,
            source_path=document.source_path,
            chunk_index=index,
            chunk_count=chunk_count,
            token_count=len(window_tokens),
            text=f"# {document.title}\n" + " ".join(window_tokens),
        )
        for index, window_tokens in enumerate(windows)
    ]


def _parse_policy_document(path: Path, text: str) -> PolicyDocument:
    title = _extract_title(text) or path.stem.replace("_", " ").title()
    policy_id = _extract_header_value(text, "Policy ID") or path.stem
    version_text = _extract_header_value(text, "Version") or "1"
    effective_date = _extract_header_value(text, "Effective date") or ""
    owner = _extract_header_value(text, "Owner") or ""

    try:
        version = int(version_text)
    except ValueError as exc:
        raise ValueError(
            f"invalid policy version in {path}: {version_text}") from exc

    return PolicyDocument(
        policy_id=policy_id,
        title=title,
        version=version,
        effective_date=effective_date,
        owner=owner,
        source_path=str(path),
        text=text,
    )


def _extract_title(text: str) -> str:
    match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_header_value(text: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.+)$",
                      text, flags=re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _policy_document_id(document: PolicyDocument) -> str:
    key = f"{document.policy_id}|v{document.version}|{document.source_path}"
    return f"policy-{uuid5(NAMESPACE_URL, key)}"


def _policy_chunk_id(document: PolicyDocument, *, chunk_index: int) -> str:
    if chunk_index == 0:
        return _policy_document_id(document)
    key = f"{document.policy_id}|v{document.version}|{document.source_path}|chunk:{chunk_index}"
    return f"policy-{uuid5(NAMESPACE_URL, key)}"


def _tokens(text: str) -> list[str]:
    return re.findall(r"\S+", text)
