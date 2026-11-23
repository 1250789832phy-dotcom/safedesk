import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent.service import AgentService
from apps.api.main import create_app
from evaluation.hub import EvalHub


DATASET = {
    "name": "refunds",
    "documents": {"policy.md": "# Refund\nrefund within 7 days"},
    "cases": [
        {"id": "hit", "query": "refund", "expected_source": "policy.md"},
        {"id": "miss", "query": "unrelatedxyz", "expected_source": None},
    ],
}


def test_dataset_snapshots_are_idempotent_and_reopenable(tmp_path):
    db = tmp_path / "hub.sqlite3"
    hub = EvalHub(db)
    first = hub.register(DATASET)
    assert hub.register(DATASET) == first
    second = hub.register({**DATASET, "documents": {"policy.md": "# Refund\nChanged"}})
    assert (first["version"], second["version"]) == (1, 2)
    assert first["digest"] != second["digest"]
    reopened = EvalHub(db)
    assert reopened.get_dataset("refunds", 1)["documents"] == DATASET["documents"]
    assert [v["version"] for v in reopened.list_datasets()] == [2, 1]


def test_evaluation_runs_compare_final_context_with_same_snapshot(tmp_path):
    db = tmp_path / "hub.sqlite3"
    hub = EvalHub(db)
    hub.register(DATASET)
    before = hub.run("refunds", 1, mode="keyword", rerank=False, token_budget=0)
    after = hub.run("refunds", 1, mode="keyword", rerank=True, token_budget=1200)
    assert before["metrics"] == {
        "cases": 2, "pass_rate": 0.5, "hit_at_1": 1.0, "hit_at_5": 1.0,
        "mrr_at_5": 1.0, "context_hit_rate": 0.0, "no_hit_accuracy": 1.0,
    }
    assert before["cases"][0]["failure"] == "budget_excluded"
    assert after["cases"][0]["context"][0]["source"] == "policy.md"
    reopened = EvalHub(db)
    assert reopened.get_run(after["id"]) == after
    compared = reopened.compare(before["id"], after["id"])
    assert compared["delta"]["context_hit_rate"] == 1.0
    assert compared["changed_cases"] == [{"id": "hit", "before": False, "after": True}]


def test_api_creates_runs_rejects_invalid_cases_and_cross_version_comparison(tmp_path):
    client = TestClient(
        create_app(AgentService(), evaluation_db=tmp_path / "hub.sqlite3"),
        base_url="http://127.0.0.1",
    )
    bad = {**DATASET, "cases": [{"id": "bad", "query": "x", "expected_source": "missing.md"}]}
    assert client.post("/eval/datasets", json=bad).status_code == 422
    assert client.post("/eval/datasets", json=DATASET).status_code == 201
    config = {"dataset": "refunds", "version": 1, "mode": "keyword"}
    response = client.post("/eval/runs", json=config)
    assert response.status_code == 201
    first = response.json()["id"]
    assert client.get(f"/eval/runs/{first}").json()["metrics"]["pass_rate"] == 1.0
    assert len(client.get("/eval/runs").json()) == 1
    assert client.get("/eval/datasets/refunds/9").status_code == 404
    assert client.post("/eval/runs", json={**config, "version": 9}).status_code == 404
    updated = {**DATASET, "documents": {"policy.md": "# Refund\nrefund in 30 days"}}
    assert client.post("/eval/datasets", json=updated).json()["version"] == 2
    second = client.post("/eval/runs", json={**config, "version": 2}).json()["id"]
    assert client.get("/eval/compare", params={"baseline": first, "candidate": second}).status_code == 409
    assert client.get("/eval/compare", params={"baseline": first, "candidate": first}).json()["changed_cases"] == []


def test_eval_api_limits_access_and_caps_dataset_chunks(tmp_path):
    app = create_app(AgentService(), evaluation_db=tmp_path / "hub.sqlite3")
    local = TestClient(app, base_url="http://127.0.0.1")
    assert TestClient(app, base_url="http://other.example").get("/eval/datasets").status_code == 403
    assert local.get("/eval/datasets", headers={"Origin": "http://other.example"}).status_code == 403
    assert local.get("/eval/datasets").headers["cache-control"] == "no-store"
    assert local.get("/eval/datasets/refunds/9223372036854775808").status_code == 422
    too_many = {**DATASET, "documents": {"policy.md": "# Refund\n" * 1001}}
    assert local.post("/eval/datasets", json=too_many).status_code == 422


def test_example_changes_ranking_with_only_rerank_toggled(tmp_path):
    payload = Path(__file__).resolve().parents[1] / "examples" / "retrieval-dataset.json"
    hub = EvalHub(tmp_path / "hub.sqlite3")
    meta = hub.register(json.loads(payload.read_text(encoding="utf-8")))
    args = (meta["name"], meta["version"])
    before = hub.run(*args, rerank=False)
    after = hub.run(*args, rerank=True)
    assert before["metrics"]["hit_at_5"] == 0
    assert after["metrics"]["mrr_at_5"] == 1
    assert hub.compare(before["id"], after["id"])["changed_cases"] == [
        {"id": "policy-hit", "before": False, "after": True}
    ]
