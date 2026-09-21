# RAG 分场景评测

CodePlus 当前唯一维护的 RAG 质量题库：**300 道静态题＋20 组三轮会话＋20 组状态流程**。其中 260 道静态题来自公开数据集，40 道为有针对性的原创虚构题。32 题 smoke 是其中的开发子集，不额外累计题量。完整设计见 [评测设计](design.md)。

功能单元测试使用各自的最小夹具，不充当质量题库。检索 CLI 的详细参数见 [benchmark 说明](benchmark.md)。

## 已落地内容

| 数据分区 | 题数 | 资料数 | 协议 |
| --- | ---: | ---: | --- |
| original | 40 | 22 | 原创 MD/PDF/DOCX；实际检索 |
| crud | 90 | 2,020 | 固定新闻池与精确 QA 原件版本；实际检索 |
| multihop | 100 | 609 | 完整官方语料；80 多文档题＋20 引用派生题；实际检索 |
| openrag | 20 | 20 | 20 份原始论文 PDF；实际检索，文本题 |
| rgb | 50 | 230 | 逐题给定上下文；20 拒答、30 噪声题 |

静态题按家族划分开发 60、保留验收 240。会话与流程分别按 4/16 划分。保留集答案仅供评测器核对；禁止把题目、答案、gold、rubrics、设计文档或冻结 text 入库。仅导入所选分区的 `corpus/`。公开题可能被预训练见过，本地保留集不等同于模型从未见过的秘密盲测。

## 统一入口

从项目根目录使用 PowerShell 7；需要项目 Windows 环境 `uv sync --locked --extra knowledge`。默认检查完整题库，不启动模型或数据库：

```powershell
pwsh -File eval/RAG-eval/run.ps1 -Check
pwsh -File eval/RAG-eval/run.ps1 -Dataset smoke -Check
```

检索前为每个分区创建独立库，只导入对应 corpus。例如原创分区：

```text
/knowledge create "RAG scenarios v1 original"
/knowledge import "D:\CodePlus\eval\RAG-eval\full\original\corpus"
/knowledge status
```

其他安装位置替换绝对路径，核对文档数与 READY 状态。使用新库 ID，对同一库、revision、题库指纹及 Top-5 比较三种策略：

```powershell
pwsh -File eval/RAG-eval/run.ps1 -Dataset original -KbId <新库ID> -Mode dense -ManagedLocal
pwsh -File eval/RAG-eval/run.ps1 -Dataset original -KbId <新库ID> -Mode bm25 -ManagedLocal
pwsh -File eval/RAG-eval/run.ps1 -Dataset original -KbId <新库ID> -Mode hybrid -ManagedLocal
pwsh -File eval/RAG-eval/run.ps1 -Dataset original -Replay <report.json绝对路径>
```

远程 Milvus 省略 `-ManagedLocal`。其他可检索分区分别指定 `crud`、`multihop`、`openrag` 或 `smoke` 及其独立库 ID。hybrid 固定 N=50/RRF=60，报告落在对应分区的 `runs/`。现有 benchmark 继续强制库内只有 manifest 指定的资料版本，并拒绝回放不同题库指纹的报告。不恢复旧评测 ID、不自动改用户绑定。

RGB 必须逐题只提供 `context_sets` 指定的资料；入口拒绝对 RGB 整池执行检索，避免拒答题接触被刻意撤去的正例。噪声条件是 1 正例配 0/1/4 个负例，不声称准确的 token 比例。此协议、真实回答裁判、会话及状态流程的自动执行器尚未接入；题目和验收步骤已齐备，状态均为 `not_executed`。

## 质量与判分

- `dataset.json`、`questions.json`：原件/冻结解析哈希、原始题目 ID、改写说明、答案和可定位证据。偏移为 Unicode 字符位置，左闭右开。
- `rubrics.json`：预期行为和引用要求，证据引用对应同题 `gold[].id`。允许有依据的等价答案与其他有效来源，须经复核。
- `sessions.json`：20 个独立会话、60 轮输入，配套资料位于 sessions/corpus；真实保留历史，不以单轮改写替代。
- `workflows.json`：20 个状态流程，包含原件、替换资料、操作、断言及仅清理自建库的范围；故障点需由集成执行器绑定。
- `source-lock.json`、`NOTICE.md`：上游版本、许可及来源；`selection-exclusions.json` 记录候选排除。
- `quality-review.json`：来源语义复核、修订数量和典型错误记录；独立人工复审仍待完成。
- `validation.json`：实际离线校验结果，包含分区及全套规则/流程指纹；不代表系统质量通过。

已进行来源对照、精简无关证据、剔除日期/范围错误候选、精确与近重复筛选，以及文档家族和标准证据原件跨集检查。论文证据由官方相关章节核对后映射到原始 PDF 的实际解析文本；复杂公式/图表题未收入。多文档题保留上游题目编号、原问题和 2–4 个来源；62 题修正问法或答案，改为有证据支撑的跨文档比较、归纳或推理。RGB 另修订 15 题范围/答案并更换 8 段上下文，避免负例泄漏或关系支撑不足。独立人工审核仍待完成，数据包状态为 `curated_candidate`。

检索只报告证据覆盖、找齐题数与错误率；250 道检索题中 247 道有标准证据，3 道拒答/澄清题不进入召回分母。RGB 50 题另评，其中 20 道拒答；全套共 23 道无标准证据题。回答正确、部分回答、拒答、澄清、冲突披露与引用支持必须另评。RGB 段落证据和句子证据分开说明，不把整段返回率当作语义引用正确率。

未执行真实检索、回答、连续会话或状态流程，因此没有策略排名和通过率。更改资料、问题、解析结果或判分规则须更新版本及指纹。`evaluate` 保留原索引实验接口，其旧 Markdown 格式不兼容该混合文件题库，旧 frozen 文件不再用于当前质量成绩。
