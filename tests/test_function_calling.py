from agent.tools import ToolRegistry


class FakeResponse:
    def __init__(self, output, output_text=""):
        self.output = output
        self.output_text = output_text


class FakeResponsesClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.responses_api = self

    @property
    def responses(self):
        return self.responses_api

    @responses.setter
    def responses(self, value):
        self._responses = list(value)

    def _next_response(self):
        return self._responses.pop(0)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._next_response()


class NativeFakePlanner:
    supports_function_calling = True

    def __init__(self, turns):
        self.turns = list(turns)
        self.inputs = []

    def respond_with_tools(self, request, tools, input_items=None):
        self.inputs.append(input_items)
        return self.turns.pop(0)


def test_tool_registry_exports_responses_function_schemas():
    schemas = ToolRegistry().function_schemas()

    read_file = next(item for item in schemas if item["name"] == "read_file")
    assert read_file["type"] == "function"
    assert read_file["description"]
    assert read_file["parameters"]["properties"]["path"]["type"] == "string"
    assert "implementation" not in read_file


def test_responses_planner_parses_function_call_and_counts_request():
    from agent.planner import OpenAIResponsesPlanner

    planner = OpenAIResponsesPlanner(
        FakeResponsesClient(
            [
                FakeResponse(
                    output=[
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "read_file",
                            "arguments": '{"path":"example.txt"}',
                        }
                    ]
                )
            ]
        ),
        "test-model",
    )

    turn = planner.respond_with_tools(
        "读取 example.txt",
        [{"type": "function", "name": "read_file", "parameters": {}}],
    )

    assert turn["function_calls"] == [
        {
            "call_id": "call-1",
            "name": "read_file",
            "arguments": {"path": "example.txt"},
        }
    ]
    assert planner.model_calls == 1


def test_chat_completions_planner_keeps_json_plan_path():
    from agent.planner import OpenAIResponsesPlanner

    planner = OpenAIResponsesPlanner(
        FakeResponsesClient([]),
        "test-model",
        protocol="chat_completions",
    )

    assert planner.supports_function_calling is False


def test_responses_planner_sends_function_call_output_on_next_turn():
    from agent.planner import OpenAIResponsesPlanner

    client = FakeResponsesClient(
        [
            FakeResponse(
                output=[
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "read_file",
                        "arguments": '{"path":"example.txt"}',
                    }
                ]
            ),
            FakeResponse(output=[], output_text="读取完成"),
        ]
    )
    planner = OpenAIResponsesPlanner(client, "test-model")
    first = planner.respond_with_tools("读取", [], None)
    second = planner.respond_with_tools(
        "读取",
        [],
        first["output_items"]
        + [
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": '{"ok": true, "message": "完成"}',
            }
        ],
    )

    assert second["text"] == "读取完成"
    assert client.calls[1]["input"][-1]["type"] == "function_call_output"


def test_service_executes_native_function_call_and_returns_final_text():
    from agent.service import AgentService

    planner = NativeFakePlanner(
        [
            {
                "function_calls": [
                    {
                        "call_id": "call-1",
                        "name": "read_file",
                        "arguments": {"path": "example.txt"},
                    }
                ],
                "output_items": [
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "read_file",
                        "arguments": '{"path":"example.txt"}',
                    }
                ],
                "text": "",
            },
            {"function_calls": [], "output_items": [], "text": "读取完成"},
        ]
    )

    record = AgentService(planner=planner).submit("读取 example.txt")

    assert record.status == "succeeded"
    assert record.report == "读取完成"
    assert record.trace[0].tool_name == "read_file"
    assert planner.inputs[-1][-1]["type"] == "function_call_output"


def test_native_function_call_unknown_tool_uses_existing_error_category():
    from agent.service import AgentService

    planner = NativeFakePlanner(
        [
            {
                "function_calls": [
                    {
                        "call_id": "call-unknown",
                        "name": "delete_file",
                        "arguments": {},
                    }
                ],
                "output_items": [],
                "text": "",
            }
        ]
    )

    record = AgentService(planner=planner).submit("删除文件")

    assert record.status == "failed"
    assert record.trace[0].error_category == "unknown_tool"


def test_native_function_call_respects_service_step_limit():
    from agent.service import AgentService

    planner = NativeFakePlanner(
        [
            {
                "function_calls": [
                    {
                        "call_id": "call-1",
                        "name": "read_file",
                        "arguments": {"path": "example.txt"},
                    },
                    {
                        "call_id": "call-2",
                        "name": "read_file",
                        "arguments": {"path": "example.txt"},
                    },
                ],
                "output_items": [],
                "text": "",
            }
        ]
    )

    record = AgentService(planner=planner, max_steps=1).submit("读取两次")

    assert record.status == "blocked"
    assert len(record.trace) == 1
    assert "step limit" in record.report.lower()
