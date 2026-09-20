# 知识库标准回归评测

`benchmark` 使用日常 `KnowledgeService.search`，检验一个固定真实资料集的检索结果。原有 `evaluate` 继续用于 FLAT/HNSW、BM25/RRF 独立实验，两者不修改日常配置。

## 运行

```powershell
# 核对原件、解析文本指纹和逐题引用；不启动模型或数据库。
python -m codeplus.knowledge benchmark --dataset <数据集目录> --check

# 库内必须恰好包含数据集指定的资料版本。模型、分块沿用该库的配置。
python -m codeplus.knowledge benchmark --dataset <数据集目录> --kb-id <知识库ID> --managed-local

# 在同一个混合库和固定 Top-K 下，分别保留三种策略的报告。
python -m codeplus.knowledge benchmark --dataset <数据集目录> --kb-id <知识库ID> --mode dense
python -m codeplus.knowledge benchmark --dataset <数据集目录> --kb-id <知识库ID> --mode bm25
python -m codeplus.knowledge benchmark --dataset <数据集目录> --kb-id <知识库ID> --mode hybrid --candidates 50 --rrf-k 60

# 按相同题目与口径重算保存结果，不重新检索。
python -m codeplus.knowledge benchmark --dataset <数据集目录> --replay <report.json>
```

`--managed-local` 仅用于项目自带本地部署；远程服务省略它。实时运行在本进程启用知识库，不修改用户配置，也不导入、删除或重建库。重建资料库可复用现有 create/import 命令，再把新库 ID 传入 benchmark。

`--mode`、`--candidates` 和 `--rrf-k` 都是可选的，仅覆盖本次运行；省略的字段沿用现有 knowledge 配置。默认配置为 `auto`、50、60：auto 在新混合库使用 hybrid，在已知旧向量库使用 dense。显式对旧向量库请求 bm25/hybrid 会计为查询失败，需另建混合库。候选数必须为 1–16384 的整数，RRF 常量必须为有限正数，复用日常配置校验。

最终 Top-K 固定取自数据集协议，不能用 benchmark 参数修改。hybrid 每路取 `max(candidates, Top-K)` 后融合；dense/bm25 只取该路 Top-K，候选数与 RRF 配置不参与单路排名。分数分别为 `cosine_similarity`、`bm25`、`rrf`；RRF 分数不代表相似度或概率。

`--replay` 和 `--check` 不连接模型或数据库，且拒绝上述三个检索覆盖参数。旧报告仍可回放；回放只按保存的命中重算评分，不补造旧报告缺失的策略信息，也不修改原报告。

输出写入数据集的 `runs/<时间_编号>/report.json` 和 `report.md`，每次保留独立结果。JSON 报告包含数据集指纹、库版本、库内实际保存的完整 `profile`、依赖版本、逐题原始返回和分组指标。`implementation_sha256` 覆盖检索、共享 retrieval、profile/model、配置校验及 CLI 等相关源码。

`retrieval_request` 保存配置覆盖后的请求模式、候选数、RRF 常量和数据集 Top-K；每题的 `retrieval` 直接保存服务返回的请求/实际模式、实际候选数、RRF 参数、两路数量及原有 profile hash、index、metric。单路的实际 `rrf_k` 为 null，未执行路的数量为 null；查询失败保留原错误，不推断实际检索参数。查询错误计入分母并单独列出；库在运行期间变化则拒绝产出可比较的报告。

Markdown 报告也显示请求参数和每题实际策略、候选数、RRF。实际 RRF 为 null 时显示“—”；旧报告或失败查询缺少的策略信息显示“未记录”。

三策略应使用同一个库和 revision、同一数据集指纹进行比较。独立 CLI 进程的模型准备和首题冷启动耗时不能直接当作稳定的热查询性能；本命令不额外预热或重试查询。

## 数据集结构

- `dataset.json`：版本、固定协议和资料清单。
- `questions.json`：固定问题、参考答案、证据区间和分组。
- `corpus/`：原件快照；`text/`：与证据偏移对应的冻结解析文本。
- `review.json`：人工逐题意见，独立于冻结问题。
- `runs/`：每次运行结果；改题、换资料或改评分规则应发布新版本。

`dataset.json` 的最小结构：

```json
{
  "id": "my-knowledge-regression",
  "version": "1.0.0",
  "protocol": {"top_k": 5, "followup_query": "latest_turn_only"},
  "documents": [{
    "id": "D01", "file": "example.md", "path": "corpus/D01/example.md",
    "sha256": "原件 SHA-256", "text_path": "text/D01.txt", "text_sha256": "解析文本 SHA-256"
  }]
}
```

`questions.json` 以 `questions` 数组保存题目。每题包含 `id`、`query`、`track`、`answerable`、`reference_answer`、`gold`。证据包含 `source_id`、`char_start`、`char_end`、`quote`；偏移是冻结解析文本的 Unicode 字符下标，左闭右开。无答案题的 `gold` 为空，并提供 `no_answer_reason`；追问题保留 `history` 供人工检查。

## 评分边界

| 分组 | 运行方式 | 指标含义 |
|---|---|---|
| single_turn | 单轮原问题 | 前 K 条是否覆盖指定版本的全部证据 |
| followup | 仅追问最后一句 | 当前首次检索诊断，未模拟完整 Agent 的上下文与补查 |
| no_answer | 保留原问题检索 | 记录结果，不把返回空/非空当成拒答正确或错误 |

每段证据的所有非空白文字都被返回位置覆盖才算命中，允许多个片段合并覆盖；仅忽略空白，不忽略标点、缺词或版本差异。资料身份使用稳定的 D 编号，不用可能重名的文件名。原有实验的严格区间指标保持不变。

“找齐题数”和“证据范围召回率”分别统计，不混称回答正确率。完整回答的事实、引用是否支持结论、缺失信息时是否拒答，需要另行人工验收；本命令不调用回答模型。已经观察过结果的题应作为回归集，不包装成盲测集。

真实个人资料应留在 Git 忽略的本地目录；公共测试只使用合成样例。冻结表示版本稳定，不代表每题已由用户逐条审核。
