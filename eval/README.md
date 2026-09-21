# 评测集

| 目录 | 内容 | 本地校验 |
| --- | --- | --- |
| [coding-agent-eval](coding-agent-eval/README.md) | SWE-bench-Live 之前实际评测的固定 15 题 | `python eval/coding-agent-eval/check.py` |
| [RAG-eval](RAG-eval/README.md) | 300 道静态题、20 组会话、20 组状态流程 | `pwsh -File eval/RAG-eval/run.ps1 -Check` |

在项目根目录使用项目 Python 环境。这里收纳题集、来源、判分依据及必要使用说明；产品文档仍在 `docs/`。数据校验不调用模型，不代表系统评测通过。
