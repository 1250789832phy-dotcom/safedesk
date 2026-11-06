import sys

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from agent.planner import MockPlanner
from agent.service import AgentService
from apps.api.main import create_app


windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Configuration storage uses Windows DPAPI (crypt32)",
)

HEADERS = {"X-SafeDesk-Settings": "1", "Origin": "http://127.0.0.1:8000"}
CREDENTIALS = {
    "base_url": "https://upstream.example/v1",
    "api_key": "discovery-test-key-not-a-real-credential",
}
ENDPOINT = "/settings/model/models"


def client_at(tmp_path):
    return TestClient(
        create_app(
            AgentService(), settings_path=tmp_path / "settings.json",
            planner_factory=lambda config: MockPlanner(),
        ),
        base_url="http://127.0.0.1:8000",
    )


def upstream(monkeypatch, *, ids=None, status=200, raw=None, error=None, malformed=False):
    requests, clients = [], []

    def handle(request):
        requests.append(request)
        if error:
            raise error
        if malformed:
            return httpx.Response(200, text="not-json " + CREDENTIALS["api_key"])
        body = raw if raw is not None else {
            "object": "list",
            "data": [
                {"id": model, "object": "model", "created": 0, "owned_by": "test"}
                for model in (ids or [])
            ],
        }
        headers = {"Location": "https://redirect.example/v1/models"} if status == 302 else {}
        return httpx.Response(status, json=body, headers=headers)

    def factory(credentials):
        client = OpenAI(
            api_key=credentials.api_key.get_secret_value(),
            base_url=credentials.base_url, max_retries=0,
            http_client=httpx.Client(
                transport=httpx.MockTransport(handle), follow_redirects=False
            ),
        )
        clients.append(client)
        return client

    # Patch the transport boundary, not the discovery operation.
    monkeypatch.setattr("agent.settings.build_client", factory, raising=False)
    return requests, clients


def test_discovery_uses_sdk_models_endpoint_without_model_or_persistence(tmp_path, monkeypatch):
    requests, clients = upstream(
        monkeypatch, ids=["model-z", "model-a", "model-a", "", CREDENTIALS["api_key"]]
    )
    client = client_at(tmp_path)
    response = client.post(ENDPOINT, json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["models"] == ["model-a", "model-z"]
    assert response.json()["truncated"] is False
    assert response.headers["cache-control"] == "no-store"
    assert CREDENTIALS["api_key"] not in response.text
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v1/models"
    assert requests[0].headers["authorization"] == "Bearer " + CREDENTIALS["api_key"]
    assert requests[0].content == b""
    assert clients[0].is_closed
    assert not (tmp_path / "settings.json").exists()
    assert client.get("/settings/model").json()["mode"] == "mock"


@windows_only
def test_discovery_reuses_saved_key_without_changing_active_configuration(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    payload = {**CREDENTIALS, "model": "chosen-before-discovery", "protocol": "responses"}
    assert client.put("/settings/model", json=payload, headers=HEADERS).status_code == 200
    before = (tmp_path / "settings.json").read_bytes()
    requests, clients = upstream(monkeypatch, ids=["another-model"])
    response = client.post(
        ENDPOINT, json={"base_url": CREDENTIALS["base_url"], "api_key": ""}, headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json()["models"] == ["another-model"]
    assert requests[0].headers["authorization"] == "Bearer " + CREDENTIALS["api_key"]
    assert (tmp_path / "settings.json").read_bytes() == before
    assert client.get("/settings/model").json()["model"] == payload["model"]
    assert clients[0].is_closed


@windows_only
def test_blank_key_cannot_be_sent_to_different_upstream(tmp_path, monkeypatch):
    client = client_at(tmp_path)
    saved = client.put(
        "/settings/model", json={**CREDENTIALS, "model": "saved"}, headers=HEADERS
    )
    assert saved.status_code == 200
    requests, _ = upstream(monkeypatch)
    response = client.post(
        ENDPOINT, json={"base_url": "https://another.example/v1"}, headers=HEADERS
    )
    assert response.status_code == 400
    assert requests == []


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 302])
def test_upstream_errors_are_sanitized_and_client_closed(tmp_path, monkeypatch, status):
    requests, clients = upstream(
        monkeypatch, status=status, raw={"error": {"message": CREDENTIALS["api_key"]}}
    )
    response = client_at(tmp_path).post(ENDPOINT, json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 502
    assert CREDENTIALS["api_key"] not in response.text
    assert len(requests) == 1
    assert clients[0].is_closed


def test_timeout_is_sanitized_and_does_not_retry(tmp_path, monkeypatch):
    requests, clients = upstream(
        monkeypatch, error=httpx.ReadTimeout(CREDENTIALS["api_key"])
    )
    response = client_at(tmp_path).post(ENDPOINT, json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 502
    assert CREDENTIALS["api_key"] not in response.text
    assert len(requests) == 1
    assert clients[0].is_closed


@pytest.mark.parametrize("body", [{}, {"data": None}, {"data": "wrong"}])
def test_invalid_upstream_schema_is_an_error_not_empty_success(tmp_path, monkeypatch, body):
    upstream(monkeypatch, raw=body)
    response = client_at(tmp_path).post(ENDPOINT, json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 502


def test_malformed_provider_json_is_not_reported_as_missing_key(tmp_path, monkeypatch):
    upstream(monkeypatch, malformed=True)
    response = client_at(tmp_path).post(ENDPOINT, json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 502
    assert CREDENTIALS["api_key"] not in response.text


def test_empty_catalog_is_reported_honestly(tmp_path, monkeypatch):
    upstream(monkeypatch, ids=[])
    response = client_at(tmp_path).post(ENDPOINT, json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["models"] == []
    assert "未返回" in response.json()["message"]


def test_model_catalog_has_a_bounded_result(tmp_path, monkeypatch):
    upstream(monkeypatch, ids=[f"model-{index:04d}" for index in range(2005)])
    response = client_at(tmp_path).post(ENDPOINT, json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 200
    assert len(response.json()["models"]) == 2000
    assert response.json()["truncated"] is True


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-SafeDesk-Settings": "1", "Origin": "https://foreign.example"}],
)
def test_discovery_preserves_local_settings_boundary(tmp_path, monkeypatch, headers):
    requests, _ = upstream(monkeypatch)
    response = client_at(tmp_path).post(ENDPOINT, json=CREDENTIALS, headers=headers)
    assert response.status_code == 403
    assert requests == []


def test_credentials_validation_never_echoes_secret(tmp_path):
    response = client_at(tmp_path).post(
        ENDPOINT,
        json={**CREDENTIALS, "base_url": "https://user:secret@upstream.example/v1"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert CREDENTIALS["api_key"] not in response.text
    assert "user:secret" not in response.text


def test_page_provides_explicit_model_fetch_and_native_selector(tmp_path):
    page = client_at(tmp_path).get("/").text
    assert 'id="fetch-models"' in page
    assert '<select id="upstream-model"' in page
    assert 'id="model-name"' in page
    assert "localStorage" not in page
    assert "sessionStorage" not in page
