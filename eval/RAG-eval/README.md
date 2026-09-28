# MultiHop-RAG 评测集

本目录只使用官方 [MultiHop-RAG](https://github.com/yixuantt/MultiHop-RAG)：**2,556 道原题、609 篇英文新闻语料、6,084 条原始证据**。问题、答案、题型与语料正文均保留上游内容。没有原创题、中文改写、额外干扰文档或来自其他题库的拼接。新增的 [BEIR–SciFact](../scifact-eval/README.md) 单独存放与评分，不加入本组语料、题目或索引。

这套数据集中评测跨文档检索与回答，按官方题型分别报告比较、推断、时间关系和信息不足。它不覆盖中文效果、PDF/OCR 解析、多轮会话、知识库增删改或 Agent 流程；这些功能继续由产品测试验证，不能从本题库分数推断其质量。

## 三个题量档位

| 档位 | 比较 | 推断 | 时间关系 | 信息不足 | 总题数 | 检索计分题数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| lite | 17 | 16 | 11 | 6 | **50** | 44 |
| medium | 67 | 64 | 46 | 23 | **200** | 177 |
| full | 856 | 816 | 583 | 301 | **2,556** | 2,255 |

三个档位始终使用同一份完整 **609 篇语料**。lite ⊂ medium ⊂ full；按官方题型比例分配名额，以固定种子和 SHA256 排序选题，再恢复上游顺序。具体题号固定于 [tiers.json](tiers.json)，不能根据系统表现换题。

lite 用于日常回归，medium 用于方案对照，full 用于完整报告。它们是本地抽样档位，不是官方发布的 lite/medium 版本，也不是训练/验证/测试切分。小档位减少查询和评分耗时，初次建库开销不变；不可将小样本分数冒充官方全量结果。已用来调参的题也不能再宣称为未见测试题。

## 文件与来源

- [upstream/MultiHopRAG.json](upstream/MultiHopRAG.json)、[upstream/corpus.json](upstream/corpus.json)：官方原始 JSON，字节未改。
- `corpus/`：609 个 Markdown 导入文件，每篇附原有标题、来源、日期、URL；正文逐字保留。**仅导入此目录**，不要将题目、答案或 upstream 文件入库。
- [dataset.json](dataset.json)、[questions.json](questions.json)：CodePlus 所需的无损适配。题号 `MH-0001` 对应上游第 1 条；`upstream_index` 从 0 开始。gold 为原始 fact 在对应文章中的精确字符位置，不删证据、不补答案。
- [source-lock.json](source-lock.json)、[NOTICE.md](NOTICE.md)：固定版本、下载链接、SHA256 与署名。
- `upstream/*_evaluate.py`：官方评分代码原样冻结；[score.py](score.py) 校验题目身份后调用其中的评分函数。
- [prepare.py](prepare.py)：可重复生成适配文件；`--check` 只比较字节。[check.py](check.py) 同时校验来源、全部证据及三档题号。

当前版本完全替代旧 RAG 题库。旧题库、数据副本及专用说明已移除，旧报告不能与此版本直接比较；应新建仅包含这 609 篇语料的评测库。SWE-bench 的固定 15 题仍单独位于 `../coding-agent-eval/`。

## 使用

在项目根目录运行，先安装 Windows 环境 `uv sync --locked`。

```powershell
# 离线完整性检查，不加载模型、不连接 Milvus
pwsh -File eval/RAG-eval/run.ps1 -Tier lite -Check
pwsh -File eval/RAG-eval/run.ps1 -Tier medium -Check
pwsh -File eval/RAG-eval/run.ps1 -Tier full -Check
```

旧检索引擎及其宿主入口已移除；本历史评测脚本仍不提供在线入口。`-KbId`、`-Mode` 和 `-ManagedLocal` 保留参数解析，但在线调用会在访问旧模块或生成报告前明确报错。当前检索及普通 Agent 评测见 [正式评测入口](../../deployment/AgenticRAG/eval/README.md)，测试入口见 [环境与验证命令](../../deployment/AgenticRAG/docs/environment-command-matrix.md)。

`-Dataset all` 和 `-Dataset multihop` 指向同一数据集；默认 `-Tier full`。Check、Answers、Replay 保留 R01 离线协议；输入路径相对调用者目录解析，Replay 仍写入原生 `report.json`、`report.md` 和 `upstream-retrieval.json`，保留 evidence_recall、失败题分母和非零失败退出码。

```powershell
# 只能按原报告的同一档位重算，不连接模型或数据库
pwsh -File eval/RAG-eval/run.ps1 -Tier lite -Replay eval/RAG-eval/runs/<run>/report.json

# 对已有的回答输出进行离线评分，不会自动生成回答
pwsh -File eval/RAG-eval/run.ps1 -Tier lite -Answers <answers.json>
```

`answers.json` 是 JSON 数组，每项包含原题 `query` 和字符串 `model_answer`，可附 `id`、`status`。必须完整覆盖所选档位且无重复；请求失败保留该题并写 `status: "error", model_answer: ""`，不得漏题。输入的 `gold_answer`、`question_type` 不参与评分，以冻结题库为准。

## 分数的含义

| 输出 | 实际衡量内容 | 边界 |
| --- | --- | --- |
| `report.json` 的 evidence_recall | CodePlus 原文范围完整覆盖 | 本地诊断指标，不是官方 Hits/MAP |
| `upstream-retrieval.json` | 官方函数的 Hits@4、Hits@10、MAP@10、MRR@10 | Top-10 文本匹配原始 fact；按上游排除 null_query |
| `-Answers` 输出 | 官方函数的 `upstream_word_overlap_accuracy`，含全部四类题 | 只要预测与答案有一个小写空白分词相同就算命中；不是语义正确率，也不验证引用 |

保留上游 MAP 公式的原始实现，不替换为另一种 MAP 定义。这里对齐的是官方数据和固定版本评分函数，CodePlus 的分块、嵌入与检索配置仍可能不同于论文，因此不能声称已复现论文分数。答案指标过于宽松，实际回答质量仍需额外人工审查；当前未添加新的自动评委或自编题。

[validation.json](validation.json) 记录离线校验结果。**本离线入口不执行真实检索、答案生成或模型效果评测**，离线完整性通过不代表 RAG 效果达标。
