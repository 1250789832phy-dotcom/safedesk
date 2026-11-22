# 双协议原生 Function Calling 设计

用户已确认本方案；本设计扩展同日的 Responses 专项设计，不改写历史范围。

## 范围

“全协议”仅指项目配置中已有的 `responses` 和 `chat_completions`。
实现两者的非流式原生函数工具调用，不承诺任意中转站或模型均支持。
不增加网络依赖、真实密钥、任意命令工具或新的自动文件修改权限。

## 方案

保留 `OpenAIResponsesPlanner` 的名称和 `plan()` / `finalize()` 接口。
`respond_with_tools()` 根据协议分发，统一返回 `function_calls`、
`output_items` 和 `text`。不以 JSON 计划降级冒充原生工具调用。

Chat Completions 适配器把注册表 schema 包装成
`{"type": "function", "function": {...}}`，读取 `message.tool_calls`，
校验 ID、函数名、调用类型和 JSON 对象参数。保留 assistant 的内容和
原始工具调用，把 service 的规范化 `function_call_output` 转为
`role="tool"`、`tool_call_id`、`content`。

Service 继续通过 `ToolRegistry` 执行工具，并沿用未知工具、参数错误、
步数、调用数和时间预算。历史必须累计最初 user 消息、每轮模型输出和
每条工具结果，不能每轮覆盖。单轮多个工具按返回顺序执行。

非法或截断的 Chat Completions 回复作为 planner 错误，不执行工具。
每个实际 SDK 请求计一次 `model_calls`，请求失败也计数；
上游异常不得把密钥写入任务报告或历史。

## 验收

- 使用安装的官方 SDK 和 `httpx.MockTransport` 验证真实序列化报文，
  不连接外部 API。
- 两协议均测试三轮请求、原始请求保留、早期工具结果保留和最终回复。
- Chat Completions 覆盖同轮多工具、直接文字回复、非法 JSON、缺失
  ID/名字、重复 ID、非 function 调用、截断回复、空回复和请求失败。
- 两协议均验证未知工具、参数校验及工具步数限制未被绕过。
- 显式 `plan()` 的旧 JSON 接口保持可用；默认 mock 不变。
- 全量 pytest 通过后再更新 README 的能力边界；测试不是中转站实测。
- 仓库未提供简历文件，不修改仓库外不明确的简历。
