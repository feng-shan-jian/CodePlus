# SWE-bench-Live 题集

从项目已有备份恢复的 **SWE-bench-Live 之前实际评测的固定 15 题**，题目来自 Lite 分区，revision 为 `b51a86422e10cfd403beb4773e5a2947953e36ec`。保留原正式题单顺序与对应记录；本目录不包含完整 300 题或 40 题候选池。

| 文件 | 用途 |
| --- | --- |
| `data/agent/tasks.jsonl` | 15 题的允许输入字段：`instance_id`、`repo`、`base_commit`、`problem_statement` |
| `data/evaluator/dataset.json` | 这 15 题的完整原始记录，含 gold patch、测试补丁及测试列表，仅供宿主评测器读取 |
| `selected_tasks.json` | 原有固定 15 题清单与当时的镜像 digest；不是本次重新筛选 |
| `source-lock.json` | 来源、版本、文件哈希和字段隔离约束 |
| `validation.json` | 本次数据完整性与字段隔离校验结果 |

```powershell
# 项目根目录运行，无需模型、Docker 或额外 Python 依赖。
.\.venv\Scripts\python.exe eval/coding-agent-eval/check.py
```

给 Agent 准备任务时，只使用 `data/agent/tasks.jsonl` 中对应任务的资料和干净仓库。`patch`、`test_patch`、`FAIL_TO_PASS`、`PASS_TO_PASS`、hints 和官方评测日志不可进入 Agent 工作区；不要挂载整个 `eval/`。输入字段白名单不等于已经完成容器隔离。

实际判分使用 [官方 SWE-bench-Live evaluator](https://github.com/microsoft/SWE-bench-Live/tree/ad79b850f15e33992e96f03f6e97f05ddf9aa0be)，数据来自 [固定版本的官方数据集](https://huggingface.co/datasets/SWE-bench-Live/SWE-bench-Live/tree/b51a86422e10cfd403beb4773e5a2947953e36ec)。沿用各来源许可，不从“可下载”推断额外使用授权。

本次只整理数据和校验入口，没有恢复旧模型执行器、历史成绩、轨迹、私有配置、源码快照或虚拟环境，也没有重跑模型或官方 Docker 评分。正式 15 题清单中的 `gold_pass` 是历史选择记录，不代表当前环境已复验。
