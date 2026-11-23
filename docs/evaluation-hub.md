# 数据中台与评测中台

SafeDesk 的本地数据中台接收 Markdown / Python 文本和标注后的查询，将它们保存成只增不改的数据集快照。评测中台在指定快照上复用 Agent 的检索、重排和上下文预算逻辑，保存逐例结果，并对比同一数据集版本上的两次运行。

## 跑一次重排对照

在项目根目录的 PowerShell 终端运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

打开本机 Swagger（`http://127.0.0.1:8000/docs`）可以逐个调用接口。也可以在第二个 PowerShell 终端执行：

```powershell
$hubUrl = 'http://127.0.0.1:8000/eval'
$dataset = Invoke-RestMethod "$hubUrl/datasets" -Method Post -ContentType 'application/json' -InFile 'examples/retrieval-dataset.json'
$config = @{dataset=$dataset.name; version=$dataset.version; mode='keyword'; rerank=$false}
$baseline = Invoke-RestMethod "$hubUrl/runs" -Method Post -ContentType 'application/json' -Body ($config | ConvertTo-Json)
$config.rerank = $true
$candidate = Invoke-RestMethod "$hubUrl/runs" -Method Post -ContentType 'application/json' -Body ($config | ConvertTo-Json)
Invoke-RestMethod "$hubUrl/compare?baseline=$($baseline.id)&candidate=$($candidate.id)" | ConvertTo-Json -Depth 6
```

样例中五个干扰文档和正确文档的关键词得分相同。关闭重排时，正确文档排在第 6 位；打开重排后，完整短语命中让正确文档排到第 1 位。预期 `hit_at_5` 与 `mrr_at_5` 的差值均为 1，`policy-hit` 从失败变为通过。这是用于验证流程的两例人工样本。

## 接口与指标

| 接口 | 作用 |
| --- | --- |
| `POST /eval/datasets` | 导入数据集。相同名称与相同内容重复提交返回原版本，内容改变则创建新版本。 |
| `GET /eval/datasets` | 列出版本、SHA-256 摘要、文档数和用例数。 |
| `GET /eval/datasets/{name}/{version}` | 读取快照，包括原始文档和用例。 |
| `POST /eval/runs` | 执行固定版本的评测。配置支持关键词、哈希向量、混合检索、重排开关和 Token 预算。 |
| `GET /eval/runs` / `GET /eval/runs/{run_id}` | 查看历史摘要或完整报告。 |
| `GET /eval/compare?baseline=…&candidate=…` | 比较指标和发生通过/失败变化的用例；数据集版本不同返回 409。 |

收录的文档以相对文件名作为来源标签，服务只处理提交的文本，不根据这些名称读取磁盘。每条用例标注一个期望来源，或用 `null` 标注预期无命中。Hit@1、Hit@5 和 MRR@5 在有标注来源的用例上按预算裁剪前的块排名计算；上下文命中率看正确来源是否真正入选。预期无命中的用例在前 5 块为空时通过，因此把预算设为 0 不会掩盖误召回。没有对应类别时，该类别的指标返回 `null`。

数据默认持久化到本机 `data/evaluation-hub.sqlite3`。接口只接受本机请求，拒绝跨来源浏览器请求，响应不缓存。单个数据集最多 100 个文档、25 万字符、1000 个切分片段和 200 个用例。评测同步执行，不调用模型或覆盖原来六个 Agent 测试场景的报告；原有独立 Agent 行为评测入口仍可通过 `python -m evaluation.run` 使用。数据库会保留导入的原始文本，请使用测试语料。
