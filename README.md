# SafeDesk

**一个受控执行代码任务的本地 Agent：规划 → 检索项目上下文 → 调用注册工具 → 生成候选改动 → 人工确认后应用 → 跑固定测试 → 输出验收报告。**

![CI](https://github.com/1250789832phy-dotcom/safedesk/actions/workflows/ci.yml/badge.svg)

---

## 它解决什么问题

让模型去改代码，最大的风险不是改错，而是**改到不该改的地方、绕过测试、把失败当成功上报**。SafeDesk 把这条链路拆成有边界、可审计的步骤：

- 文件访问被限制在 `workspace/` 内，路径规范化后校验目录边界，**越界先拒绝再读**，不是读完再检查
- 工具调用走注册表 + 参数校验，未注册工具直接拒绝
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

评测集 6 个用例，覆盖成功路径与四类失败归因：

| 用例 | 验证内容 | 归因层 | 结果 |
|---|---|---|---|
| `read_only` | 真实读取工作区文件并回报内容 | — | 通过 |
| `clarification` | 模糊需求必须反问而非猜测 | — | 通过 |
| `rag_hit` | 检索命中时来源可追溯 | context | 通过 |
| `rag_miss` | 检索未命中时不得编造来源 | context | 通过 |
| `tool_rejection` | 未注册工具被拒绝、任务判失败 | tool | 通过 |
| `test_failure` | 固定测试非零退出被如实上报 | testing | 通过 |

**6 / 6 通过。** 完整报告见 [`data/evaluation.json`](data/evaluation.json)。

> 当前的评测运行使用确定性 mock planner（`"mode": "mock"`、`"model_calls": 0`），目的是让归因逻辑本身可重复验证，不依赖模型随机性。真实模型接入为下一步工作，见"已知限制"。

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

启动接口：

```bash
uvicorn apps.api.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000> 使用 Web 工作台。

配置模型（可选，不配置则使用 mock planner）：

```bash
cp .env.example .env
# 填入 OPENAI_API_KEY 与 base_url
```

## 测试

```bash
python -m pytest -q
```

覆盖 9 个维度：Agent 主流程、受控改动全链路、上下文检索、模型配置、上游模型发现、Web 界面、评测框架自身、文件读取契约、健康检查。

CI 矩阵覆盖 Ubuntu / Windows 与 Python 3.11 / 3.12 / 3.13，共 6 组。核心测试在两种系统运行；9 个依赖真实 Windows DPAPI 的加密配置用例仅在 Windows 运行，在 Linux 上通过 `skipif` 明确跳过，其余用例不跳过。

## 评测

```bash
python -m evaluation.run
```

输出到 `data/evaluation.json`，并按失败用例数量返回退出码，可直接用于 CI 门禁。

## 已知限制与设计取舍

**检索层**：已完成切分与上下文预算，检索当前为关键词匹配。`ContextIndex` 已预留 `embedding_provider` 接口，向量模式实现与重排序为下一步工作。

**规划器**：默认 mock 实现使归因测试可重复；OpenAI 兼容适配器已实现但未纳入默认评测路径。

**语料范围**：切分器覆盖 Markdown 与 Python，其他语言回退到段落切分。

**持久化**：任务与记忆当前在进程内存中；改动备份与评测输出落在 `data/`。

**平台支持**：模型配置的密钥加密持久化依赖 Windows DPAPI（`crypt32`），仅支持当前 Windows 用户。双系统 CI 不代表 Linux 已支持加密配置保存；Linux 可运行不依赖该功能的核心流程与测试。

**边界**：面向本地单机使用。不含无人值守监听、唤醒词、任意 shell/Python 执行、自动部署或推送、以及不受限制的自动修复。

## 许可

[MIT](LICENSE)
