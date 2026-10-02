# 检索与上下文

默认流程：Dense 与 BM25 召回 → RRF 融合 → Rerank → 选择正文。每轮任务使用固定发布版本，Dense 和 BM25 在召回前过滤该版本的文档集合。

| 配置 | 当前选定值 |
| --- | ---: |
| Dense / BM25 候选 | 每路最多 50 |
| RRF k | 10 |
| 精排候选 | 最多 24 |
| 精排最低分 | 0.001 |

参数见 [retrieval-selected.json](retrieval-selected.json)。最低分在完整精排后应用，保留 `score >= 0.001`，结果可为空；关闭精排时不使用这个阈值。

fixed 固定检索路线，Agent 仍可重复搜索和阅读。auto 允许每次选择 dense、bm25、hybrid 及精排开关；纯 BM25 且关闭精排时不需要模型。

SourceSession 从排名中选择正文，在近似分数组内优先补充不同文档，并去除当前上下文中同版本的重叠区间。`context_chunks` 限制单次片段数；`context_tokens` 当前按 UTF-8 字节上界计量完整工具正文。历史调用量不扣减下一次额度。

共享 Collection 的 BM25 统计随更新变化，旧任务的文档集合固定，但 BM25 分数可能变化。原文分页与引用见[来源读取](sources-and-evidence.md)。
