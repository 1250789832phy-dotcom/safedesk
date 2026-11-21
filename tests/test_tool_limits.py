import time

import pytest

from agent.models import Plan, ToolCall
from agent.service import AgentService
from agent.tools import NoArguments, ToolRegistry, ToolSpec


def registry_for(implementation):
    return ToolRegistry(
        [ToolSpec("test_tool", "Test execution limits", NoArguments, implementation)]
    )


def test_tool_timeout():
    from agent.tools import ToolError

    calls = []

    def slow_tool(arguments):
        calls.append(1)
        time.sleep(2)
        return "finished"

    registry = registry_for(slow_tool)
    registry.timeout_seconds = 0.1
    started = time.monotonic()
    with pytest.raises(ToolError, match="已重试 2 次"):
        registry.execute("test_tool", {})
    elapsed = time.monotonic() - started

    assert elapsed < 2
    assert len(calls) == 1  # A timed-out worker is still running.


def test_tool_retry_then_success(monkeypatch):
    calls = []
    delays = []

    def transient_tool(arguments):
        calls.append(1)
        if len(calls) < 3:
            raise OSError("file is busy")
        return "success"

    monkeypatch.setattr("agent.tools.time.sleep", delays.append)
    registry = registry_for(transient_tool)

    result = registry.execute("test_tool", {})

    assert result.ok is True
    assert result.data == "success"
    assert len(calls) == 3
    assert delays == [0.5, 1.0]
    assert registry._calls == 1


def test_tool_retry_exhaustion_preserves_cause(monkeypatch):
    from agent.tools import ToolError

    calls = []

    def failing_tool(arguments):
        calls.append(1)
        raise OSError("disk unavailable")

    monkeypatch.setattr("agent.tools.time.sleep", lambda delay: None)
    with pytest.raises(ToolError, match="disk unavailable"):
        registry_for(failing_tool).execute("test_tool", {})
    assert len(calls) == 3


def test_tool_validation_and_unknown_tool_do_not_retry():
    calls = []
    registry = registry_for(lambda arguments: calls.append(1))
    unknown = registry.execute("missing", {})
    invalid = registry.execute("test_tool", {"unexpected": True})

    assert unknown.error_category == "unknown_tool"
    assert invalid.error_category == "invalid_arguments"
    assert calls == []


@pytest.mark.parametrize("error", [PermissionError("denied"), FileNotFoundError("missing")])
def test_permanent_file_errors_do_not_retry(error):
    calls = []

    def failing_tool(arguments):
        calls.append(1)
        raise error

    result = registry_for(failing_tool).execute("test_tool", {})

    assert result.error_category == "tool_error"
    assert len(calls) == 1


def test_tool_call_budget_and_reset():
    from agent.tools import ToolError

    calls = []
    registry = registry_for(lambda arguments: calls.append(1))
    assert registry.max_calls == 25
    for _ in range(25):
        assert registry.execute("test_tool", {}).ok
    with pytest.raises(ToolError, match="最大工具调用步数"):
        registry.execute("test_tool", {})
    assert len(calls) == 25
    registry.reset_calls()
    assert registry._calls == 0
    assert registry.execute("test_tool", {}).ok


class ToolPlanner:
    def plan(self, request):
        return Plan(
            summary="Run a test tool",
            acceptance_criteria=["Report the result"],
            tool_calls=[ToolCall(name="test_tool", arguments={})],
        )

    def finalize(self, request, plan, observations):
        return observations[-1].message


def test_service_resets_budget_only_for_new_tasks():
    registry = registry_for(lambda arguments: "ok")
    registry.max_calls = 1
    service = AgentService(planner=ToolPlanner(), tools=registry)

    first = service.submit("first", idempotency_key="first")
    cached = service.submit("first", idempotency_key="first")
    assert cached.task_id == first.task_id
    assert registry._calls == 1
    second = service.submit("second")
    assert first.status == second.status == "succeeded"
    assert registry._calls == 1


def test_service_reports_registry_budget_failure():
    registry = registry_for(lambda arguments: "ok")
    registry.max_calls = 0
    record = AgentService(planner=ToolPlanner(), tools=registry).submit("run tool")

    assert record.status == "failed"
    assert record.trace[-1].error_category == "tool_error"
    assert "最大工具调用步数" in record.report


