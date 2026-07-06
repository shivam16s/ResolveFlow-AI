from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[2] / "docs" / "policies"


def seed_policy_sql_table(
    connection: sqlite3.Connection,
    *,
    policy_dir: Path = DEFAULT_POLICY_DIR,
) -> int:
    """Mirror markdown policy docs into the SQL policies table for inspection/FKs."""
    documents = _load_policy_markdown(policy_dir)
    for document in documents:
        connection.execute(
            """
            INSERT INTO policies (
                policy_id,
                policy_name,
                policy_text,
                effective_date,
                version
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(policy_id) DO UPDATE SET
                policy_name = excluded.policy_name,
                policy_text = excluded.policy_text,
                effective_date = excluded.effective_date,
                version = excluded.version
            """,
            (
                document["policy_id"],
                document["title"],
                document["text"],
                document["effective_date"] or "2026-01-01",
                document["version"],
            ),
        )
    return len(documents)


def _load_policy_markdown(policy_dir: Path) -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    for path in sorted(Path(policy_dir).glob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        documents.append({
            "policy_id": _header(text, "Policy ID") or path.stem,
            "title": _title(text) or path.stem.replace("_", " ").title(),
            "text": text,
            "effective_date": _header(text, "Effective date") or "2026-01-01",
            "version": int(_header(text, "Version") or "1"),
        })
    return documents


def _title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _header(text: str, label: str) -> str:
    prefix = f"{label.lower()}:"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            return stripped.split(":", 1)[1].strip()
    return ""
