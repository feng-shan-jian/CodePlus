# 知识库标准回归评测

`benchmark` 使用日常 `KnowledgeService.search`，检验唯一维护的 [分场景题库](README.md) 的检索结果。当前为完整 300 题（260 道公开来源派生题、40 道原创题），另附 20 会话和 20 状态流程；旧个人题库与旧独立合成题库已退役。`evaluate` 保留索引实验接口，不再提供另一套质量评测题源。

## 运行

```powershell
# 核对原件、解析文本指纹和逐题引用；不启动模型或数据库。
python -m codeplus.knowledge benchmark --dataset eval/RAG-eval/full/original --check

# 库内必须恰好包含数据集指定的资料版本。模型、分块沿用该库的配置。
python -m codeplus.knowledge benchmark --dataset eval/RAG-eval/full/original --kb-id <知识库ID> --managed-local

# 在同一个混合库和固定 Top-K 下，分别保留三种策略的报告。
python -m codeplus.knowledge benchmark --dataset eval/RAG-eval/full/original --kb-id <知识库ID> --mode dense
python -m codeplus.knowledge benchmark --dataset eval/RAG-eval/full/original --kb-id <知识库ID> --mode bm25
python -m codeplus.knowledge benchmark --dataset eval/RAG-eval/full/original --kb-id <知识库ID> --mode hybrid --candidates 50 --rrf-k 60

# 按相同题目与口径重算保存结果，不重新检索。
python -m codeplus.knowledge benchmark --dataset eval/RAG-eval/full/original --replay <report.json>
```

`--managed-local` 仅用于项目自带本地部署；远程服务省略它。实时运行在本进程启用知识库，不修改用户配置，也不导入、删除或重建库。重建资料库可复用现有 create/import 命令，再把新库 ID 传入 benchmark。

Windows 使用项目的 `.venv\Scripts\python.exe` 或 `uv run --extra knowledge python` 执行上述命令。完整旧库兼容与重建步骤见 [使用说明](../../docs/knowledge-setup.md#旧库兼容与重建)。

`--mode`、`--candidates` 和 `--rrf-k` 都是可选的，仅覆盖本次运行；省略的字段沿用现有 knowledge 配置。默认配置为 `auto`、50、60：auto 在新混合库使用 hybrid，在已知旧向量库使用 dense。显式对旧向量库请求 bm25/hybrid 会计为查询失败，需另建混合库。候选数必须为 1–16384 的整数，RRF 常量必须为有限正数，复用日常配置校验。

最终 Top-K 固定取自数据集协议，不能用 benchmark 参数修改。hybrid 每路取 `max(candidates, Top-K)` 后融合；dense/bm25 只取该路 Top-K，候选数与 RRF 配置不参与单路排名。分数分别为 `cosine_similarity`、`bm25`、`rrf`；RRF 分数不代表相似度或概率。

`--replay` 和 `--check` 不连接模型或数据库，且拒绝上述三个检索覆盖参数。旧报告仍可回放；回放只按保存的命中重算评分，不补造旧报告缺失的策略信息，也不修改原报告。

输出写入数据集的 `runs/<时间_编号>/report.json` 和 `report.md`，每次保留独立结果。JSON 报告包含数据集指纹、库版本、库内实际保存的完整 `profile`、依赖版本、逐题原始返回和分组指标。`implementation_sha256` 覆盖检索、共享 retrieval、profile/model、配置校验及 CLI 等相关源码。

`retrieval_request` 保存配置覆盖后的请求模式、候选数、RRF 常量和数据集 Top-K；每题的 `retrieval` 直接保存服务返回的请求/实际模式、实际候选数、RRF 参数、两路数量及原有 profile hash、index、metric。单路的实际 `rrf_k` 为 null，未执行路的数量为 null；查询失败保留原错误，不推断实际检索参数。查询错误计入分母并单独列出；库在运行期间变化则拒绝产出可比较的报告。

Markdown 报告也显示请求参数和每题实际策略、候选数、RRF。实际 RRF 为 null 时显示“—”；旧报告或失败查询缺少的策略信息显示“未记录”。

三策略应使用同一个库和 revision、同一数据集指纹进行比较。独立 CLI 进程的模型准备和首题冷启动耗时不能直接当作稳定的热查询性能；本命令不额外预热或重试查询。

## 当前唯一题库

入口为 `pwsh -File eval/RAG-eval/run.ps1 -Check`，详见 [题库运行说明](README.md) 和 [完整设计](design.md)。已落地 S01–S08 共 300 题、20 会话及20状态流程定义；32 题 smoke 为开发子集。250 题使用实际检索协议，RGB 50 题使用逐题给定上下文。原件、冻结解析文本与证据位置已校验；尚未执行新题库真实检索、回答、会话或状态流程，不提供策略质量排名。

只导入 manifest 列出的 `corpus/`，严禁将题目、参考答案、评分规则、`text/` 或整个评测目录入库。不同题库指纹的历史结果不能参与本轮重放或汇总。已有数据集/CLI 契约不变，标准入口固定使用新题库。

## 数据集结构

- `dataset.json`：版本、固定协议和资料清单。
- `questions.json`：固定问题、参考答案、证据区间和分组。
- `corpus/`：原件快照；`text/`：与证据偏移对应的冻结解析文本。
- `rubrics.json`：逐题人工判分规则；`sessions.json` / `workflows.json`：会话与状态验收定义，当前未执行。
- 顶层 `source-lock.json` / `quality-review.json`：来源、改写及审核状态；逐题 provenance 保留上游信息。
- `runs/`：每次运行结果；改题、换资料或改评分规则应发布新版本。

`dataset.json` 的最小结构：

```json
{
  "id": "rag-scenarios-v1-original",
  "version": "1.0.0",
  "protocol": {"top_k": 5, "followup_query": "sessions_separate"},
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
| S01–S05、原创 S07–S08 | 单轮原问题 | 前 K 条是否覆盖指定版本的全部证据；不判回答与引用语义 |
| 原创 S06 | 保留原问题检索 | 部分回答只计已知证据；拒答/澄清题不参加证据召回分母 |
| RGB S06/S07 | 逐题受控上下文，尚未接入执行器 | 不纳入语料检索召回；单独评拒答及噪声条件下回答 |
| S09/S10 | 需真实会话/状态执行器 | 当前仅有定义，不在 benchmark 已执行分母内 |

每段证据的所有非空白文字都被返回位置覆盖才算命中，允许多个片段合并覆盖；仅忽略空白，不忽略标点、缺词或版本差异。资料身份使用稳定的 D 编号，不用可能重名的文件名。原有实验的严格区间指标保持不变。

“找齐题数”和“证据范围召回率”分别统计，不混称回答正确率。完整回答的事实、引用是否支持结论、缺失信息时是否拒答，需要另行人工验收；本命令不调用回答模型。已经观察过结果的题应作为回归集，不包装成盲测集。

当前静态题库由 260 道公开来源派生题与 40 道原创虚构题组成；来源修订见 `quality-review.json`。冻结表示版本稳定，不代表每题已由用户逐条审核；独立人工验收仍待完成。
