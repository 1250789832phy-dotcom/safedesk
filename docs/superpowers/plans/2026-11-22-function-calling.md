# Responses API Function Calling 实现计划

> **给执行者：** 按任务逐项执行。每一步都先写测试、确认测试按预期失败，再写最小实现。步骤使用复选框跟踪。

**目标：** 在不破坏现有 mock/JSON planner 的前提下，为 Responses API 增加真实的工具调用、工具结果回传和最终回复循环。

**架构：** `ToolRegistry` 负责导出受控工具 schema 和执行工具；`OpenAIResponsesPlanner` 负责 Responses API 请求、`function_call` 解析和 `function_call_output` 输入；`AgentService` 负责预算、轨迹和多轮循环。现有 `plan()`/`finalize()` 路径继续作为非原生 planner 的后备路径。

**技术栈：** Python 3.11+、Pydantic、OpenAI Python SDK、pytest、现有 SafeDesk 工具注册表与任务状态机。

## 全局约束

- 只实现 Responses API 原生 Function Calling；本轮不实现 Chat Completions Function Calling。
- 工具必须继续经过 `ToolRegistry.execute()`，不能绕过参数校验、未知工具拒绝、超时、重试和预算。
- 现有 mock、JSON planner、评测和模型设置行为保持兼容。
- 不新增运行时依赖，不读取或写入 API key 到报告、日志或测试 fixture。
- 真实网络不参与测试；所有新增测试使用 fake Responses client。
- 不修改现有工具名称、参数模型、公开函数签名和任务状态枚举。

---

### 任务 1：工具 schema 导出

**文件：**

- 修改：`agent/tools.py`
- 测试：`tests/test_function_calling.py`

**接口：**

- 新增 `ToolRegistry.function_schemas() -> list[dict[str, object]]`。
- 每项格式为 Responses API 的扁平 function tool：
  `type`、`name`、`description`、`parameters`。
- `parameters` 来自参数模型的 `model_json_schema()`；`dict` 参数模型使用空对象 schema。
- 测试文件在顶部定义 `FakeResponse`（保存 `output`、`output_text`）和
  `FakeResponsesClient`（按顺序返回响应并把每次请求参数追加到 `calls`）。

- [x] **步骤 1：写失败测试**

```python
def test_tool_registry_exports_responses_function_schemas():
    schemas = ToolRegistry().function_schemas()

    read_file = next(item for item in schemas if item["name"] == "read_file")
    assert read_file["type"] == "function"
    assert read_file["description"]
    assert read_file["parameters"]["properties"]["path"]["type"] == "string"
    assert "implementation" not in read_file
```

- [x] **步骤 2：运行测试确认失败**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_function_calling.py::test_tool_registry_exports_responses_function_schemas -q
```

预期：失败，原因是 `ToolRegistry.function_schemas` 尚不存在。

- [x] **步骤 3：实现最小 schema 导出**

在 `ToolRegistry` 中遍历 `self.specs.values()`，对 Pydantic 参数模型调用
`model_json_schema()`，对 `dict` 使用
`{"type": "object", "additionalProperties": True}`，只返回公开 schema 字段。

- [x] **步骤 4：运行测试确认通过**

运行同一个 pytest 命令，预期 `1 passed`。

- [x] **步骤 5：提交**

```powershell
git add agent/tools.py tests/test_function_calling.py
git commit -m "feat: export safe responses tool schemas"
```

---

### 任务 2：Responses 原生工具调用解析

**文件：**

- 修改：`agent/planner.py`
- 测试：`tests/test_function_calling.py`

**接口：**

- `OpenAIResponsesPlanner.supports_function_calling = True`。
- 新增 `respond_with_tools(request, tools, input_items=None) -> dict`。
- 返回字典至少包含：
  - `output_items: list[dict]`
  - `function_calls: list[dict]`，每项含 `call_id`、`name`、`arguments`
  - `text: str`
- 每次调用 `client.responses.create` 增加一次 `model_calls`。
- `FakeResponse`、`FakeResponsesClient` 沿用本任务 1 在测试文件顶部定义的
  fixture；fake client 的 `create(**kwargs)` 保存 `kwargs` 后按顺序返回响应。

- [x] **步骤 1：写失败测试**

```python
def test_responses_planner_parses_function_call_and_counts_request():
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
```

- [x] **步骤 2：运行测试确认失败**

预期：失败，原因是 `respond_with_tools` 尚不存在。

- [x] **步骤 3：实现最小 Responses 解析**

增加统一的 response item 读取辅助方法，兼容字典、Pydantic 对象和
`SimpleNamespace`。调用 `client.responses.create(model=self.model,
input=..., tools=...)`，递增 `model_calls`，解析 `function_call` 的
`arguments` JSON，并使用 `response.output_text` 作为最终文字。

- [x] **步骤 4：增加工具结果续轮测试**

```python
def test_responses_planner_sends_function_call_output_on_next_turn():
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
```

- [x] **步骤 5：运行定向测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_function_calling.py -q
```

