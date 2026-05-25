from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.api import app, create_app  # noqa: E402


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


def main() -> None:
    assert_create_app_returns_fastapi_app()
    assert_health_endpoint_contract()
    assert_openapi_schema_is_available()
    print("fastapi scaffold tests passed")


if __name__ == "__main__":
    main()
