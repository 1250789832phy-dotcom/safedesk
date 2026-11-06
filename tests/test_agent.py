from fastapi.testclient import TestClient

from agent.models import Plan, ToolCall
from agent.planner import OpenAIResponsesPlanner
from agent.service import AgentService
from agent.tools import ToolRegistry, ToolSpec
from apps.api.main import create_app


def make_service(planner, tools=None, max_steps=5):
    return AgentService(
        planner=planner,
        tools=tools or ToolRegistry(),
        max_steps=max_steps,
    )


class ReadFilePlanner:
    def plan(self, request):
        return Plan(
            summary="Read a workspace file",
            acceptance_criteria=["Return the file content"],
            tool_calls=[
                ToolCall(name="read_file", arguments={"path": "example.txt"})
            ],
        )

    def finalize(self, request, plan, observations):
        return observations[-1].message


class EmptyPlanner:
    def plan(self, request):
        raise AssertionError("empty requests must be clarified before planning")

    def finalize(self, request, plan, observations):
        raise AssertionError("clarification must not run tools")


def test_health_route_remains_compatible():
    client = TestClient(create_app(make_service(ReadFilePlanner())))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_workbench_page_is_served():
    client = TestClient(create_app(make_service(ReadFilePlanner())))

    response = client.get("/")

    assert response.status_code == 200
    assert "SafeDesk" in response.text
    assert "SpeechRecognition" in response.text


def test_read_only_task_completes_with_mock_planner():
    client = TestClient(create_app(make_service(ReadFilePlanner())))

    response = client.post("/tasks", json={"task": "Read example.txt"})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["report"] == "这是练习文件\n"
    assert [item["tool_name"] for item in body["trace"]] == ["read_file"]


def test_empty_task_returns_clarification_without_tool_execution():
    service = make_service(EmptyPlanner())

    record = service.submit("   ")

    assert record.status == "clarifying"
    assert record.clarification_question
    assert record.trace == []


def test_task_output_preference_is_validated_and_returned():
    client = TestClient(create_app(make_service(ReadFilePlanner())))

    response = client.post(
        "/tasks",
        json={"task": "Read example.txt", "output_preference": "voice"},
    )

    assert response.status_code == 201
    assert response.json()["output_preference"] == "voice"

    invalid = client.post(
        "/tasks",
        json={"task": "Read example.txt", "output_preference": "telepathy"},
    )
    assert invalid.status_code == 422


def test_all_output_preferences_keep_the_report_in_api_result():
    client = TestClient(create_app(make_service(ReadFilePlanner())))

    for preference in ("text", "voice", "both"):
        response = client.post(
            "/tasks",
            json={"task": "Read example.txt", "output_preference": preference},
        )
        assert response.status_code == 201
        assert response.json()["output_preference"] == preference
        assert response.json()["report"]


def test_unknown_tool_is_rejected_and_task_fails():
    class UnknownToolPlanner(ReadFilePlanner):
        def plan(self, request):
            return Plan(
                summary="Try an unregistered tool",
                acceptance_criteria=["Do not execute unknown tools"],
                tool_calls=[ToolCall(name="delete_file", arguments={})],
            )

    record = make_service(UnknownToolPlanner()).submit("Try unknown tool")

    assert record.status == "failed"
    assert record.trace[0].error_category == "unknown_tool"


def test_invalid_tool_arguments_are_rejected():
    class InvalidArgumentsPlanner(ReadFilePlanner):
        def plan(self, request):
            return Plan(
                summary="Read a file",
                acceptance_criteria=["Validate tool arguments"],
                tool_calls=[ToolCall(name="read_file", arguments={"path": 12})],
            )

    record = make_service(InvalidArgumentsPlanner()).submit("Read a file")

    assert record.status == "failed"
    assert record.trace[0].error_category == "invalid_arguments"


def test_tool_exception_is_reported_as_failure():
    class RaisingTool:
        def run(self, arguments):
            raise OSError("disk unavailable")

    registry = ToolRegistry(
        specs=[
            ToolSpec(
                name="read_file",
                description="test tool",
                argument_model=dict,
                implementation=RaisingTool(),
            )
        ]
    )

    record = make_service(ReadFilePlanner(), registry).submit("Read a file")

    assert record.status == "failed"
    assert record.trace[0].error_category == "tool_error"
    assert "disk unavailable" in record.report


