# 固定版本的 Dense、BM25 与 Hybrid

`RetrievalSearch(catalog, run_id, provider, backend)` 执行该运行冻结的 `retrieval.route`。Dense 按索引版的 Embedding 身份编码 query；BM25 把原 query 交给 Milvus 原生 sparse/BM25 字段，`provider` 可以为 `None`。Hybrid 在一个读者保护区间内查询同一不可变 Collection，两路不重新解析 current。原 `DenseSearch` 构造和 `search(query, limit=...)` 保持显式 Dense 及原候选上限语义。

```python
from agentic_rag.retrieval import RetrievalSearch
from agentic_rag.sources import SourceSession

# lease 已由 Catalog 创建；config、库与版本在本次运行内固定。
search = RetrievalSearch(catalog, lease.run.run_id, provider, backend)
result = search.search(query, limit=10)
session = SourceSession(catalog, lease, answer_meter, dense=search)
tool_result = session.search(query)
trace = session.retrieval_trace(tool_result.payload['call_id'])
```

纯 BM25 不请求查询 Embedding，也不连接模型工作进程。正常宿主策略现支持显式 `fixed` 的三路、`rerank=false`，仍只向 Agent 暴露 `query`。SourceSession 保留原 `dense=` 接点；自动模式的动态参数、模型 Rerank 和完整 Context 选择留给 R18/R19。

每路使用冻结的 `dense_candidates` / `bm25_candidates`。`RetrievalSearch.search` 的 `limit` 限制最终返回数量，完整融合顺序仍在 trace 中。未启用 Rerank 时，SourceSession 最多接纳两路候选并集，再沿已有片段/token 预算选取正文；`rerank_candidates` 尚未参与重排。原始分数类型分别为 `cosine_similarity` 和 `bm25`。

RRF 只用稳定 Chunk ID 和每路从 1 开始的原始排名：`sum(1 / (rrf_k + rank))`。同一路重复 ID 只取第一次出现的原排名，不压缩其他候选的排名；缺席不贡献分数。同分按 Chunk ID 排序，融合分数类型为 `rrf`，不表示概率或答案置信度。

`result['trace']` 保留 query、库/版本、固定 Collection/过滤条件、索引参数/身份、实际候选上限、每路原候选的 ID/分数/排名、模型耗时、完整融合顺序和最终 ID。异常携带同样的 `retrieval_trace`，保留已完成分支和失败阶段。正常 SourceSession 将它存入 schema11 的 `retrieval_traces`，通过 `call_id` 关联原 `source_calls`。该新增表不改历史十份 migration 或原有候选/交付表。

一路合法为空允许融合；任一路失败整次报错。失败轨迹不产生 `source_candidates` 或已送达证据，先前已确认的正文仍可引用。轨迹、排名和分数不进入模型正文；引用仍要求既有交付回执。

现有内部 runner 增加 `bm25`、`hybrid`，保留 `build`、`dense`。BM25 不要求 `--cuda-python` / `--model-cache`，也不读取未连接 worker 的状态。四种 action 都使用同一个入口：

```powershell
<installed-python> -I -B deployment/AgenticRAG/eval/dense_runner.py bm25 --root <build-root> --endpoint <milvus-uri> --ids deployment/AgenticRAG/eval/development-ids.json --dataset-hash f80fc4033be6625b19da2af9529cf925d147e9ad62c95b943df2c3d08ec2e898 --report <new-report.json>
```

Dense/Hybrid 另传两项模型参数。运行只读 query/corpus；原官方与 source-span 评分在独立进程执行。每档固定 200 个 medium ID、TopK10，官方检索分母为 177；初始化/单题/附加查询/关闭失败均保留记录，不能删除失败题。结果和真实验收见 [R17 执行记录](implementation-records/R17.md)。
