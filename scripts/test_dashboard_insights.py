from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.llm_client import GeminiClientError  # noqa: E402
from backend.api import create_app  # noqa: E402
import backend.api.dashboard_routes as dashboard_routes  # noqa: E402
from backend.db.seed_demo_dashboard import seed_demo_dashboard  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class FailingLLMClient:
    def generate(self, *_args, **_kwargs) -> str:
        raise GeminiClientError("offline")


def test_dashboard_insights_marks_deterministic_fallback() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-dashboard-insights-")) / "resolveflow.db"
    seed_demo_dashboard(db_path)
    original_client = dashboard_routes.LLMClient
    dashboard_routes.LLMClient = lambda: FailingLLMClient()
    try:
        with TestClient(create_app(db_path=db_path)) as client:
            response = client.get("/api/insights")
    finally:
        dashboard_routes.LLMClient = original_client

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["fallback"] is True
    assert payload["source"] == "deterministic_fallback"
    assert payload["error"] == "GeminiClientError"
    assert payload["insights"].startswith("Fallback insight:")


if __name__ == "__main__":
    test_dashboard_insights_marks_deterministic_fallback()
    print("dashboard insights tests passed")
