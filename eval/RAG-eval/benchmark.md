# 旧知识库回归评测的历史说明

旧 `codeplus.knowledge` 引擎、`benchmark/evaluate` 产品入口及可选依赖已移除。此文件保留旧数据结构和评分边界的历史叙述，不再提供旧引擎运行命令。当前离线评测接缝见 [R01](../../deployment/AgenticRAG/docs/implementation-records/R01.md)，独立核心进度见 [AgenticRAG](../../deployment/AgenticRAG/README.md)；宿主 Agent 接入尚未完成。

## 历史题库

入口为 `pwsh -File eval/RAG-eval/run.ps1 -Check`，详见 [题库运行说明](README.md) 和 [完整设计](design.md)。已落地 S01–S08 共 300 题、20 会话及20状态流程定义；32 题 smoke 为开发子集。250 题使用实际检索协议，RGB 50 题使用逐题给定上下文。原件、冻结解析文本与证据位置已校验；尚未执行新题库真实检索、回答、会话或状态流程，不提供策略质量排名。

只导入 manifest 列出的 `corpus/`，严禁将题目、参考答案、评分规则、`text/` 或整个评测目录入库。不同题库指纹的历史结果不能参与本轮重放或汇总。历史题库描述不构成当前产品 CLI 承诺；在线入口随旧引擎清理移除。

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
