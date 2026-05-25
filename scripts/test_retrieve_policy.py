from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.api import create_app  # noqa: E402
from backend.tools import retrieve_policy  # noqa: E402


POLICY_DIR = ROOT / "docs" / "policies"


def assert_retrieves_policy_by_id_with_relevance() -> None:
    result = retrieve_policy(
        "service_credit_policy",
        query="Can I get service credit for a verified outage lasting 7 hours?",
        policy_dir=POLICY_DIR,
        top_k=2,
    )

    if result is None:
        raise AssertionError("expected service credit policy")
    if result["policy_id"] != "service_credit_policy":
        raise AssertionError(f"wrong policy id: {result}")
    if result["title"] != "Service Credit Policy":
        raise AssertionError(f"wrong title: {result}")
    if "verified broadband service disruption" not in result["text"]:
        raise AssertionError("full policy text should be returned")
    if result["retrieve_decision"]["token"] != "yes":
        raise AssertionError(f"policy query should trigger retrieval: {result['retrieve_decision']}")
    if result["relevance"]["route"] != "correct":
        raise AssertionError(f"service-credit query should be relevant: {result['relevance']}")
    if len(result["evidence_strips"]) != 2:
        raise AssertionError(f"top_k evidence strips wrong: {result['evidence_strips']}")
    if not any("6 continuous hours" in strip["text"] for strip in result["evidence_strips"]):
        raise AssertionError(f"evidence should include duration eligibility: {result['evidence_strips']}")


def assert_retrieves_policy_by_title_or_filename_alias() -> None:
    by_title = retrieve_policy("Refund Policy", query="refund window", policy_dir=POLICY_DIR)
    by_filename = retrieve_policy("duplicate charge policy", query="charged twice", policy_dir=POLICY_DIR)
    if by_title is None or by_title["policy_id"] != "refund_policy":
        raise AssertionError(f"title alias failed: {by_title}")
    if by_filename is None or by_filename["policy_id"] != "duplicate_charge_policy":
        raise AssertionError(f"filename/title alias failed: {by_filename}")


def assert_handles_missing_policy_and_bad_inputs() -> None:
    if retrieve_policy("missing_policy", policy_dir=POLICY_DIR) is not None:
        raise AssertionError("missing policy should return None")

    for kwargs in (
        {"policy_name": "   ", "policy_dir": POLICY_DIR},
        {"policy_name": "refund_policy", "policy_dir": POLICY_DIR, "top_k": 0},
        {"policy_name": "refund_policy", "policy_dir": POLICY_DIR, "query": "   "},
    ):
        try:
            retrieve_policy(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad retrieve_policy inputs were accepted: {kwargs}")


def assert_retrieve_policy_api_endpoint() -> None:
    client = TestClient(create_app(policy_dir=POLICY_DIR))

    response = client.get(
        "/api/tools/retrieve_policy/service_credit_policy",
        params={"query": "verified outage service credit", "top_k": 2},
    )
    if response.status_code != 200:
        raise AssertionError(f"retrieve policy endpoint failed: {response.status_code} {response.text}")
    payload = response.json()
    if payload["tool_name"] != "retrieve_policy" or payload["ok"] is not True:
        raise AssertionError(f"wrong tool envelope: {payload}")
    if payload["result"]["policy_id"] != "service_credit_policy":
        raise AssertionError(f"wrong endpoint policy payload: {payload}")

    missing = client.get("/api/tools/retrieve_policy/missing_policy")
    if missing.status_code != 404:
        raise AssertionError(f"missing policy should return 404: {missing.status_code} {missing.text}")

    invalid = client.get("/api/tools/retrieve_policy/refund_policy?top_k=0")
    if invalid.status_code != 422:
        raise AssertionError(f"bad top_k should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_retrieves_policy_by_id_with_relevance()
    assert_retrieves_policy_by_title_or_filename_alias()
    assert_handles_missing_policy_and_bad_inputs()
    assert_retrieve_policy_api_endpoint()
    print("retrieve policy tests passed")


if __name__ == "__main__":
    main()
