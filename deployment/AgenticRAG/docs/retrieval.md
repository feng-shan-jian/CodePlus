# 固定版本检索、逐 Chunk Rerank 与 Context

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

BM25 不请求查询 Embedding；`rerank=false` 时零模型连接，启用时仅调用 Rerank。fixed 模式只接受 query，仍可重复搜索/改写/open。auto 可在 `session.search(query, strategy='bm25', rerank=False)` 或直接 RetrievalSearch.search 中指定本次路线/开关；省略项使用运行基础配置，不沿用前一次选择。选择局部解析，不修改服务的共享 route 或运行配置。fixed 直接传覆盖参数同样报错。SourceSession 保留原 `dense=` 接点，DenseSearch 的显式上限接口不变。

每路使用冻结的 `dense_candidates` / `bm25_candidates`。`RetrievalSearch.search` 的 `limit` 只限制完整评分后的返回数量，完整融合顺序仍在 trace 中。启用 Rerank 时，稳定 ID 去重后的前 `rerank_candidates` 条原始 Chunk 进入评分；关闭时 SourceSession 接纳召回候选并集。原始分数类型分别为 `cosine_similarity` 和 `bm25`。

RRF 只用稳定 Chunk ID 和每路从 1 开始的原始排名：`sum(1 / (rrf_k + rank))`。同一路重复 ID 只取第一次出现的原排名，不压缩其他候选的排名；缺席不贡献分数。同分按 Chunk ID 排序，融合分数类型为 `rrf`，不表示概率或答案置信度。

`result['trace']` 保留 query、库/版本、固定 Collection/过滤条件、索引参数/身份、实际候选上限、每路原候选的 ID/分数/排名、模型耗时、完整融合顺序和最终 ID。`selection` 记录 mode、requested 与 effective；`preceding_call_id` 关联同一运行的上一次搜索，表示调用顺序，不推断模型的重试意图。异常保留已完成分支和失败阶段。SourceSession 将轨迹存入 schema11 的 `retrieval_traces`，通过 call_id 关联原 source_calls。R18/R19 沿用现有表与 JSON，无新 migration，历史十一份 SQL 保持原字节。

一路合法为空允许融合；任一路失败整次报错。失败轨迹不产生 `source_candidates` 或已送达证据，先前已确认的正文仍可引用。轨迹、排名和分数不进入模型正文；引用仍要求既有交付回执。

Rerank 使用运行冻结的 profile、归档 `ChunkInput.index_title` 及 canonical 正文；精排前不拼邻块，不用文件名代替标题。正式本地 client 的 `config.model_cache` 提供冻结 tokenizer 资产；自定义 provider 可显式传 `RetrievalSearch(..., rerank_tokenizer=...)`，其 profile 必须一致。`FrozenTokenizer.rerank` 完整计入 query、标题、模板和特殊 token；按实际条数及 padded-token 上限拆批，不截断。所有 ID、实际 token 数及完整响应通过校验后，才按分数降序、ID 升序合并。任一批失败整次失败，无未精排回退。每条输入 hash/实际 token、每批 request ID、评分、排队/加载/推理及墙钟耗时写入同次 `trace.rerank`。

整个召回、原文读取、计数与精排仍共用外层 reader；子 Rerank 请求使用原模型 reader 和真实 handle 的 `wait_finished`。取消、结果报错或 client 关闭不是 GPU 完成回执，未完成读者继续保护旧版本。

Context 的 `query-relative-v1` 是可复核的试验规则：在当前 query 排名中，以组首分数的绝对值乘 2% 划分相关性近似组，组内先选尚未选过的文档，内部保持原排名；不设置文档配额，不比较不同 query 的分数。同文档不同位置及冲突正文保留；只扣除同文档版本在当前 `evidence_windows` 和本次已选结果中的重叠 Unicode 区间。已返回而未进入当前窗口的正文不受历史 `source_candidates` 抑制，仍累计原 source-return/片段 reservations。

片段数和最终工具完整序列化上界沿 `SourceSession` 计量；不能整段放入时按 canonical 区间折半到完整序列化可放入，再校验后提交。`trace.context` 记录规则、计量身份、选择前窗口与用量、每条原始/可选/返回/省略区间及限制原因。宿主完整 JSON 上界、窗口累计与送达资格仍由既有 `DeliveryGateway`/ModelControl 控制。receipt 的 `window_transition.removed_from_request` 只描述位置移出；`on_confirmed_compact_removed` 必须结合 `confirmed` 且 `purpose=compact` 解读，失败 compact 不宣称已清空。旧确认资格与当前保留窗口分开。

这里的 `evidence_windows` 是宿主当前保留的原文窗口，可在 HTTP 发送前由 `retain_prepared_window` 同步；not_sent/unknown 不自动回滚该窗口。连续工具调用中尚未 retain 的 pending 正文可再次选取；已在宿主保留窗口中的位置按该窗口去重。两者都不绕过 `delivered_evidence` 的确认交付要求。

现有内部 runner 增加 `bm25`、`hybrid`，保留 `build`、`dense`。BM25 不要求 `--cuda-python` / `--model-cache`，也不读取未连接 worker 的状态。四种 action 都使用同一个入口：

```powershell
<installed-python> -I -B deployment/AgenticRAG/eval/dense_runner.py bm25 --root <build-root> --endpoint <milvus-uri> --ids deployment/AgenticRAG/eval/development-ids.json --dataset-hash f80fc4033be6625b19da2af9529cf925d147e9ad62c95b943df2c3d08ec2e898 --report <new-report.json>
```

Dense/Hybrid 或 `--rerank` 另传两项模型参数。R18 的固定 Hybrid 开关对照增加 `--answer-tokenizer <固定资产绝对路径>`，同一次检索同时取得 TopK10 排名和 SourceSession Context。每题独立运行绑定同一个发布版本，使用固定工具结果请求模板、实际完整 JSON 和生产上界 meter；仅 prepare，随后记为 not_sent，不运行回答模型或伪造 confirmed。工具选择和最终请求窗口失败分别保留，`COMPLETE_WITH_CONTEXT_ERRORS` 不伪装 PASS；排序指标与 Context 覆盖分别评分。

工具结果插入宿主 JSON 后会新增转义字符，SourceSession 的工具正文可放入不保证完整请求仍可放入。最终窗口 gate 拒绝时，已成功的排名与所选正文继续作为诊断保留，`prepared` 正文为空，并以零覆盖纳入原 177 题分母；未测得的上界以未知和样本数报告。R18 不增加固定余量或调大单组预算；完整预算策略及参数冻结分别留 R19/R23。

运行只读 query/corpus；原官方与 source-span 评分在独立进程执行。每档固定 200 个 medium ID、TopK10，官方检索分母为 177；初始化/单题/Context/附加查询/关闭失败均保留记录，不能删除失败题。开关是唯一组间配置差异；真实模型评分不代表答案正确率，试验参数未完成 R23 冻结。此前三路结果见 [R17 执行记录](implementation-records/R17.md)。
