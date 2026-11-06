import json

import pytest
from fastapi.testclient import TestClient

from agent.service import AgentService
from apps.api.main import create_app


HEADERS = {"X-SafeDesk-Settings": "1", "Origin": "http://127.0.0.1:8000"}
PAYLOAD = {
    "base_url": "https://example.com/v1",
    "model": "user-selected-model",
    "protocol": "chat_completions",
    "api_key": "unit-test-key-not-a-real-credential",
}


def client_at(path, factory=None):
    return TestClient(
        create_app(AgentService(), settings_path=path, planner_factory=factory),
        base_url="http://127.0.0.1:8000",
    )


def test_settings_routes_are_available_and_redacted():
    client = TestClient(create_app(AgentService()), base_url="http://127.0.0.1:8000")
    response = client.get("/settings/model")
    assert response.status_code == 200
    assert "api_key" not in response.json()


def test_page_has_model_configuration_form():
    page = TestClient(create_app(AgentService())).get("/").text
    for field in (
        'id="model-settings"', 'id="api-url"', 'id="api-key"',
        'id="model-name"', 'id="api-protocol"', 'id="save-model"',
        'id="test-model"', 'id="mock-model"', 'id="forget-model"',
    ):
        assert field in page
    assert 'type="password"' in page
    assert "localStorage" not in page
    assert "sessionStorage" not in page


def test_encrypted_configuration_survives_new_app_without_exposing_key(tmp_path):
    path = tmp_path / "model-settings.json"
    client = client_at(path)
    response = client.put("/settings/model", json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 200
    assert PAYLOAD["api_key"] not in response.text
    saved = path.read_text(encoding="utf-8")
    assert PAYLOAD["api_key"] not in saved
    assert json.loads(saved)["encrypted_api_key"]
    reloaded = client_at(path).get("/settings/model").json()
    assert reloaded["mode"] == "api"
    assert reloaded["has_api_key"] is True
    assert reloaded["model"] == PAYLOAD["model"]
    assert "encrypted_api_key" not in reloaded


def test_blank_key_keeps_existing_key_but_not_for_different_url(tmp_path):
    path = tmp_path / "settings.json"
    client = client_at(path)
    assert client.put("/settings/model", json=PAYLOAD, headers=HEADERS).status_code == 200
    update = {**PAYLOAD, "api_key": "", "model": "another-model"}
    assert client.put("/settings/model", json=update, headers=HEADERS).status_code == 200
    before = path.read_bytes()
    update["base_url"] = "https://another.example.com/v1"
    response = client.put("/settings/model", json=update, headers=HEADERS)
    assert response.status_code == 400
    assert path.read_bytes() == before
    assert client.get("/settings/model").json()["base_url"] == PAYLOAD["base_url"]


def test_mock_mode_is_persisted_and_can_forget_configuration(tmp_path):
    path = tmp_path / "settings.json"
    client = client_at(path)
    client.put("/settings/model", json=PAYLOAD, headers=HEADERS)
    response = client.post("/settings/model/mock", headers=HEADERS)
    assert response.status_code == 200
    assert client_at(path).get("/settings/model").json()["mode"] == "mock"
    assert response.json()["has_api_key"] is True
    assert client.delete("/settings/model", headers=HEADERS).status_code == 200
    status = client_at(path).get("/settings/model").json()
    assert status["has_api_key"] is False
    assert status["mode"] == "mock"
    assert PAYLOAD["api_key"] not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-SafeDesk-Settings": "1", "Origin": "https://evil.example"},
        {"X-SafeDesk-Settings": "1", "Origin": "null"},
    ],
)
def test_settings_reject_missing_header_and_cross_origin(tmp_path, headers):
    path = tmp_path / "settings.json"
    response = client_at(path).put("/settings/model", json=PAYLOAD, headers=headers)
    assert response.status_code == 403
    assert not path.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"protocol": "invalid"},
        {"model": ""},
        {"api_key": {"secret": "must-not-be-echoed"}},
        {"base_url": "file:///D:/tasks"},
        {"base_url": "https://username:password@example.com/v1"},
        {"base_url": "https://example.com/v1?api_key=secret"},
        {"base_url": "https://example.com/v1/chat/completions"},
    ],
)
def test_invalid_config_never_echoes_submitted_body(tmp_path, overrides):
    response = client_at(tmp_path / "settings.json").put(
        "/settings/model", json={**PAYLOAD, **overrides}, headers=HEADERS
    )
    assert response.status_code in (400, 422)
    assert PAYLOAD["api_key"] not in response.text
    assert "must-not-be-echoed" not in response.text


