from __future__ import annotations

import os
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.db.init_db import DEFAULT_DB_PATH
from backend.agent.policy_store import ChromaPolicyStore, DEFAULT_POLICY_DIR

from .routes import dashboard_router, health_router, tools_router
from .chat_routes import router as chat_router
from .rag_routes import rag_router
from .test_routes import router as test_router


def _expected_policy_chunks(policy_dir: Path) -> int:
    try:
        return sum(1 for _ in Path(policy_dir).glob("*.md"))
    except OSError:
        return 0


def _safe_collection_count(store: ChromaPolicyStore | None) -> int:
    if store is None:
        return 0
    try:
        return store.collection.count()
    except Exception:
        return 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    store: ChromaPolicyStore | None = None
    try:
        store = ChromaPolicyStore()
        app.state.policy_store = store
        # Ingestion is an idempotent write. Skip it when the collection is
        # already populated so a re-launch (or a second worker/process) does not
        # contend for the Chroma SQLite write lock. On Windows that contention
        # surfaces as "disk I/O error (code 2570)" and previously disabled policy
        # retrieval for the whole server.
        existing = _safe_collection_count(store)
        if existing and existing >= _expected_policy_chunks(app.state.policy_dir):
            print(f"Policies already ingested ({existing} chunks); skipping re-ingest.")
        else:
            summary = store.ingest_policy_docs(policy_dir=app.state.policy_dir)
            print(f"Policies ingested successfully ({summary.chunk_count} chunks).")
    except Exception as e:
        # If ingestion failed but a previously populated collection exists, keep
        # serving from it rather than disabling policy retrieval entirely.
        if _safe_collection_count(store) > 0:
            app.state.policy_store = store
            print(f"Policy ingestion skipped ({e}); using existing collection.")
        else:
            app.state.policy_store = None
            print(f"Policy ingestion failed: {e}")
    yield


API_TITLE = "ResolveFlow AI API"
API_VERSION = "0.1.0"


def _runtime_db_path() -> Path:
    configured = os.environ.get("RESOLVEFLOW_DB_PATH")
    if configured:
        return Path(configured)
    demo_path = DEFAULT_DB_PATH.with_name("resolveflow_demo.db")
    return demo_path if demo_path.exists() else DEFAULT_DB_PATH


def create_app(*, db_path: Path = DEFAULT_DB_PATH, policy_dir: Path = DEFAULT_POLICY_DIR) -> FastAPI:
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description="Backend API scaffold for ResolveFlow AI tool-calling and dashboard endpoints.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            "http://127.0.0.1:3001",
            "http://localhost:3001",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.db_path = Path(db_path)
    app.state.policy_dir = Path(policy_dir)
    app.include_router(health_router)
    app.include_router(dashboard_router)
    app.include_router(tools_router)
    app.include_router(chat_router)
    app.include_router(rag_router)
    app.include_router(test_router)
    return app


app = create_app(db_path=_runtime_db_path())