def test_maximum_step_limit_blocks_execution():
    class MultiCallPlanner(ReadFilePlanner):
        def plan(self, request):
            return Plan(
                summary="Run more tools than allowed",
                acceptance_criteria=["Stop at the configured limit"],
                tool_calls=[
                    ToolCall(name="read_file", arguments={"path": "example.txt"}),
                    ToolCall(name="read_file", arguments={"path": "example.txt"}),
                ],
            )

    record = make_service(MultiCallPlanner(), max_steps=1).submit("Read twice")

    assert record.status == "blocked"
    assert len(record.trace) == 1
    assert "step limit" in record.report.lower()


def test_plan_without_tools_or_clarification_is_blocked():
    class NoToolPlanner(ReadFilePlanner):
        def plan(self, request):
            return Plan(
                summary="Do the task",
                acceptance_criteria=["Complete the task"],
                tool_calls=[],
            )

    record = make_service(NoToolPlanner()).submit("Do something")

    assert record.status == "blocked"
    assert "no executable tool" in record.report.lower()


def test_total_time_budget_blocks_after_slow_tool():
    class SlowClock:
        def __init__(self):
            self.values = iter([0.0, 0.0, 0.0, 0.0, 2.0])

        def __call__(self):
            return next(self.values)

    record = AgentService(
        planner=ReadFilePlanner(),
        max_steps=5,
        max_duration_seconds=1,
        clock=SlowClock(),
    ).submit("Read example.txt")

    assert record.status == "blocked"
    assert len(record.trace) == 1
    assert "time budget" in record.report.lower()


def test_idempotency_key_returns_existing_task_without_reexecution():
    service = make_service(ReadFilePlanner())

    first = service.submit("Read example.txt", idempotency_key="request-1")
    second = service.submit("Read example.txt", idempotency_key="request-1")

    assert second.task_id == first.task_id
    assert len(second.trace) == 1


def test_openai_adapter_parses_sdk_output_text_without_network():
    class FakeResponses:
        def create(self, **kwargs):
            return type(
                "Response",
                (),
                {
                    "output_text": (
                        '{"summary":"Read one file","acceptance_criteria":["Report it"],'
                        '"tool_calls":[{"name":"read_file","arguments":{"path":"example.txt"}}]}'
                    )
                },
            )()

    client = type("Client", (), {"responses": FakeResponses()})()
    planner = OpenAIResponsesPlanner(client=client, model="test-model")

    plan = planner.plan("Read example.txt")

    assert plan.tool_calls[0].name == "read_file"


def test_missing_openai_key_does_not_prevent_api_startup(monkeypatch):
    monkeypatch.setenv("SAFE_DESK_MODEL", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(AgentService.from_environment()))

    assert client.get("/health").status_code == 200
    response = client.post("/tasks", json={"task": "Read example.txt"})

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert "OPENAI_API_KEY" in response.json()["report"]


def test_fixed_test_tool_cannot_accept_a_caller_command():
    result = ToolRegistry().execute(
        "run_tests", {"command": "python -c \"print('unsafe')\""}
    )

    assert result.ok is False
    assert result.error_category == "invalid_arguments"


def test_nonzero_fixed_test_exit_is_not_reported_as_success(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "agent.tools.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="1 failed",
            stderr="assertion error",
        ),
    )

    result = ToolRegistry().execute("run_tests", {})

    assert result.ok is False
    assert result.error_category == "tests_failed"
    assert "1 failed" in result.message


def test_fixed_test_timeout_is_bounded_by_remaining_agent_budget(monkeypatch):
    from types import SimpleNamespace

    observed = {}

    def fake_run(*args, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent.tools.subprocess.run", fake_run)

    result = ToolRegistry().execute("run_tests", {}, timeout_seconds=0.4)

    assert result.ok is True
    assert observed["timeout"] == 0.4