def test_test_connection_is_explicit_nonpersistent_and_uses_no_project_context(tmp_path):
    observed = []

    class Probe:
        def check_connection(self):
            observed.append("probe")

    path = tmp_path / "settings.json"
    client = client_at(path, factory=lambda config: Probe())
    response = client.post("/settings/model/test", json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert observed == ["probe"]
    assert not path.exists()
    assert client.get("/settings/model").json()["mode"] == "mock"


def test_probe_failure_does_not_leak_key_or_change_configuration(tmp_path):
    class Probe:
        def check_connection(self):
            raise RuntimeError(PAYLOAD["api_key"])

    path = tmp_path / "settings.json"
    client = client_at(path, factory=lambda config: Probe())
    response = client.post("/settings/model/test", json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 502
    assert PAYLOAD["api_key"] not in response.text
    assert not path.exists()


def test_corrupt_file_does_not_prevent_startup_or_get_overwritten(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"encrypted_api_key":"broken"}', encoding="utf-8")
    response = client_at(path).get("/settings/model")
    assert response.status_code == 200
    assert response.json()["load_error"] is True
    assert path.read_text(encoding="utf-8") == '{"encrypted_api_key":"broken"}'


def test_replacing_or_forgetting_planner_closes_previous_client(tmp_path):
    from types import SimpleNamespace

    from agent.settings import ModelConfiguration, ModelSettings

    closed = []
    service = AgentService()
    settings = ModelSettings(
        service, tmp_path / "settings.json",
        factory=lambda config: SimpleNamespace(
            client=SimpleNamespace(close=lambda: closed.append(config.model))
        ),
    )
    settings.save(ModelConfiguration(**PAYLOAD))
    settings.save(ModelConfiguration(**{**PAYLOAD, "model": "replacement"}))
    assert closed == [PAYLOAD["model"]]
    settings.use_mock(forget=True)
    assert closed == [PAYLOAD["model"], "replacement"]


def test_failed_write_keeps_saved_file_and_planner_and_closes_candidate(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from agent.settings import ModelConfiguration, ModelSettings

    closed = []
    service = AgentService()
    path = tmp_path / "settings.json"
    settings = ModelSettings(
        service, path,
        factory=lambda config: SimpleNamespace(
            client=SimpleNamespace(close=lambda: closed.append(config.model))
        ),
    )
    settings.save(ModelConfiguration(**PAYLOAD))
    original_file, original_planner = path.read_bytes(), service.planner

    def fail_write(stored):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(settings, "_write", fail_write)
    with pytest.raises(OSError):
        settings.save(ModelConfiguration(**{**PAYLOAD, "model": "replacement"}))
    assert path.read_bytes() == original_file
    assert service.planner is original_planner
    assert settings.status()["model"] == PAYLOAD["model"]
    assert closed == ["replacement"]


def test_reload_decrypts_original_key_for_planner(tmp_path):
    from agent.settings import ModelConfiguration, ModelSettings
    from agent.planner import MockPlanner

    path = tmp_path / "settings.json"
    observed = []

    def factory(config):
        observed.append(config.api_key.get_secret_value())
        return MockPlanner()

    settings = ModelSettings(AgentService(), path, factory=factory)
    settings.save(ModelConfiguration(**PAYLOAD))
    ModelSettings(AgentService(), path, factory=factory)
    assert observed == [PAYLOAD["api_key"], PAYLOAD["api_key"]]


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
@pytest.mark.parametrize("budget_exhausted", [False, True])
def test_sdk_probe_request_and_response_shape(protocol, budget_exhausted):
    import httpx
    from openai import OpenAI

    from agent.planner import OpenAIResponsesPlanner

    requests = []

    def handle(request):
        requests.append(request)
        if protocol == "chat_completions":
            body = {
                "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
                "model": PAYLOAD["model"],
                "choices": [{"index": 0, "finish_reason": "stop", "logprobs": None,
                             "message": {"role": "assistant", "content": "OK"}}],
            }
        else:
            body = {
                "id": "resp_test", "object": "response", "created_at": 0,
                "status": "completed", "model": PAYLOAD["model"],
                "output": [{
                    "id": "msg_test", "type": "message", "role": "assistant",
                    "status": "completed", "content": [
                        {"type": "output_text", "text": "OK", "annotations": []}
                    ],
                }],
            }
        if budget_exhausted and protocol == "chat_completions":
            body["choices"][0]["finish_reason"] = "length"
            body["choices"][0]["message"]["content"] = None
        elif budget_exhausted:
            body.update(
                status="incomplete", output=[],
                incomplete_details={"reason": "max_output_tokens"},
            )
        return httpx.Response(200, json=body)

    with OpenAI(
        api_key=PAYLOAD["api_key"], base_url=PAYLOAD["base_url"], max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        OpenAIResponsesPlanner(
            client, PAYLOAD["model"], protocol=protocol, api_key=PAYLOAD["api_key"]
        ).check_connection()
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["model"] == PAYLOAD["model"]
    if protocol == "chat_completions":
        assert requests[0].url.path == "/v1/chat/completions"
        assert body["messages"] == [{"role": "user", "content": "Reply with OK only."}]
        assert body["max_completion_tokens"] == 64
        assert "max_tokens" not in body
    else:
        assert requests[0].url.path == "/v1/responses"
        assert body["input"] == "Reply with OK only."
        assert body["max_output_tokens"] == 64


def test_sdk_error_cannot_expose_key_in_task_or_history(tmp_path):
    from types import SimpleNamespace

    from agent.planner import OpenAIResponsesPlanner

    def fail(**kwargs):
        raise RuntimeError("provider echoed " + PAYLOAD["api_key"])

    def factory(config):
        return OpenAIResponsesPlanner(
            SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail))),
            config.model, protocol=config.protocol,
            api_key=config.api_key.get_secret_value(),
        )

    client = client_at(tmp_path / "settings.json", factory=factory)
    assert client.put("/settings/model", json=PAYLOAD, headers=HEADERS).status_code == 200
    response = client.post("/tasks", json={"task": "读取 example.txt"})
    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert PAYLOAD["api_key"] not in response.text
    assert PAYLOAD["api_key"] not in client.get("/tasks").text