预期：schema 和 planner 解析测试全部通过。

- [x] **步骤 6：提交**

```powershell
git add agent/planner.py tests/test_function_calling.py
git commit -m "feat: parse responses function calls"
```

---

### 任务 3：AgentService 多轮工具调用

**文件：**

- 修改：`agent/service.py`
- 测试：`tests/test_function_calling.py`

**接口：**

- 当 planner 的 `supports_function_calling` 为真时，`submit()` 进入原生循环。
- 原生循环调用 `tools.function_schemas()`，再调用
  `planner.respond_with_tools(...)`。
- 每次模型调用返回的工具结果都写入 `record.trace`。
- 最终文字写入 `record.report`，任务状态为 `succeeded`。
- 工具失败沿用现有 `_fail()`，超出预算沿用 `_block()`。
- 测试中的 `NativeFakePlanner` 设置
  `supports_function_calling = True`，按顺序返回预设 turn，并把传入的
  `input_items` 保存到 `inputs`。

- [x] **步骤 1：写失败集成测试**

```python
def test_service_executes_native_function_call_and_returns_final_text():
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
```

- [x] **步骤 2：运行测试确认失败**

预期：失败，原因是 `AgentService` 仍然只识别 JSON `Plan`。

- [x] **步骤 3：实现原生循环**

在上下文检索完成、JSON `Plan` 校验之前检测
`getattr(self.planner, "supports_function_calling", False)`，调用独立的
`_submit_native(...)` 辅助方法。维护 `history`、`steps_used` 和每轮的工具
输出；每个工具调用都检查总时长、`max_steps`，并继续调用
`ToolRegistry.execute()`。

- [x] **步骤 4：增加失败和预算测试**

测试未知工具会得到 `unknown_tool`，测试 `max_steps=1` 时第二个函数调用
不会执行并返回 `blocked`。

- [x] **步骤 5：运行定向测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_function_calling.py -q
```

预期：所有 Function Calling 测试通过。

- [x] **步骤 6：提交**

```powershell
git add agent/service.py tests/test_function_calling.py
git commit -m "feat: execute responses function calls in service"
```

---

### 任务 4：文档与完整验证

**文件：**

- 修改：`README.md`
- 修改：`docs/superpowers/specs/2026-11-22-function-calling-design.md`
- 修改：`docs/superpowers/plans/2026-11-22-function-calling.md`
- 测试：全部现有测试与 `tests/test_function_calling.py`

- [x] **步骤 1：同步 README**

把“Function Calling 仍在未来计划中”改为：

```text
Responses API 原生 Function Calling 已实现：模型可以请求已注册工具，
SafeDesk 会校验参数、执行工具、回传 function_call_output，并继续生成最终回复。
本轮未实现 Chat Completions 的原生 Function Calling；CI 和默认离线评测仍使用 mock/JSON 路径。
```

- [x] **步骤 2：运行完整测试**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

预期：全部测试通过，且不访问真实 API。

- [x] **步骤 3：检查安全边界**

```powershell
git diff --check
rg -n "OPENAI_API_KEY|Authorization|Function Calling" README.md agent tests docs/superpowers
```

确认没有测试 fixture、报告或文档硬编码真实密钥。

- [ ] **步骤 4：提交并推送**

```powershell
git add README.md agent tests docs/superpowers
git commit -m "feat: add responses api function calling"
git push origin main
```

然后查询 GitHub Actions，确认 Ubuntu/Windows × Python 3.11/3.12/3.13
六组全部成功。

