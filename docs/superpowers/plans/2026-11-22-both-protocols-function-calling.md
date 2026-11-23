# 双协议原生 Function Calling 实现计划

> 执行方式：在当前会话逐项执行，先写失败测试，再写实现。

**目标：** 为 Responses 与 Chat Completions 均提供原生函数工具调用。

**架构：** Planner 适配协议，Service 累计历史并执行已有受控工具。
模型请求的 HTTP 层用真实 SDK 和本地 MockTransport 验证。

**技术栈：** Python 3.11+、现有 OpenAI SDK、httpx、pytest。

## 约束

- 无真实 API key、外部模型请求或新增依赖。
- 不更名公开类，不删除 JSON 规划接口，不修改工具权限或参数模型。
- 不把双协议支持宣传成支持所有模型、流式调用或所有厂商。
- 原有测试预期只有在能力确实变化时替换成更强的行为测试。

## 任务 1：协议测试

文件：`tests/test_chat_function_calling.py`、
`tests/test_function_calling.py`。

- [x] 用 `OpenAI(http_client=httpx.Client(transport=httpx.MockTransport(...)))`
  捕获 `/v1/chat/completions` 和 `/v1/responses` 请求体。
- [x] 断言 Chat 请求含嵌套 function schema、assistant.tool_calls，
  下一轮含匹配的 tool_call_id 和序列化 ToolResult。
- [x] 三轮调用断言首轮用户消息、两轮工具调用与结果均完整保留。
- [x] 参数化测试非法结构、空响应、失败脱敏、计数和安全预算。
- [x] 运行新增测试，确认失败源于缺少功能而不是测试环境。

命令：`.\.venv\Scripts\python.exe -m pytest tests/test_chat_function_calling.py -q`

## 任务 2：实现

文件：`agent/planner.py`、`agent/service.py`。

- [x] `supports_function_calling` 对两个已支持协议均为真。
- [x] 新增 Chat Completions adapter，将内部工具输出转换为 tool 消息。
- [x] 校验整批调用后再交由 service 执行；保留 assistant 原始参数字符串。
- [x] 继续使用 Responses 现有适配器，不改变其报文格式。
- [x] Service 的下一轮历史为原历史（初次为 user）加本轮输出和结果。
- [x] 显式 JSON plan 调用保留可用，用行为测试替换旧的能力禁用断言。

命令：`.\.venv\Scripts\python.exe -m pytest tests/test_function_calling.py tests/test_chat_function_calling.py tests/test_model_settings.py -q`

## 任务 3：验证和文档

文件：`README.md`、本计划。

- [x] 全量 `.\.venv\Scripts\python.exe -m pytest -q`。
- [x] `git diff --check`，检查仅修改协议实现、测试和相关文档。
- [x] README 明确两个协议、各自回传格式、离线验证边界及简历措辞。
- [x] 记录最终测试数量；未经用户要求不推送，不声称远端 CI 已验证。

## 验证记录

- 干净基线：`156 passed in 16.93s`。
- 初始协议测试：`20 failed, 11 passed`；实现后 `31 passed`。
- 增补显式 JSON 接口验证，全量 `187 passed in 17.09s`。
- 审查发现原生成功回复绕过旧路径的密钥脱敏，以及模型请求结束后缺少
  时间检查。补充回归先观察到 `6 failed, 34 passed`；脱敏后 ID 碰撞测试
  先观察到 `2 failed`。实现共享原生输出脱敏和响应后预算检查。
- 最终协议定向测试：`42 passed in 1.09s`。
- 最终全量测试：`197 passed in 17.31s`；即基线 156 项，用更强的
  JSON 接口行为测试替换旧的单个能力禁用断言，再增加协议与安全覆盖。
- 只读复审未发现剩余阻塞问题，`git diff --check` 通过。
- 未读取真实 API key、未请求中转站、未改写既有真实评测 JSON，
  未触发本轮远程 CI。没有找到需要同步的仓库内简历文件。
