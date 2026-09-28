# 评测集

| 目录 | 内容 | 本地校验 |
| --- | --- | --- |
| [coding-agent-eval](coding-agent-eval/README.md) | SWE-bench-Live 之前实际评测的固定 15 题 | `python eval/coding-agent-eval/check.py` |
| [RAG-eval](RAG-eval/README.md) | 官方 MultiHop-RAG；lite / medium / full：50 / 200 / 2,556 题，共用完整 609 篇语料 | `pwsh -File eval/RAG-eval/run.ps1 -Tier lite -Check` |
| [scifact-eval](scifact-eval/README.md) | 独立 BEIR–SciFact；完整 5,183 篇语料，官方 train 809 / test 300；本地 development 807 排除跨 split 重复文本 | 首次 `-Prepare`，之后 `pwsh -File eval/scifact-eval/run.ps1 -Check` |

在项目根目录使用项目 Python 环境。这里收纳题集、来源、判分依据及必要使用说明；产品文档仍在 `docs/`。数据校验不调用模型，不代表系统评测通过。

SciFact 使用自身目录下锁定的评分环境和独立检索运行环境。两套 RAG 评测的语料、索引状态、题目与结果分别保存；不要导入整个 `eval/`，也不要合并两组分数。SciFact 的纯检索入口与使用方式见其 README，运行结果以各自报告为准。