def test_tool_retries_respect_total_remaining_budget():
    from agent.tools import ToolError

    calls = []

    def busy_tool(arguments):
        calls.append(1)
        raise OSError("busy")

    started = time.monotonic()
    with pytest.raises(ToolError):
        registry_for(busy_tool).execute("test_tool", {}, timeout_seconds=0.05)
    assert time.monotonic() - started < 0.4
    assert len(calls) == 1


def test_no_retry_when_budget_cannot_cover_backoff(monkeypatch):
    from agent.tools import ToolError

    calls = []
    delays = []

    def busy_tool(arguments):
        calls.append(1)
        raise OSError("busy")

    monkeypatch.setattr("agent.tools.time.monotonic", lambda: 100.0)
    monkeypatch.setattr("agent.tools.time.sleep", delays.append)
    with pytest.raises(ToolError):
        registry_for(busy_tool).execute("test_tool", {}, timeout_seconds=0.05)

    assert len(calls) == 1
    assert delays == []


def test_timeout_wait_can_recover_without_replaying_tool(monkeypatch):
    from threading import Event

    started = Event()
    finish = Event()
    calls = []

    def tool(arguments):
        calls.append(1)
        started.set()
        assert finish.wait(1)
        return "completed"

    def backoff(delay):
        assert started.is_set()
        finish.set()

    monkeypatch.setattr("agent.tools.time.sleep", backoff)
    registry = registry_for(tool)
    registry.timeout_seconds = 0.02
    result = registry.execute("test_tool", {})

    assert result.ok
    assert result.data == "completed"
    assert len(calls) == 1


def test_repair_api_reports_tool_error_without_server_error(monkeypatch):
    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    registry = ToolRegistry()
    calls = []

    def failing_tests(arguments, timeout_seconds=30):
        calls.append(1)
        raise OSError("test execution budget exceeded")

    monkeypatch.setattr(registry, "_run_tests", failing_tests)
    monkeypatch.setattr("agent.tools.time.sleep", lambda delay: None)
    response = TestClient(
        create_app(AgentService(planner=ToolPlanner(), tools=registry))
    ).post("/repairs")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "blocked"
    assert "test execution budget exceeded" in body["failure_summary"]
    assert len(calls) == 3


def test_concurrent_repair_cannot_reset_running_task_budget(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    paused = Event()
    release = Event()
    repair_sent = Event()
    reset_during_task = Event()
    calls = []

    def tool(arguments):
        calls.append(1)
        if len(calls) == 13:
            paused.set()
            assert release.wait(5)
        return "ok"

    class ManyCallsPlanner(ToolPlanner):
        def plan(self, request):
            plan = super().plan(request)
            return plan.model_copy(update={"tool_calls": plan.tool_calls * 26})

    registry = ToolRegistry()
    registry.specs["test_tool"] = ToolSpec("test_tool", "", NoArguments, tool)
    original_reset = registry.reset_calls

    def tracked_reset():
        if paused.is_set() and not release.is_set():
            reset_during_task.set()
        original_reset()

    monkeypatch.setattr(registry, "reset_calls", tracked_reset)
    monkeypatch.setattr(
        registry, "_run_tests",
        lambda *args: {"returncode": 0, "stdout": "ok", "stderr": ""},
    )
    service = AgentService(planner=ManyCallsPlanner(), tools=registry, max_steps=30)
    client = TestClient(create_app(service))

    def repair():
        repair_sent.set()
        return client.post("/repairs")

    with ThreadPoolExecutor(max_workers=2) as executor:
        task_future = executor.submit(client.post, "/tasks", json={"task": "many"})
        try:
            assert paused.wait(5)
            repair_future = executor.submit(repair)
            assert repair_sent.wait(5)
            reset_observed = reset_during_task.wait(0.5)
        finally:
            release.set()
        task = task_future.result(timeout=5).json()
        repaired = repair_future.result(timeout=5)

    assert reset_observed is False
    assert task["status"] == "failed"
    assert task["trace"][-1]["error_category"] == "tool_error"
    assert len(calls) == 25
    assert repaired.status_code == 201
