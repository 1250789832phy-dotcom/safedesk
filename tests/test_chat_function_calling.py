import json
from contextlib import contextmanager

import httpx
import pytest
from openai import OpenAI

from agent.planner import OpenAIResponsesPlanner
from agent.service import AgentService
from agent.tools import ToolRegistry


def function_call(call_id="call-1", name="read_file", arguments='{"path":"example.txt"}'):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def model_reply(protocol, calls=(), text=None, finish_reason=None):
    if protocol == "chat_completions":
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason or ("tool_calls" if calls else "stop"),
                    "message": {
                        "role": "assistant",
                        "content": text,
                        "tool_calls": list(calls) or None,
                    },
                }
            ],
        }
    output = [
        {
            "type": "function_call",
            "id": "fc_" + call["id"],
            "call_id": call["id"],
            "name": call["function"]["name"],
            "arguments": call["function"]["arguments"],
        }
        for call in calls
    ]
    if text is not None:
        output.append(
            {
                "id": "msg-test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        )
    return {
        "id": "resp-test",
        "object": "response",
        "created_at": 0,
        "model": "test-model",
        "status": "completed",
        "output": output,
    }


@contextmanager
def planner_at(protocol, replies):
    requests = []
    remaining = iter(replies)

    def handle(request):
        expected_path = "/v1/chat/completions" if protocol == "chat_completions" else "/v1/responses"
        assert request.url.path == expected_path
        requests.append(json.loads(request.content))
        reply = next(remaining)
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

    with OpenAI(
        api_key="unit-test-secret",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        yield OpenAIResponsesPlanner(
            client, "test-model", protocol=protocol, api_key="unit-test-secret"
        ), requests


def test_chat_planner_sends_native_schemas_and_parses_tool_calls():
    call = function_call(arguments='{ "path": "example.txt" }')
    schemas = ToolRegistry().function_schemas()
    with planner_at("chat_completions", [model_reply("chat_completions", [call])]) as (planner, requests):
        turn = planner.respond_with_tools("Read example.txt", schemas)

        assert planner.supports_function_calling is True
        assert planner.model_calls == 1
        assert requests[0]["messages"] == [{"role": "user", "content": "Read example.txt"}]
        assert requests[0]["tools"] == [
            {"type": "function", "function": {key: value for key, value in schema.items() if key != "type"}}
            for schema in schemas
        ]
        assert turn["function_calls"] == [
            {"call_id": "call-1", "name": "read_file", "arguments": {"path": "example.txt"}}
        ]
        assert turn["output_items"] == [
            {"role": "assistant", "content": None, "tool_calls": [call]}
        ]


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_native_service_preserves_history_across_three_requests(protocol):
    replies = [
        model_reply(protocol, [function_call()]),
        model_reply(protocol, [function_call("call-2", "list_tasks", "{}")]),
        model_reply(protocol, text="Completed"),
    ]
    with planner_at(protocol, replies) as (planner, requests):
        record = AgentService(planner=planner).submit("Read example.txt then list tasks")

        assert record.status == "succeeded"
        assert record.report == "Completed"
        assert [item.tool_name for item in record.trace] == ["read_file", "list_tasks"]
        assert all(item.ok for item in record.trace)
        assert planner.model_calls == len(requests) == 3
        history = requests[2]["messages" if protocol == "chat_completions" else "input"]
        assert history[0] == {"role": "user", "content": "Read example.txt then list tasks"}
        if protocol == "chat_completions":
            assert [item["role"] for item in history] == ["user", "assistant", "tool", "assistant", "tool"]
            assert history[1]["tool_calls"] == [function_call()]
            assert history[3]["tool_calls"] == [function_call("call-2", "list_tasks", "{}")]
            outputs = [item for item in history if item["role"] == "tool"]
            assert [item["tool_call_id"] for item in outputs] == ["call-1", "call-2"]
            results = [json.loads(item["content"]) for item in outputs]
        else:
            assert [item.get("type") for item in history[1:]] == [
                "function_call", "function_call_output", "function_call", "function_call_output"
            ]
            outputs = [item for item in history if item.get("type") == "function_call_output"]
            assert [item["call_id"] for item in outputs] == ["call-1", "call-2"]
            results = [json.loads(item["output"]) for item in outputs]
        assert results == [item.model_dump(mode="json") for item in record.trace]


def test_chat_service_returns_each_parallel_call_result_with_matching_id():
    calls = [
        function_call(arguments='{ "path": "example.txt" }'),
        function_call("call-2", "list_tasks", "{}"),
    ]
    replies = [
        model_reply("chat_completions", calls, text="Checking both"),
        model_reply("chat_completions", text="Done"),
    ]
    with planner_at("chat_completions", replies) as (planner, requests):
        record = AgentService(planner=planner).submit("Inspect workspace")

        assert record.status == "succeeded"
        assert len(record.trace) == 2
        assert planner.model_calls == 2
        messages = requests[1]["messages"]
        assert messages[1] == {"role": "assistant", "content": "Checking both", "tool_calls": calls}
        assert [item["tool_call_id"] for item in messages[2:]] == ["call-1", "call-2"]
        assert all(json.loads(item["content"])["ok"] for item in messages[2:])


@pytest.mark.parametrize(
    "calls",
    [
        [{"type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        [function_call(call_id="")],
        [function_call(name="")],
        [{"id": "call-1", "type": "function", "function": {"arguments": "{}"}}],
        [function_call(arguments="{")],
        [function_call(arguments="[]")],
        [function_call(arguments="null")],
        [function_call(arguments='""')],
        [{"id": "call-1", "type": "function", "function": {"name": "list_tasks"}}],
        [{"id": "call-1", "type": "custom", "custom": {"name": "other", "input": "x"}}],
        [function_call(), function_call()],
    ],
    ids=[
        "missing-id", "blank-id", "blank-name", "missing-name", "invalid-json",
        "array-arguments", "null-arguments", "string-arguments", "missing-arguments",
        "non-function-tool", "duplicate-id",
    ],
)
def test_chat_service_rejects_malformed_call_batch_before_executing(calls):
    # Include a valid call first: a malformed batch must not partially execute.
    calls = [function_call("valid-call", "list_tasks", "{}")] + calls
    with planner_at("chat_completions", [model_reply("chat_completions", calls)]) as (planner, requests):
        record = AgentService(planner=planner).submit("Inspect")

        assert record.status == "failed"
        assert record.trace == []
        assert "Planner error" in record.report
        assert planner.model_calls == len(requests) == 1
    with planner_at("chat_completions", [model_reply("chat_completions", calls)]) as (planner, _):
        with pytest.raises(ValueError):
            planner.respond_with_tools("Inspect", ToolRegistry().function_schemas())


@pytest.mark.parametrize("text", [None, "", "   "])
def test_chat_service_rejects_empty_final_reply(text):
    with planner_at("chat_completions", [model_reply("chat_completions", text=text)]) as (planner, requests):
        record = AgentService(planner=planner).submit("Inspect")

        assert record.status == "failed"
        assert not record.trace
        assert planner.model_calls == len(requests) == 1


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_chat_service_does_not_execute_incomplete_or_filtered_reply(finish_reason):
    reply = model_reply("chat_completions", [function_call()], finish_reason=finish_reason)
    with planner_at("chat_completions", [reply]) as (planner, requests):
        record = AgentService(planner=planner).submit("Inspect")

        assert record.status == "failed"
        assert not record.trace
        assert planner.model_calls == len(requests) == 1


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_native_sdk_failure_is_counted_and_does_not_expose_key(protocol):
    error = httpx.Response(
        401, json={"error": {"message": "provider echoed unit-test-secret", "type": "invalid_api_key"}}
    )
    with planner_at(protocol, [error]) as (planner, requests):
        service = AgentService(planner=planner)
        record = service.submit("Inspect")

        assert record.status == "failed"
        assert planner.model_calls == len(requests) == 1
        assert "unit-test-secret" not in record.model_dump_json()
        assert "unit-test-secret" not in service.get(record.task_id).model_dump_json()
        assert "HTTP 401" in record.report


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
@pytest.mark.parametrize(
    ("call", "category"),
    [
        (function_call(name="delete_file", arguments="{}"), "unknown_tool"),
        (function_call(arguments='{"path":123}'), "invalid_arguments"),
    ],
)
def test_native_service_preserves_tool_validation(protocol, call, category):
    with planner_at(protocol, [model_reply(protocol, [call])]) as (planner, requests):
        record = AgentService(planner=planner).submit("Inspect")

        assert record.status == "failed"
        assert record.trace[0].error_category == category
        assert planner.model_calls == len(requests) == 1


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_native_service_enforces_step_limit_across_model_rounds(protocol):
    replies = [
        model_reply(protocol, [function_call()]),
        model_reply(protocol, [function_call("call-2", "list_tasks", "{}")]),
    ]
    with planner_at(protocol, replies) as (planner, requests):
        record = AgentService(planner=planner, max_steps=1).submit("Inspect")

        assert record.status == "blocked"
        assert len(record.trace) == 1
        assert "step limit" in record.report.lower()
        assert planner.model_calls == len(requests) == 2


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_native_service_enforces_registry_call_limit(protocol):
    calls = [function_call(), function_call("call-2", "list_tasks", "{}")]
    tools = ToolRegistry()
    tools.max_calls = 1
    with planner_at(protocol, [model_reply(protocol, calls)]) as (planner, requests):
        record = AgentService(planner=planner, tools=tools).submit("Inspect")

        assert record.status == "failed"
        assert len(record.trace) == 1
        assert "达到最大工具调用步数" in record.report
        assert planner.model_calls == len(requests) == 1


def test_chat_service_accepts_plain_final_text_without_json_plan():
    with planner_at("chat_completions", [model_reply("chat_completions", text="Hello")]) as (planner, requests):
        record = AgentService(planner=planner).submit("Hello")

        assert record.status == "succeeded"
        assert record.report == "Hello"
        assert record.plan is None
        assert not record.trace
        assert planner.model_calls == len(requests) == 1


def test_chat_completions_planner_keeps_explicit_json_plan_interface():
    plan = {
        "summary": "Read one file",
        "acceptance_criteria": ["Report it"],
        "tool_calls": [{"name": "read_file", "arguments": {"path": "example.txt"}}],
    }
    reply = model_reply("chat_completions", text=json.dumps(plan))
    with planner_at("chat_completions", [reply]) as (planner, requests):
        result = planner.plan("Read example.txt")

        assert result.tool_calls[0].name == "read_file"
        assert result.tool_calls[0].arguments == {"path": "example.txt"}
        assert planner.model_calls == len(requests) == 1
        assert "tools" not in requests[0]


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_native_successful_reply_redacts_key_before_report_and_history(protocol):
    reply = model_reply(protocol, text="provider echoed unit-test-secret")
    with planner_at(protocol, [reply]) as (planner, _):
        service = AgentService(planner=planner)
        record = service.submit("Hello")

        assert record.status == "succeeded"
        assert record.report == "provider echoed [REDACTED]"
        assert "unit-test-secret" not in service.get(record.task_id).model_dump_json()
    with planner_at(protocol, [reply]) as (planner, _):
        turn = planner.respond_with_tools("Hello", [])
        assert "unit-test-secret" not in json.dumps(turn)


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_native_key_echo_in_tool_call_is_redacted_before_execution(protocol):
    reply = model_reply(
        protocol,
        [function_call(name="unit-test-secret", arguments='{"value":"unit-test-secret"}')],
    )
    with planner_at(protocol, [reply]) as (planner, _):
        service = AgentService(planner=planner)
        record = service.submit("Inspect")

        assert record.status == "failed"
        assert record.trace[0].error_category == "unknown_tool"
        assert "unit-test-secret" not in record.model_dump_json()
    with planner_at(protocol, [reply]) as (planner, _):
        turn = planner.respond_with_tools("Inspect", ToolRegistry().function_schemas())
        assert "unit-test-secret" not in json.dumps(turn)
        assert turn["function_calls"][0]["arguments"] == {"value": "[REDACTED]"}


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_native_rejects_call_ids_that_collide_after_redaction(protocol):
    calls = [
        function_call("unit-test-secret", "list_tasks", "{}"),
        function_call("[REDACTED]", "list_tasks", "{}"),
    ]
    with planner_at(protocol, [model_reply(protocol, calls)]) as (planner, _):
        record = AgentService(planner=planner).submit("Inspect")

        assert record.status == "failed"
        assert not record.trace
        assert "unit-test-secret" not in record.model_dump_json()


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
@pytest.mark.parametrize("has_call", [False, True])
def test_native_service_blocks_if_model_request_uses_up_remaining_time(protocol, has_call):
    elapsed = [0]
    reply = (
        model_reply(protocol, [function_call()])
        if has_call
        else model_reply(protocol, text="Done")
    )
    with planner_at(protocol, [reply]) as (planner, requests):
        respond = planner.respond_with_tools

        def slow_response(*args, **kwargs):
            turn = respond(*args, **kwargs)
            elapsed[0] = 2
            return turn

        planner.respond_with_tools = slow_response
        record = AgentService(
            planner=planner, max_duration_seconds=1, clock=lambda: elapsed[0]
        ).submit("Inspect")

        assert record.status == "blocked"
        assert not record.trace
        assert "time budget" in record.report
        assert planner.model_calls == len(requests) == 1
