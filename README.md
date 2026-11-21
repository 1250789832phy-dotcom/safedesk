# SafeDesk

**一个受控执行代码任务的本地 Agent：规划 → 检索项目上下文 → 调用注册工具 → 生成候选改动 → 人工确认后应用 → 跑固定测试 → 输出验收报告。**

![CI](https://github.com/1250789832phy-dotcom/safedesk/actions/workflows/ci.yml/badge.svg)

---

## 它解决什么问题

让模型去改代码，最大的风险不是改错，而是**改到不该改的地方、绕过测试、把失败当成功上报**。SafeDesk 把这条链路拆成有边界、可审计的步骤：

- 文件访问被限制在 `workspace/` 内，路径规范化后校验目录边界，**越界先拒绝再读**，不是读完再检查
- 工具调用走注册表 + 参数校验，未注册工具直接拒绝
- 每个新任务最多调用工具 25 次；可重试的工具错误最多尝试 3 次，退避 0.5 秒、1 秒
- 候选改动先出 diff，经独立确认令牌后才落盘，写前备份、可回滚
- 测试命令固定，不接受模型任意拼命令；测试失败如实上报为任务失败
- 发布门禁只做**放行前检查**，不做真实部署

## 架构

```
                    ┌──────────────────────────────────────┐
   用户请求  ──────► │  AgentService                        │
                    │  ├─ Planner      规划/澄清/验收条件    │
                    │  ├─ ContextIndex 检索项目上下文        │
                    │  ├─ ToolRegistry 工具注册与分发        │
                    │  ├─ ChangeSet    diff / 备份 / 回滚    │
                    │  └─ TaskState    状态机与轨迹          │
                    └───────────────┬──────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
   apps/api                   apps/web                    evaluation/
   FastAPI 接口               Web 工作台                   评测与归因
```

### 目录

| 路径 | 职责 |
|---|---|
| `agent/service.py` | 任务编排入口，状态流转 |
| `agent/planner.py` | 规划器接口 + mock 实现 + OpenAI 兼容适配器 |
| `agent/tools.py` | 工具注册表：`read_file`、`list_tasks`、`search_code`、固定 `pytest` |
| `agent/changes.py` | 候选改动的 diff、确认令牌、备份、回滚 |
| `agent/state.py` | 任务状态机 |
| `agent/models.py` | `Plan`、`ToolCall`、`TaskStatus` |
| `agent/settings.py` | 模型配置与上游模型发现 |
| `context/index.py` | 文档切分（Markdown 标题级 / Python 函数级）、检索、上下文预算 |
| `context/memory.py` | 记忆存储 |
| `apps/api/main.py` | FastAPI 应用与路由 |
| `apps/web/index.html` | 单文件 Web 工作台 |
| `evaluation/run.py` | 评测框架，输出结构化报告 |

## 评测结果

评测集 6 个用例，覆盖文件读取、澄清、检索命中与未命中、
未注册工具拒绝和固定测试失败上报：

| 用例 | 验证内容 | 评测失败归因 (`failure_layer`) | 结果 |
|---|---|---|---|
| `read_only` | 真实读取工作区文件并回报内容 | `none` | 通过 |
| `clarification` | 模糊需求必须反问而非猜测 | `none` | 通过 |
| `rag_hit` | 检索命中时来源可追溯 | `none` | 通过 |
| `rag_miss` | 检索未命中时不得编造来源 | `none` | 通过 |
| `tool_rejection` | 未注册工具被拒绝、任务判失败 | `none` | 通过 |
| `test_failure` | 固定测试非零退出被如实上报 | `none` | 通过 |

`failure_layer` 只归因评测用例未满足预期的情况，通过时为 `none`。
`tool_rejection` 和 `test_failure` 预期 Agent 将任务判为失败；
正确拒绝未知工具、正确上报注入的非零测试结果，均表示评测通过。
后者使用模拟测试工具返回值，不代表在评测中启动了一次真实失败的 pytest。

**6 / 6 通过。** 完整报告见 [`data/evaluation.json`](data/evaluation.json)。

> 当前的评测运行使用确定性 mock planner（`"mode": "mock"`、`"model_calls": 0`），目的是让归因逻辑本身可重复验证，不依赖模型随机性。真实模型接入为下一步工作，见"已知限制"。

## 快速开始

需要 Python 3.11 或更高版本。以下命令均在仓库根目录执行。

Windows PowerShell（直接使用虚拟环境的 Python，无需激活）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --reload --port 8000
```

Linux / macOS：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn apps.api.main:app --reload --port 8000
```

Windows PowerShell 下运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

打开 <http://127.0.0.1:8000> 使用 Web 工作台。

配置模型（可选；没有已保存的工作台配置时，默认使用 mock planner）：

`.env.example` 仅供参考，应用不会自动加载 `.env`。若使用环境变量，
请在启动服务前，在同一个终端设置以下变量；已运行的服务需要停止后重新启动。

Windows PowerShell：

```powershell
$env:SAFE_DESK_MODEL = "openai"
$env:OPENAI_API_KEY = "replace-with-your-api-key"
$env:OPENAI_MODEL = "your-model-name"
# 可选：使用兼容服务时设置其基础地址
# $env:OPENAI_BASE_URL = "https://your-provider.example/v1"
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --reload --port 8000
```

Linux / macOS（先激活上面的虚拟环境）：

```bash
export SAFE_DESK_MODEL=openai
export OPENAI_API_KEY="replace-with-your-api-key"
export OPENAI_MODEL="your-model-name"
# 可选：使用兼容服务时设置其基础地址
# export OPENAI_BASE_URL="https://your-provider.example/v1"
python -m uvicorn apps.api.main:app --reload --port 8000
```

环境变量方式使用 Responses 协议。若上游只支持 Chat Completions，
请使用工作台中的模型配置选择对应协议。已保存的工作台配置
（`data/model-settings.json`，包括 mock 模式）会优先于启动时的环境变量生效。

这些变量只会在服务进程启动时读取；修改变量后必须停止并重新启动服务。
恢复 mock 时应区分配置来源：

- 如果已有工作台配置，在页面“模型设置”中点击“切回模拟模式”。
  这会保存 mock 模式并保留加密密钥，重启后仍为 mock。
- 若还要移除已保存的地址、模型和密钥，使用“删除已存配置”并确认。
  该操作也会保存 mock 模式，不会恢复为环境变量配置。

只有在不存在已保存的工作台配置时，才能通过以下命令清空当前终端变量、
重启服务并恢复默认的 mock planner。清空变量不能覆盖已保存的 API 模式。

Windows PowerShell：

```powershell
Remove-Item Env:SAFE_DESK_MODEL, Env:OPENAI_API_KEY, Env:OPENAI_MODEL, Env:OPENAI_BASE_URL -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --reload --port 8000
```

Linux / macOS：

```bash
unset SAFE_DESK_MODEL OPENAI_API_KEY OPENAI_MODEL OPENAI_BASE_URL
python -m uvicorn apps.api.main:app --reload --port 8000
```

可通过 `GET /settings/model` 确认 `mode` 为 `mock`。
保留加密密钥时 `has_api_key` 可以仍为 `true`，不代表正在使用 API 模式。

## 测试

Windows PowerShell（不激活虚拟环境也可以直接运行）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Linux / macOS（已激活上面的虚拟环境）：

```bash
python -m pytest -q
```

覆盖 9 个维度：Agent 主流程、受控改动全链路、上下文检索、模型配置、上游模型发现、Web 界面、评测框架自身、文件读取契约、健康检查。

CI 矩阵覆盖 Ubuntu / Windows 与 Python 3.11 / 3.12 / 3.13，共 6 组。核心测试在两种系统运行；9 个依赖真实 Windows DPAPI 的加密配置用例仅在 Windows 运行，在 Linux 上通过 `skipif` 明确跳过，其余用例不跳过。

## 评测

```bash
python -m evaluation.run
```

输出到 `data/evaluation.json`。所有用例通过时退出码为 `0`，
存在失败用例时为 `1`，不是失败用例数量；具体数量见报告中的
`passed`、`failed` 字段。运行会覆盖该文件，耗时等动态字段可能产生 Git 差异。

当前 CI 的 `Export evaluation report` 步骤设置了 `continue-on-error: true`，
用于尽力导出报告；即使该步骤失败，工作流仍可能为绿色。
因此，CI 绿色不能单独证明导出的评测报告通过，应同时检查步骤结果和报告。
若需把此命令作为强制门禁，调用方必须让非零退出码阻断后续流程。

## 已知限制与设计取舍

**检索层**：任务服务 `AgentService` 默认使用 embedding 检索，结果标记为
`local_hash_embedding`；可在构造时传入 `retrieval_mode="keyword"` 或
`retrieval_mode="hybrid"` 显式切换。底层 `index.search(query)` 仍默认使用
关键词检索（`keyword_fallback`），现有六项评测也显式选择 keyword，
保持原有关键词命中与未命中契约。索引默认离线生成
256 维字符哈希向量：文本 casefold 后取字符 1-gram / 2-gram，经 CRC32
定位并累计带符号计数，最后 L2 归一化，无网络调用或额外依赖。
`index.search(query, mode="embedding")` 使用余弦相似度，返回
`local_hash_embedding`；`mode="hybrid"` 返回 `hybrid`，以 0.5 权重合并
按候选最高关键词分归一化的关键词分和 `(cosine + 1) / 2`，最多返回五段。
仅保留关键词命中或余弦相似度为正的候选。显式传入
`embedding_provider=None` 可禁用向量，但此时不能使用 embedding / hybrid。
哈希向量反映字符重叠与拼写相似度，不具备训练得到的语义理解能力。

**工具超时与重试**：默认每次等待最多 10 秒，连同退避受调用方剩余任务
时间预算约束；耗尽后抛出 `ToolError`，服务将其记录为工具失败。
未知工具与参数校验失败直接返回结构化错误，权限错误与文件不存在不重试。
已结束且抛出可重试 `OSError` 的调用可以重新执行；超时但仍在运行的线程
只重试等待同一个结果，避免重叠调用。Python 线程不能被强制终止，
因此超时后工具可能继续运行，进程退出也可能等待该线程结束；
这不是对任意工具的强制取消保证。固定 pytest 子进程仍有独立超时。
`AgentService` 每个新任务重置调用计数，幂等命中则直接复用旧任务；
直接使用注册表的调用方可用 `reset_calls()` 开始新的计数周期。

**规划器**：默认 mock 实现使归因测试可重复；OpenAI 兼容适配器已实现但未纳入默认评测路径。

**语料范围**：切分器覆盖 Markdown 与 Python，其他语言回退到段落切分。

**持久化**：任务与记忆当前在进程内存中；改动备份与评测输出落在 `data/`。

**回滚边界**：候选确认令牌、已应用改动的 `change_id` 索引也只存在于当前
进程内存。重启服务（包括开发模式自动重载）后，旧令牌和旧改动 ID 不再有效；
即使 `data/backups/` 中的 `.bak` 文件仍在，当前实现也不会据此自动恢复回滚索引。
同一进程内，若目标文件在应用后又被修改，回滚会以 `file_changed` 拒绝覆盖，
以免丢失后续编辑。因此“可回滚”不代表跨重启的自动恢复或版本管理，
重要改动仍应使用 Git 等独立版本管理方式保存。

**平台支持**：模型配置的密钥加密持久化依赖 Windows DPAPI（`crypt32`），仅支持当前 Windows 用户。双系统 CI 不代表 Linux 已支持加密配置保存；Linux 可运行不依赖该功能的核心流程与测试。

**边界**：面向本地单机使用。不含无人值守监听、唤醒词、任意 shell/Python 执行、自动部署或推送、以及不受限制的自动修复。

## 许可

[MIT](LICENSE)
