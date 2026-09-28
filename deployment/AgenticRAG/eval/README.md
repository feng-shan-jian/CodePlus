# 开发评测

评测复用生产检索和普通 CodePlus Agent。运行侧只加载原 query 与语料路径；参考答案、Gold、rubric 和评分器由独立离线进程读取。题目、顺序、划分和计分分母保持原协议。

## MultiHop

`dense_runner.py` 调用生产 capture/process/build/RetrievalSearch；`--context` 额外通过 SourceSession 测量单次工具正文与未发送请求。它不调用回答模型，也不把 prepared 记录视为真实交付。`score_dense.py` 与 `score_context.py` 复用原 source-span 评分，`retrieval_binding.py` 核对原始结果和官方评分所属同一输入。

`--rerank-candidates` 显式指定精排候选上限，默认 50；`--worker-idle-timeout-ms` 指定本地 worker 空闲时限，默认 1000 毫秒。默认值保持原评测配置；采用其他设置时须随结果保留实际参数。

长批次运行在问题之间检查模型会话的请求容量；旧请求实际结束后才关闭会话并创建新会话。每题记录 `model_owner_id`，已结束会话写入 `retired_worker_sessions`，不清零旧会话计数，也不自动重试失败题。

`run_agent.py` 在已导入知识库上执行 medium 200 题，使用基础 registry、权限和普通 Agent 循环。没有专属答案 JSON、修复或最终审批。所有题始终保留在 answers.json；未开始与失败均作为错误保留，付费接口返回 HTTP402 后停止新题。该入口衡量离线回答，不装配 CLI 的协作和记忆初始化；完整用户能力通过实际 CLI/TUI/Remote 另行验收。

```powershell
& $CorePython -B -X utf8 deployment/AgenticRAG/eval/run_agent.py --settings $Settings --state $State --output $Runs --mode auto
& $ScorePython -B -X utf8 eval/RAG-eval/score.py --task answers --input "$Runs/answers.json" --tier medium --output $Scores
```

`$Settings` 是实际知识配置，`$State` 包含已发布库的 kb_id；回答 provider 来自 CodePlus 配置。`--limit` 仅用于小规模预检，输出目录必须是新的。报告包括公开答案、普通运行状态、来源交付回执和请求结果，不记录隐藏推理。

官方答案指标是原 `upstream_word_overlap_accuracy`，不代表语义或引用正确率。中文、报告与续研行为由正式产品测试覆盖；已退役的强校验/修复评测流程及其专用补充输入、rubric 不再维护。

development 为 200 题，检索正例为 177 题；23 道 null query 保留在 200 题答案分母。acceptance 的 2,356 题历史暴露状态见[evaluation-protocol.md](../docs/evaluation-protocol.md)，不能称为未见测试。

## SciFact

807 题 development 与独立 300 题 test 使用[SciFact 正式入口](../../../eval/scifact-eval/README.md)。生产检索两遍运行保留双路、融合、精排与实际单次正文，离线按原文档及完整句子证据计分。评测窗口 5/10 不改变生产返回上限。
