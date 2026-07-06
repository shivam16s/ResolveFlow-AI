from __future__ import annotations

from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.api import app, create_app  # noqa: E402
import backend.api.dashboard_routes as dashboard_routes  # noqa: E402
import backend.api.rag_routes as rag_routes  # noqa: E402
from backend.db.init_db import initialize_database  # noqa: E402


def assert_create_app_returns_fastapi_app() -> None:
    created = create_app()
    if not isinstance(created, FastAPI):
        raise AssertionError(f"create_app returned wrong type: {type(created)}")
    if created.title != "ResolveFlow AI API":
        raise AssertionError(f"wrong API title: {created.title}")
    routes = {route.path for route in created.routes}
    if "/api/health" not in routes:
        raise AssertionError(f"health route missing: {routes}")


def assert_health_endpoint_contract() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    if response.status_code != 200:
        raise AssertionError(f"health endpoint failed: {response.status_code} {response.text}")

    payload = response.json()
    expected = {
        "status": "ok",
        "service": "resolveflow-api",
        "version": "0.1.0",
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise AssertionError(f"wrong health payload: {payload}")
    if not payload.get("timestamp"):
        raise AssertionError(f"timestamp missing from health payload: {payload}")


def assert_openapi_schema_is_available() -> None:
    client = TestClient(app)
    response = client.get("/openapi.json")
    if response.status_code != 200:
        raise AssertionError(f"openapi endpoint failed: {response.status_code} {response.text}")

    schema = response.json()
    if schema.get("info", {}).get("title") != "ResolveFlow AI API":
        raise AssertionError(f"OpenAPI title wrong: {schema.get('info')}")
    if "/api/health" not in schema.get("paths", {}):
        raise AssertionError("health endpoint missing from OpenAPI schema")


def assert_rag_memory_search_reuses_app_scoped_manager() -> None:
    class FakeMemoryResult:
        def to_dict(self) -> dict:
            return {
                "memory_id": "mem-test-001",
                "document": "Customer had a duplicate charge.",
                "metadata": {"source": "test"},
                "fused_score": 1.0,
                "sources": ["vector"],
                "vector_rank": 1,
                "graph_rank": None,
                "vector_score": 1.0,
                "graph_score": None,
                "supporting_nodes": [],
                "query_nodes": ["duplicate", "charge"],
            }

    class FakeMemoryManager:
        instances = []

        def __init__(self, *, db_path):
            self.db_path = Path(db_path)
            self.calls = []
            self.closed = False
            self.__class__.instances.append(self)

        def retrieve(self, **kwargs):
            self.calls.append(kwargs)
            return [FakeMemoryResult()]

        def close(self) -> None:
            self.closed = True

    original_manager = rag_routes.MemoryManager
    rag_routes.MemoryManager = FakeMemoryManager
    try:
        db_path = Path(tempfile.mkdtemp(prefix="resolveflow-rag-cache-")) / "resolveflow.db"
        with TestClient(create_app(db_path=db_path)) as client:
            for _ in range(2):
                response = client.post(
                    "/api/rag/memory/search",
                    json={
                        "customer_id": "CUST-1001",
                        "query": "duplicate charge",
                        "top_k": 3,
                    },
                )
                if response.status_code != 200:
                    raise AssertionError(f"memory search failed: {response.status_code} {response.text}")

            if len(FakeMemoryManager.instances) != 1:
                raise AssertionError(
                    f"memory search should reuse one manager, got {len(FakeMemoryManager.instances)}"
                )
            instance = FakeMemoryManager.instances[0]
            if len(instance.calls) != 2:
                raise AssertionError(f"cached manager should receive both calls: {instance.calls}")

        if not FakeMemoryManager.instances[0].closed:
            raise AssertionError("app shutdown should close the cached MemoryManager")
    finally:
        rag_routes.MemoryManager = original_manager


def assert_evaluation_run_uses_app_db_path_and_persists_background_result() -> None:
    calls: list[Path] = []

    def fake_run_evaluation(*, k: int, db_path: Path, **_kwargs):
        calls.append(Path(db_path))
        return {
            "pass_k": k,
            "scenario_count": 1,
            "total_runs": 1,
            "success_rate": 1.0,
            "results": [
                {
                    "scenario_id": "case_01_simple_bill_question",
                    "passed": True,
                    "policies_retrieved": [],
                    "artifacts": {"messages": ["hi"]},
                }
            ],
        }

    original_run_evaluation = dashboard_routes.run_evaluation
    dashboard_routes.run_evaluation = fake_run_evaluation
    try:
        db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-route-")) / "active.db"
        with TestClient(create_app(db_path=db_path)) as client:
            response = client.post("/api/evaluation/run")
            if response.status_code != 200:
                raise AssertionError(f"evaluation run failed: {response.status_code} {response.text}")
            payload = response.json()
            if payload.get("status") != "queued":
                raise AssertionError(f"evaluation run should be queued: {payload}")
            if calls != [db_path]:
                raise AssertionError(f"evaluation runner did not use app db_path: {calls}")

            result_file = db_path.parent / f"{payload['run_id']}.json"
            if not result_file.exists():
                raise AssertionError(f"background evaluation result was not persisted: {result_file}")

            latest = client.get("/api/evaluation/results")
            if latest.status_code != 200:
                raise AssertionError(f"evaluation results failed: {latest.status_code} {latest.text}")
            report = latest.json()
            if report.get("run_id") != payload.get("run_id"):
                raise AssertionError(f"latest report did not read queued run: {report} vs {payload}")
            if report.get("pass_rate") != 1.0 or report.get("total_scenarios") != 1:
                raise AssertionError(f"queued evaluation report summary wrong: {report}")
    finally:
        dashboard_routes.run_evaluation = original_run_evaluation


def assert_dashboard_overview_does_not_run_ragas_for_policy_kpi() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-overview-cache-")) / "active.db"
    initialize_database(db_path)
    eval_path = db_path.parent / "eval_20990101_000000_cache.json"
    eval_path.write_text(
        """
        {
          "pass_k": 1,
          "scenario_count": 1,
          "total_runs": 1,
          "success_rate": 1.0,
          "results": [
            {
              "scenario_id": "case_01_simple_bill_question",
              "passed": true,
              "policies_retrieved": ["payment_failure_policy"],
              "artifacts": {"messages": ["hi"]}
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    original_ragas = dashboard_routes.evaluate_policy_retrievals_with_ragas

    def fail_if_called(_evaluation):
        raise AssertionError("dashboard overview should not run full RAGAS scoring")

    dashboard_routes.evaluate_policy_retrievals_with_ragas = fail_if_called
    dashboard_routes._POLICY_COMPLIANCE_PCT_CACHE.clear()
    try:
        with TestClient(create_app(db_path=db_path)) as client:
            first = client.get("/api/dashboard/overview")
            second = client.get("/api/dashboard/overview")
        for response in (first, second):
            if response.status_code != 200:
                raise AssertionError(f"overview failed: {response.status_code} {response.text}")
            payload = response.json()
            if payload.get("policy_compliant_pct") != 100.0:
                raise AssertionError(f"overview policy KPI should use lightweight eval score: {payload}")
    finally:
        dashboard_routes.evaluate_policy_retrievals_with_ragas = original_ragas
        dashboard_routes._POLICY_COMPLIANCE_PCT_CACHE.clear()


def main() -> None:
    assert_create_app_returns_fastapi_app()
    assert_health_endpoint_contract()
    assert_openapi_schema_is_available()
    assert_rag_memory_search_reuses_app_scoped_manager()
    assert_evaluation_run_uses_app_db_path_and_persists_background_result()
    assert_dashboard_overview_does_not_run_ragas_for_policy_kpi()
    print("fastapi scaffold tests passed")


if __name__ == "__main__":
    main()
