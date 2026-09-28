# RAG 评测入口

## 当前唯一题库

使用官方 **MultiHop-RAG**，原始 2,556 道题与完整 609 篇语料保持对齐。运行方式、来源版本和指标边界见 [README](README.md)。其他旧题集已经移除。

lite / medium / full 分别抽取 50 / 200 / 2,556 道原题；三个档位的语料完全相同。比较、推断、时间关系与信息不足按官方题型分别统计，不再拼接多个来源或设置自编情景。

```powershell
pwsh -File eval/RAG-eval/run.ps1 -Tier lite -Check
pwsh -File eval/RAG-eval/run.ps1 -Tier lite -Replay <原生检索报告.json>
```

旧 `codeplus.knowledge` 产品入口已移除；本脚本仍不提供在线检索。`run.ps1 -KbId` 明确报错。Check、Answers、Replay 仍按 R01 冻结协议运行；Replay 保留原生报告、evidence_recall 和官方检索评分，跨档位指纹会被拒绝。当前检索及普通 Agent 评测见 [正式评测入口](../../deployment/AgenticRAG/eval/README.md)，测试入口见 [环境与验证命令](../../deployment/AgenticRAG/docs/environment-command-matrix.md)。

离线回放不调用检索服务或回答模型；无答案题不计入官方检索指标。`-Answers` 仅对已有回答调用上游的词语重合评分，不应解释成语义正确率。旧引擎的 `evaluate` 接口已随清理移除；后续实验使用独立核心对应阶段的正式入口与冻结数据协议。
