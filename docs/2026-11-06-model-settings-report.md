# 模型配置本地保存验证记录

日期：2026-11-06。用户明确要求在页面填写 API 地址和密钥，并写入文件避免重复填写。本记录只针对该增量，不代表 SafeDesk 全部阶段交付。

## 阶段报告

阶段与固定权重：涉及任务书阶段 1（20%）和阶段 4（15%）的模型适配与界面增量，不单独新增权重，不据此计入整个阶段完成百分比。

本阶段目标：中文页面保存 URL、模型、协议和密钥；重启自动加载；密钥不以明文持久化、不回显；保存和实际网络测试分开。

新增文件：

- `agent/settings.py`：配置校验、Windows 当前用户 DPAPI、原子写入、重载及模式切换。
- `apps/api/settings.py`：本机设置接口、同源与请求头检查、脱敏错误。
- `tests/test_model_settings.py`：26 项配置、SDK 协议、安全与失败回归测试。
- 本目录下的实现计划和本报告。

修改文件：

- `agent/planner.py`：两种接口协议、连接探测、请求异常脱敏。
- `apps/api/main.py`：注册设置接口，任务/修复规划与配置切换互斥。
- `apps/web/index.html`：中文模型配置表单、显示密钥、保存、探测、模拟模式和确认删除。
- `README.md`：配置步骤、隐私与数据范围、实际验证结果和原有失败。

保留且未修改的重要文件：`day01.py` 至 `day16.py` 等学习练习、`tools/read_file_demo.py`、`workspace/`、既有测试预期、根目录学习文件。保留用户 `/health` 返回 `busy` 的修改。

运行命令（项目目录）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --tb=short
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_model_settings.py -q -p no:cacheprovider --tb=short
.\.venv\Scripts\python.exe -X utf8 -m pytest tests -q -p no:cacheprovider --tb=short
.\.venv\Scripts\python.exe -X utf8 -m evaluation.run
```

实际输出与退出码：

| 验证 | 实际输出 | 退出码 |
| --- | --- | --- |
| 修改前全量基线 | `2 failed, 69 passed` | 1 |
| 初始新增配置测试，先观察失败 | `18 failed` | 1 |
| 初始实现后配置测试 | `18 passed` | 0 |
| 追加连接释放、失败回滚和协议回归，修复前 | `6 failed, 20 passed` | 1 |
| 修复后配置测试 | `26 passed in 3.14s` | 0 |
| 本轮全量测试 | `2 failed, 95 passed in 9.01s` | 1 |
| 固定评测 | `mode: mock, passed: 6, failed: 0` | 0 |

浏览器运行：使用本机已有 Playwright 与 Edge，无新增应用依赖。执行脚本位于忽略目录 `data/verify_model_settings.cjs` 和原有 `data/verify_chinese_ui.cjs`；前者分 prepare、verify 两次执行，中间实际停止并重新启动 Uvicorn。

实际浏览器输出：

```text
settings prepare: passed 8, browserErrors []
settings verify after process restart: passed 3, browserErrors []
Chinese UI smoke: passed 10, browserErrors []
```

初次执行新增浏览器脚本因请求上下文未设置 Base URL 报 `apiRequestContext.get: Invalid URL`，退出 1；为浏览器上下文补充 Base URL 后重跑得到上述结果。未将首次失败隐藏为通过。

通过的验收项：实际 Windows DPAPI 往返；保存文件不包含虚构密钥明文；重新创建应用及实际服务重启后加载；只返回脱敏状态；同源与自定义写请求头校验；同地址留空复用、变更地址拒绝复用；保存失败保留原文件与运行配置；替换/失败候选连接关闭；两种协议的真实 SDK 配合假 HTTP 响应解析；短探测与任务执行分离；界面加载/失败/禁用/显示/删除确认；1280、375、320 像素布局检查。

失败/未验证项：

- `tests/test_agent.py::test_health_route_remains_compatible`：实际 `busy`，测试期待 `ok`，原有失败，不擅自改动。
- `tests/test_changes.py::test_apply_rejects_workspace_escape_and_symlink`：拒绝越界成功，但错误文字与符号链接断言不一致，原有失败，不属于本次配置变更。
- 无真实密钥或模型请求；SDK 测试使用 `httpx.MockTransport`，页面连接错误使用明确的模拟响应，不能证明任何外部服务已连通。
- 无实际麦克风、STT、TTS 或 Embedding 服务验证。未重新创建干净虚拟环境安装。
- 仅支持当前 Windows 用户的加密持久化，非 Windows 支持没有验收。

安全与数据边界检查：

- `data/model-settings.json`、临时文件、日志、`.env` 和 `.venv` 经 `git check-ignore` 实际确认被忽略。
- 用户原内存任务记录在首次重启前已备份到忽略目录 `data/task-history-before-model-settings.json`；没有删除备份或练习文件。
- 浏览器测试仅使用虚构密钥；测试后确认删除虚构配置，当前服务为 `mock`、`has_api_key: false`，配置文件仅留空的模拟模式记录。
- 本地日志仅显示 Uvicorn 状态与请求，不记录配置请求正文。设置不使用浏览器存储。
- `git diff --check` 返回 0，但项目主体是未跟踪文件，此命令不能替代未跟踪文件审查或正式发布检查。
- `git remote -v` 无输出；未暂存、提交、创建远程仓库或推送。发布仍须单独确认，当前也存在测试门禁失败。

当前全项目进度：不更新原有阶段权重；阶段 0–5 尚不能依据本次增量全部验收，全量测试失败，不能称为本地交付候选。

项目能力状态：本轮由助手代为实现；没有新增用户独立解释或迁移证据，不提高学习掌握状态。

尚未解决问题：原有两项全量失败、真实服务兼容性及任务效果未验证、干净环境复验未重跑。DPAPI 不防御相同 Windows 用户权限下的恶意程序。

唯一下一步：用户在页面填写自己的服务地址、模型和密钥，保存后自主确认是否进行可能计费的连接测试；不要把密钥发到聊天中。
