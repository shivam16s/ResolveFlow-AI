from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.db.init_db import DEFAULT_DB_PATH
from backend.agent.policy_store import DEFAULT_POLICY_DIR

from .routes import dashboard_router, health_router, tools_router
from .test_routes import router as test_router


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
    app.include_router(test_router)
    return app


app = create_app(db_path=_runtime_db_path())
