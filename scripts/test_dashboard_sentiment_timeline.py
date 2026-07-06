from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api import create_app  # noqa: E402
from backend.api.dashboard_routes import _evaluation_report  # noqa: E402
from backend.db.seed_demo_dashboard import DEMO_SESSION_ID, seed_demo_dashboard  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_case_detail_exposes_sentiment_timeline() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-sentiment-timeline-")) / "resolveflow.db"
    seed_demo_dashboard(db_path)

    with TestClient(create_app(db_path=db_path)) as client:
        response = client.get(f"/api/cases/{DEMO_SESSION_ID}")

    assert response.status_code == 200, response.text
    timeline = response.json()["health_score_timeline"]
    assert timeline
    assert all("sentiment_score" in point for point in timeline)
    assert all("sentiment_label" in point for point in timeline)
    assert timeline[0]["sentiment_label"] == "angry"
    assert 0 <= timeline[0]["sentiment_score"] <= 1


def test_evaluation_report_groups_temperature_results() -> None:
    report = _evaluation_report(
        {
            "scenario_count": 1,
            "success_rate": 0.5,
            "pass_k": 2,
            "results": [
                {
                    "scenario_id": "case_01_simple_bill_question",
                    "pass_index": 1,
                    "passed": True,
                    "score": 1.0,
                    "temperature": 0.3,
                    "policies_retrieved": [],
                    "failures": [],
                    "artifacts": {},
                },
                {
                    "scenario_id": "case_01_simple_bill_question",
                    "pass_index": 2,
                    "passed": False,
                    "score": 0.4,
                    "temperature": 0.8,
                    "policies_retrieved": [],
                    "failures": ["miss"],
                    "artifacts": {},
                },
            ],
        },
        run_id="temp-test",
        run_at="2026-07-05T00:00:00",
        db_path=None,
    )
    rows = report["temperature_results"]
    assert [row["temperature"] for row in rows] == [0.3, 0.8]
    assert [row["pass_rate"] for row in rows] == [1.0, 0.0]


if __name__ == "__main__":
    test_case_detail_exposes_sentiment_timeline()
    test_evaluation_report_groups_temperature_results()
    print("dashboard sentiment timeline tests passed")
