# 上游模型获取与选择增量报告

日期：2026-11-06。个人练习项目；本报告不代表生产交付或 GitHub 发布。

## 阶段与目标

- 阶段与固定权重：阶段 1（20%）及阶段 4（15%）的增量子项，不单独折算全项目百分比。
- 本阶段目标：用户在页面填写 Base URL 与密钥，主动获取上游模型目录，自行选择后明确保存启用；保留手动输入。
- 当前全项目进度：本次不重新计算原阶段进度，不增加已完成阶段权重。全量测试仍失败，不能宣称本地项目交付候选已经达标。
- 项目能力状态：助手代为实现并验证；用户独立完成与迁移能力未验收，不据此更新学习进度。

## 文件与保护

新增/修改文件：

- `agent/settings.py`：复用 URL/密钥校验及 SDK 客户端策略；增加不写配置的模型目录查询。
- `apps/api/settings.py`：增加 `POST /settings/model/models`，只需地址与可选密钥，过滤上游错误细节。
- `apps/web/index.html`：中文“获取模型”按钮、原生模型下拉框、加载/空列表/错误状态及旧列表失效处理。
- `tests/test_upstream_models.py`：20 项发现接口、SDK 协议及安全边界测试。
- `README.md`：配置步骤、接口、隐私限制与真实测试结果。
- `docs/superpowers/plans/2026-11-06-upstream-models.md`：增量执行清单。
- 本报告。

保留且未修改的重要文件：`day*.py` 学习练习、根目录学习规则/交接/验收资料、既有 `/health` 行为与两项原有失败测试。未新增应用依赖。

浏览器验证脚本、截图与日志位于已忽略的 `data/`，不作为待发布源码。验证使用独立 8011 端口、临时配置和无效测试密钥，未读取或调用真实凭据。

重启主服务前已备份内存任务历史到忽略目录中的
`data/task-history-before-upstream-models-2b1fcd2f9fbc444da847cbace31240cb.json`。
该备份并不表示应用已经实现任务历史自动持久化。

## 运行命令与实际输出

命令均在项目目录的 PowerShell 执行。基线与 RED 是实现前的历史运行记录；最后三项为完成前复验。

| 检查 | 命令 | 实际关键输出 | 退出码 |
| --- | --- | --- | --- |
| 修改前基线 | `.\.venv\Scripts\python.exe -X utf8 -m pytest tests -q -p no:cacheprovider --tb=short` | `2 failed, 95 passed in 8.27s` | 1 |
| 新增行为 RED | `.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_upstream_models.py -q -p no:cacheprovider --tb=short` | `17 failed, 2 passed in 1.56s`；后续补充第 20 项边界测试 | 1 |
| 所属模块复验 | `.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_model_settings.py tests/test_upstream_models.py -q -p no:cacheprovider --tb=short` | `46 passed in 3.00s` | 0 |
| 全量复验 | `.\.venv\Scripts\python.exe -X utf8 -m pytest tests -q -p no:cacheprovider --tb=short` | `2 failed, 115 passed in 9.14s` | 1 |
| 浏览器复验 | 下方 Node 命令 | `passed: 12`、`browserErrors: []`、`verification: local fake upstream only; no real provider` | 0 |

```powershell
& 'C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe' -e "process.env.RUNTIME_NODE_MODULES='C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'; require('./data/verify_upstream_models.cjs');"
```

全量失败原文要点：

```text
FAILED tests/test_agent.py::test_health_route_remains_compatible
assert {'status': 'busy'} == {'status': 'ok'}

FAILED tests/test_changes.py::test_apply_rejects_workspace_escape_and_symlink
Expected: Symbolic links are not allowed for writes.
Actual:   Target is outside the approved workspace.
```

两项均在本次修改前存在；未删除测试、放宽断言或改动用户行为来获得全绿。

## 通过的验收项

- 真实安装的 SDK 配合 `httpx.MockTransport` 验证认证 `GET /v1/models`；发现请求不要求模型或协议，不发送项目上下文、不推理、不写配置。
- 同地址可复用已加密保存的密钥；换地址必须重填。重复/非法 ID 被过滤，排序后最多返回 2000 项，不自动遍历分页。
- 缺少密钥、非法 URL、跨源/缺头请求、401/403/404/429/500、重定向、超时、异常 JSON/结构及空列表均有覆盖；错误不返回上游原文或密钥。
- 桌面 Edge/Playwright 验证主动获取、选择、明确保存、刷新及留空复用已保存密钥；打开页面不自动访问上游。
- 1280、375、320 像素宽度检查通过；查看截图未见控件溢出。HTML 形状的模型 ID 以文本显示。
- 审查发现“使用临时密钥 B 查询后恢复密钥 A，旧目录仍保留”的问题；先观察到断言失败，再修复并复验。保存同一凭据时可保留目录，恢复配置或修改地址/密钥时清空旧目录。
- 主服务 8000 已重新启动；HTTP 页面包含 `id="fetch-models"`，空发现请求返回 422，证明新路由加载且未触发上游请求。

## 安全与数据边界检查

- 保存配置重启前后及完成前 SHA256 均为
  `C38500A91006B301E256B2AFD1EB49DD1EF78DF5A1BA9BEBA8B951A6CC86713A`；
  未覆盖用户配置。
- 密钥沿用 Windows 当前用户 DPAPI 加密；不回传页面、不写浏览器存储。HTTPS 默认、本机 HTTP 例外；不跟随重定向、不自动使用环境代理、20 秒超时、不重试。
- `git check-ignore` 已确认真实配置及验证运行数据被忽略。没有暂存、提交、创建公开仓库或推送。
- 未变更根目录学习资料；本报告不是发布前全面密钥扫描或干净 checkout 验证的替代品。

## 失败、未验证与下一步

- 尚未解决问题：上述两项基线失败仍阻止全量测试通过。
- 未验证：真实服务商目录、各模型推理权限、协议兼容性、费用、真实语音/Embedding；本次未重做干净虚拟环境安装或全套产品评测。
- 目录只表示供应商返回了这些 ID，不保证密钥可对每个模型执行推理；不支持 `/models` 或返回空列表时仍可手动填写。
- 唯一下一步：刷新当前 8000 页面，在“模型设置”中主动点击“获取模型”，选择需要的模型，再点击“保存并启用”。“测试连接”是另一个需确认且可能收费的真实请求。
