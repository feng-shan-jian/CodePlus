# R01 评测数据与离线评分协议

本协议冻结现有 MultiHop-RAG 评测输入，解耦离线加载和评分，并定义未来运行侧的输入边界。它不证明新核心、检索质量、真实 Agent、GPU 或 Milvus 已通过验收。日常产品命令仍按 D29 复用 CodePlus；以下均为内部评测材料。

## 冻结输入与可复现范围

基线为 R00 本地提交 `7ef7ee6c26e5a104da212a6b7788d9b884ef04fa` **加上** [R00 输入清单](implementation-records/R00-input-fingerprints.json) 与 [继承保护清单](implementation-records/R00-protected-inputs.json) 标识的工作区文件。整套 MultiHop 替换是继承未提交输入，R01 不取得其所有权；仅从 Git HEAD 检出不能宣称复现完整评测数据。

官方数据保持 609 篇文档、2,556 题、6,084 条证据。corpus、dataset.json、questions.json（含答案、gold 与顺序）、tiers.json、source-lock.json、prepare.py、upstream 和 validation.json 不改。逐文件 SHA-256 对照、路径集合与三档检查见 [R01 证据](implementation-records/R01-evidence.json)。

| 档位 | 问题数 | 检索分母 | 数据/题集 SHA-256 |
| --- | ---: | ---: | --- |
| lite | 50 | 44 | `cdcbd3c33fef2df6c5406535413df20fd31157a1052d9b05c46f3059044ba9ed` |
| medium | 200 | 177 | `f80fc4033be6625b19da2af9529cf925d147e9ad62c95b943df2c3d08ec2e898` |
| full | 2,556 | 2,255 | `c9cf1a90f31d7944cfa24e3ff14db2ae3fa8b875a1a1c9397f6f6f2221bc73d9` |

`eval/RAG-eval/dataset_io.py` 的 `load_dataset` 与 `fingerprint` 取自原标准库逻辑，返回结构、字节校验、证据范围校验、JSON 的 Unicode/排序/紧凑编码及异常语义不变。check.py 保留原函数/CLI，只替换两个 import；score.py 与冻结 upstream 保持原字节。

内部固定 JSON 的字节指纹由 [eval/.gitattributes](../eval/.gitattributes) 中的 `*.json -text` 保证：Git 不对这些文件执行换行转换，即使 Windows 配置 `core.autocrlf=true`，也不能将 LF 转成 CRLF 后破坏运行清单的 raw SHA-256。R01 首次交付漏设该规则，Leader 独立验收发现后补齐；未修改 JSON 数据本身。真实 Git checkout 的字节一致性由 Leader 独立复验。

## 开发、验收与历史暴露

- [development-ids.json](../eval/development-ids.json)：medium 原序的 200 个 ID。
- [acceptance-ids.json](../eval/acceptance-ids.json)：按 full 原序扣除 medium，2,356 个 ID。
- 两者互斥且并集为 full；lite 50 嵌套于 medium。每档均使用全部 609 篇语料。
- [evaluation-protocol.json](../eval/evaluation-protocol.json) 保存规则、指纹、分母和暴露声明；它是评分/实验准备材料，不传给 Agent。

已知：继承适配器及全量证据已被准备/校验，R00 执行过三档完整性检查，R01 的准备、评分和测试读取全量问题、参考答案、gold，并核对全部运行 query。未知：这些题此前逐题参与模型执行、调参、人工查看或训练的历史未从现有记录中确立。完整性检查不等于模型执行；未知也不等于未暴露。因此验收子集**不能称为未见测试集**，将来 full 报告应分别列出开发/验收部分并继续记录实际使用。

## 运行侧严格白名单

准备阶段已导出 [runtime-inputs.json](../eval/runtime-inputs.json)。其内容仅包含：

```json
{
  "questions": [{"id": "MH-0001", "query": "原始问题"}],
  "corpus_paths": ["corpus/D0001.md"]
}
```

