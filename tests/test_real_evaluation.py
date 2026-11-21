import pytest

from agent.planner import MockPlanner, OpenAIResponsesPlanner
from evaluation.run import run_evaluation


class FakeResponses:
    def __init__(self, text=None, error=None):
        self.text = text or (
            '{"summary":"ok","acceptance_criteria":["report"],'
            '"tool_calls":[]}'
        )
        self.error = error

    def create(self, **kwargs):
        if self.error:
            raise self.error
        return type("Response", (), {"output_text": self.text})()


class FakeClient:
    def __init__(self, responses):
        self.responses = responses


def test_responses_planner_counts_successful_generate_requests():
    planner = OpenAIResponsesPlanner(
        FakeClient(FakeResponses()), "test-model", api_key="secret"
    )

    planner._generate("one")
    planner._generate("two")

    assert planner.model_calls == 2


def test_responses_planner_counts_failed_request_without_leaking_key():
    planner = OpenAIResponsesPlanner(
        FakeClient(FakeResponses(error=RuntimeError("secret"))),
        "test-model",
        api_key="secret",
    )

    with pytest.raises(RuntimeError) as error:
        planner._generate("request")

    assert planner.model_calls == 1
    assert "secret" not in str(error.value)


class CountingPlanner:
    def __init__(self):
        self.delegate = MockPlanner()
        self.model_calls = 0

    def plan(self, request):
        self.model_calls += 1
        return self.delegate.plan(request)

    def finalize(self, request, plan, observations):
        self.model_calls += 1
        return self.delegate.finalize(request, plan, observations)


def test_evaluation_records_injected_planner_call_counts():
    planner = CountingPlanner()

    report = run_evaluation(planner=planner)

    assert report["mode"] == "injected"
    assert report["model_calls"] == sum(
        case["model_calls"] for case in report["cases"]
    )
    assert report["model_calls"] > 0
    assert all(isinstance(case["model_calls"], int) for case in report["cases"])


def test_real_evaluation_requires_key(monkeypatch):
    monkeypatch.setenv("SAFE_DESK_MODEL", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        run_evaluation(use_environment=True)
