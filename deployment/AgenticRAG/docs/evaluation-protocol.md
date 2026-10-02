# MultiHop 评测协议

使用官方 609 篇语料、2,556 道题和 6,084 条证据。各档位共用完整语料，lite 嵌套于 medium：

| 档位 | 题数 | 检索计分题数 |
| --- | ---: | ---: |
| lite | 50 | 44 |
| medium | 200 | 177 |
| full | 2,556 | 2,255 |

[development](../eval/development-ids.json) 为 medium 的 200 题，[acceptance](../eval/acceptance-ids.json) 为其余 2,356 题。acceptance 的历史使用情况不完整，不标作未见测试集。固定题号、指纹和划分见 [evaluation-protocol.json](../eval/evaluation-protocol.json)。

## 输入与评分

运行只加载 [runtime-inputs.json](../eval/runtime-inputs.json) 中的 ID、query 和语料路径；答案、gold 和题型由独立评分进程读取。运行结果另存目录，题库保持固定。

原生 evidence_recall 衡量原文区间覆盖；官方检索使用 Top-10。失败题计零并保留在分母，null_query 从检索计分中排除、在答案评分中保留。

`upstream_word_overlap_accuracy` 只判断预测与参考答案是否有小写空白分词重合，不代表语义正确率。报告分别列出检索、原文覆盖和答案指标。

```powershell
.venv/Scripts/python.exe -B eval/RAG-eval/check.py --tier full
pwsh -File eval/RAG-eval/run.ps1 -Tier lite -Replay <report.json>
pwsh -File eval/RAG-eval/run.ps1 -Tier lite -Answers <answers.json>
```

上述命令用于检查和离线评分。实际检索与 Agent 执行见[开发评测](../eval/README.md)，SciFact 使用[独立题库](../../../eval/scifact-eval/README.md)。