[runtime_inputs.py](../eval/runtime_inputs.py) 只读取这份固定、带字节 SHA-256 校验的 query-only 文件。它不导入评分器、宿主或旧 knowledge，不读取 dataset/questions/tiers/source-lock/upstream。`load_runtime_inputs(suite_root, question_ids=None)` 返回同样的两个键；题目严格只有字符串 ID/query，导入路径转为 corpus 内已存在文件的绝对路径。所有额外字段、字典/列表嵌套、重复 ID/路径、未知选择、评分文件路径和路径重定向均拒绝。调用者传 ID 子集时仍保持官方题序。

运行侧不是先调用全量 `load_dataset` 再删 gold。实验准备/评分进程才能读取参考答案、gold、supporting_context、answerable、题型标签；它把固定 ID 列表交给运行加载器。Agent 与 query 生成只取当前问题的 query（ID 供结果关联）；导入器只取 corpus_paths。类型标签、答案和 gold 不进入运行 payload。语料完整性检查由独立准备阶段完成，不把校验报告整包当成模型提示。

此处建立的是输入边界，尚未装配真实 runner。正式子进程测试禁止所有 `codeplus` 导入，并在加载运行输入前用 Python audit hook 禁止打开整个 `eval/RAG-eval/` 下的文件，证明运行加载器不会偷读评分输入。此测试不是对尚未实现的 Agent 权限隔离的证明。

## 原命令、评分语义与限制

以下命令仍在 `D:/CodePlus` 的 Windows `.venv` 下执行：

```powershell
.venv/Scripts/python.exe -B eval/RAG-eval/check.py --tier full
.venv/Scripts/python.exe -B eval/RAG-eval/score.py --task retrieval --input <report.json> --tier lite
.venv/Scripts/python.exe -B eval/RAG-eval/score.py --task answers --input <answers.json> --tier lite
pwsh -NoProfile -File eval/RAG-eval/run.ps1 -Tier full -Check
pwsh -NoProfile -File eval/RAG-eval/run.ps1 -Tier lite -Replay <report.json>
pwsh -NoProfile -File eval/RAG-eval/run.ps1 -Tier lite -Answers <answers.json>
```

`run.ps1 -Replay` 的唯一路由接点改为中立 `replay.py`，保留 `--dataset/--replay/--question-ids` 装载契约、原生 coverage/latency 算法、报告路径和 Markdown、retrieval 元数据、replayed_from、summary、status 以及有请求失败时的退出码 1。原生 source-interval 覆盖报告与官方评分是两个口径；replay.py 只保留前者，不复制 upstream 官方评分器。原 score.py 继续追加官方评分。

官方检索仍强制 Top-K=10、精确 ID/query/题序和数据指纹。失败请求计零并保留在对应分母；null_query 仅从检索分母排除，在答案评分仍保留。官方答案指标是小写空白分词的任一词重合 `upstream_word_overlap_accuracy`，不能称为语义正确率、引用正确率或拒答正确率。合成报告只验证这些协议，不是模型质量结果。

`run.ps1 -KbId` 仍调用旧 `codeplus.knowledge benchmark`；新核心和真实 Agent 未连接，后续由 R10/R17 接管。直接旧产品 CLI 的 `python -m codeplus.knowledge ...` 不在本次解耦范围。未运行旧在线检索来替代新核心验收。

## 正式验证与后续修改边界

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.venv/Scripts/python.exe -B -m pytest deployment/AgenticRAG/tests/test_evaluation_protocol.py tests/test_multihop_evaluation.py -q -p no:cacheprovider
```

正式测试覆盖原三档指纹、全部证据、ID 嵌套与顺序、严格字段/路径白名单、无评分文件读取的运行子进程、禁旧模块的原命令、错误/空答案分母，以及 Replay 原生报告兼容。子进程均设 60 秒上限。测试使用合成输入，成功/失败报告都保留计分语义，生成的 runs 报告在测试结束后清理。

R00 的文档与脚本字节对照也是 R01 的继承归属检查；后续 R10/R17 若获授权更改 README、benchmark.md、执行器等路径，应按新的任务白名单更新归属测试和证据。不得借此修改 corpus、原题/答案/gold、tiers、upstream 的冻结口径。当前的来源映射、分母与评分器变化必须另行明确批准和版本化。
