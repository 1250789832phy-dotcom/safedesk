# Responses API Function Calling 设计

## 目标

给 SafeDesk 增加真正的 OpenAI Responses API Function Calling 链路：

1. 模型请求调用某个已注册工具；
2. SafeDesk 校验工具名和参数；
3. SafeDesk 通过现有 `ToolRegistry` 执行工具；
4. SafeDesk 把工具结果返回给模型；
5. 模型生成最终文字回复。

本轮保留现有的 `MockPlanner`、JSON `Plan` 路径和 Chat Completions
兼容能力，不改变它们的行为。

## 本轮范围

### 要实现

- 把现有 `ToolRegistry` 中的工具定义导出为 OpenAI function-tool schema。
- 在 Responses API 请求中发送这些工具定义。
- 解析 Responses API 返回的 `function_call` 项。
- 继续通过现有 `ToolRegistry` 执行工具。
- 使用对应的 `call_id` 把 `function_call_output` 返回给模型。
- 模型没有结束前可以继续多轮工具调用。
- 继续遵守现有的步骤数、总时长和工具调用预算。
- 保留 `model_calls` 统计和密钥脱敏。
- 增加不访问真实网络的 fake-client 测试。

### 不实现

- Chat Completions 的原生 Function Calling。
- 任意 shell/Python 执行工具。
- 自动注册模型临时创建的工具。
- 修改现有工具名称、参数模型、函数签名或任务状态。
- 放宽未知工具、非法参数、超时、重试和步数限制。
- 把本地字符哈希 embedding 说成真正的语义模型。

## 架构设计

### 1. 工具 schema 边界

`ToolRegistry` 仍然是唯一的工具执行入口。新增一个只读的 schema
导出方法，根据每个 `ToolSpec` 生成：

```json
{
  "type": "function",
  "name": "read_file",
  "description": "Read a text file under the example workspace",
  "parameters": {}
}
```

其中 `parameters` 来自参数模型的 Pydantic JSON Schema。

导出的 schema 不能包含：

- 工具实现对象；
- 本地文件系统路径；
- API key 或其他凭据；
- 任意调用者提供的 shell 命令。

### 2. Planner 边界

`OpenAIResponsesPlanner` 保留现有的 `plan()` 和 `finalize()`，同时增加
原生工具调用方法。该方法向 `client.responses.create` 发送：

- 用户请求；
- 已注册的 function schemas；
- 之前的 response 项；
- 上一轮工具结果。

只解析 Responses API 支持的输出项：

- `function_call`：读取工具名、JSON 参数和 `call_id`；
- `message` 或 output text：读取最终文字。

参数 JSON 无法解析、工具调用字段缺失或返回了不支持的输出类型时，
转换为受控的 planner 错误。API key 不得进入错误信息、报告或保存的
对话项。每次 Responses API 请求只增加一次 `model_calls`。

### 3. AgentService 执行循环

当 planner 声明支持原生 Responses 工具调用时，
`AgentService.submit()` 使用以下流程：

1. 构造用户请求和工具 schema；
2. 请求模型生成一轮 Responses；
3. 如果返回 `function_call`，逐个交给 `ToolRegistry.execute()`；
4. 将每个 `ToolResult` 写入现有任务轨迹 `trace`；
5. 使用匹配的 `call_id` 生成 `function_call_output`；
6. 把工具结果发送回模型；
7. 重复以上流程，直到模型返回最终文字、任务失败或预算耗尽。

第一个工具失败仍沿用当前行为：记录失败并结束任务。
如果模型既没有返回工具调用，也没有返回可用的最终文字，则产生受控的
失败或阻塞结果，不能假装任务成功。

现有 JSON `Plan` 路径继续作为后备路径，供 `MockPlanner`、注入式测试
planner 和非原生协议使用。

## 安全和预算

- 工具参数继续由现有 Pydantic 参数模型校验。
- 未知工具继续由 `ToolRegistry` 拒绝。
- 模型不能借 Function Calling 自动创建新工具。
- 现有每任务最多 25 次逻辑工具调用继续生效。
- `AgentService.max_steps` 和总时长限制继续生效。
- 工具超时、重试和退避规则不改变。
- Function Calling 不能绕过确认令牌、改动管理、固定测试命令限制。
- 工具结果会作为数据返回给模型，但仍保留在任务轨迹中，不能单独当作
  任务成功证明。

## 测试计划

使用 fake Responses client，不访问真实模型：

1. 检查每个已注册工具都能导出正确的 function schema。
2. 检查能解析 `function_call` 的工具名、`call_id` 和 JSON 参数。
3. fake 工具执行后，检查发送给模型的 `function_call_output` 内容正确，
   下一轮返回的文字成为任务报告。
4. 检查非法 JSON 参数和未知工具仍通过现有错误分类处理。
5. 检查原生循环达到步骤数或工具调用预算后会停止。
6. 检查现有 mock、JSON planner、模型设置、评测和完整测试仍然通过，
   且不依赖网络。

## README 文档同步

README 将明确说明：

- Responses API 原生 Function Calling 已实现；
- CI 和默认离线评测仍使用 mock/JSON 路径；
- 本轮不包含 Chat Completions Function Calling；
- API key 只从环境或配置读取，不进入报告和提交。

