# BEIR–SciFact 独立检索评测

这组评测检查相关文档能否被召回、融合和精排到前五或前十。它采用官方 BEIR SciFact 数据，包含 **5,183 篇科学文献摘要、809 道 train 查询和 300 道 test 查询**。train 有 919 条文档相关性标注，test 有 339 条。查询为科学断言，相关文档可提供支持或反驳证据；文档指标不代表答案生成质量；句子证据覆盖由独立的既有标注评分器计算。

MultiHop-RAG 继续使用 [`../RAG-eval/`](../RAG-eval/README.md)。两组不合并题目、不合并检索库、不合并分数。SciFact 的 train 和 test 按官方划分保留，共用本组完整文档库；同组共用文档是标准检索协议，查询 ID 不重叠。

官方 train 中的 `871`、`1291` 与 test 存在重复查询文本，因此另建 **development：807 查询、917 条相关性标注**，作为调参输入。排除规则固定为 NFKC 规范化、大小写折叠、合并空白后与 test 文本相同，不使用检索成绩选题；原始 train 809 和 test 300 均不删改。官方 train 仅供诊断和对齐上游，不用来挑选本地参数；test 留作验收。此规则排除了已知文本重复，不证明语义或预训练数据完全无重叠。反复用 test 选参数后，应标记为已参与调参，不能再称为未见测试。

## 文件和隔离边界

| 内容 | SciFact 专用位置 | 规则 |
| --- | --- | --- |
| 官方数据 | `upstream/corpus.jsonl`、`queries.jsonl`、`qrels/*.tsv` | 原始字节保留，SHA256 固定于 `source-lock.json`；qrels 只供准备和离线评分进程读取 |
| 可导入文档 | `runtime/corpus/SF-<官方文档ID>.md` | 只含原始标题和正文；始终导入完整 5,183 篇，不根据相关性挑选文档 |
| 运行输入 | `runtime/development.json`、`runtime/train.json`、`runtime/test.json` | 三个选择分别加载；只有查询、文档路径、身份和校验值，没有参考答案和相关性标注 |
| 索引状态 | `.state/<index-name>/data` | `bind_storage()` 固定 namespace 为 `beir_scifact_v1`，绑定数据指纹；拒绝无绑定或另一语料的状态 |
| 结果 | `runs/<development或train或test>/<run-name>/` | 数据集、split、完整题序和语料指纹必须一致；同名运行拒绝覆盖 |

不要导入本目录、`eval/`、`upstream/` 或整个 `runtime/`：只能导入运行清单列出的 `runtime/corpus/` 文档。模型权重可以共用；索引、存储状态和查询结果缓存必须按本组身份隔离。

运行输入加载器会检查完整语料清单和每个文件的 SHA256。检索适配器先安装 `protect_runtime_reads(split)`，再调用 `load_runtime_inputs(split)` 和 `bind_storage(...)`；读保护会阻止 Python 进程读取 qrels、历史评分、另一 split 的查询和 MultiHop 目录。它是防误读措施，不是操作系统沙箱。

本目录提供数据、运行输入边界、生产检索适配器和离线评分入口。现有 MultiHop `dense_runner.py` 仍绑定 MultiHop 输入；SciFact 使用独立的 `retrieval_runner.py`，调用生产 capture/process/build、RetrievalSearch 和原有精排方法，没有复制检索算法，也不调用回答模型。运行是否完成以具体运行的 `completion.json` 和评分报告为准。

## 准备与检查

在仓库根目录使用独立 Windows Python 3.12 环境：

```powershell
uv sync --project eval/scifact-eval --locked
pwsh -File eval/scifact-eval/run.ps1 -Prepare
pwsh -File eval/scifact-eval/run.ps1 -Check

# 查询侧只读检查，不打开 qrels、不运行模型
& eval/scifact-eval/.venv/Scripts/python.exe -B eval/scifact-eval/runtime_inputs.py --split development
& eval/scifact-eval/.venv/Scripts/python.exe -B eval/scifact-eval/runtime_inputs.py --split test

# 正式隔离与计分回归测试
& eval/scifact-eval/.venv/Scripts/python.exe -B -m pytest eval/scifact-eval/tests -q -p no:cacheprovider
```

原始数据随评测目录保留；生成的 `runtime/`、本地环境、索引和运行结果不入 Git。`-Prepare` 可重复执行，但拒绝覆盖不同内容或接纳外来文件。来源见 [NOTICE.md](NOTICE.md)，数据固定值见 [dataset.json](dataset.json)。

## 纯检索运行

检索环境使用冻结的 `codeplus-agentic-rag` wheel 和匹配的本地 CUDA 依赖，与上述 Python 3.12 评分环境分开。`$RuntimePython` 指向该环境的 Python；worker 使用同一个解释器，模型只从已校验的本地缓存加载。数据目录固定在本组 `.state/<index-name>/`，即使共用 Milvus 服务，也使用独立 collection。不得传入 MultiHop 的 state 或知识库 ID。

该环境还需安装同一修订的 CodePlus 宿主，正文选择使用的 SourceTextMeter 由宿主适配器导入。

```powershell
# 先将 $RuntimePython 和 $Models 设为本机检索环境与模型缓存的绝对路径。
$Endpoint = 'http://127.0.0.1:19556'
$IndexName = 'scifact-baseline'
$DevelopmentRun = 'development-01'
$TestRun = 'test-01'
$ScorePython = (Resolve-Path 'eval/scifact-eval/.venv/Scripts/python.exe').Path
& $RuntimePython -B -X utf8 eval/scifact-eval/retrieval_runner.py build --index-name $IndexName --endpoint $Endpoint --model-cache $Models
```

选定生产配置保持 512/64 分块和原模型标题/query 指令，Dense/BM25 各最多 50、RRF `k=10`、精排候选最多 24、`min_score=0.001`。由现有 `assemble_configuration` 合并[选定配置](../../deployment/AgenticRAG/docs/retrieval-selected.json)。先完整精排及响应校验，再过滤低分结果，再截取评测窗口；不补齐，允许空结果。5/10 是评分窗口，不是生产固定返回数。

第一遍完成召回和融合，第二遍对冻结候选调用生产 `_rerank`，保留实际输入、批次、完整评分和终态。最后将过滤后完整排名交给生产 SourceSession，记录单次 `context_chunks/context_tokens`、裁切和去重后的实际正文。此阶段不再次调用模型，也不创建真实答案交付回执。首题与生产公开 search 对齐。

query 记录索引的原始源码/config 身份和本次源码/config 身份；生产读取继续验证版本、成员和编码兼容。运行前须按上节命令准备本组索引，或指定仍有效的已有索引；旧运行目录不随源码分发。运行中源码变化会使结果不可验收。

```powershell
# 使用已准备的 SciFact 专属索引；先执行全部 development，再执行固定方案的一次 test
& $RuntimePython -B -X utf8 eval/scifact-eval/retrieval_runner.py query --index-name $IndexName --endpoint $Endpoint --model-cache $Models --split development --run-name $DevelopmentRun
& $ScorePython -B -X utf8 eval/scifact-eval/summarize_retrieval.py --source "eval/scifact-eval/.state/$IndexName/retrieval/$DevelopmentRun" --run-name $DevelopmentRun
& $ScorePython -B -X utf8 eval/scifact-eval/score_evidence.py score --run-name $DevelopmentRun

& $RuntimePython -B -X utf8 eval/scifact-eval/retrieval_runner.py query --index-name $IndexName --endpoint $Endpoint --model-cache $Models --split test --run-name $TestRun
& $ScorePython -B -X utf8 eval/scifact-eval/summarize_retrieval.py --source "eval/scifact-eval/.state/$IndexName/retrieval/$TestRun" --run-name $TestRun
```

`$Endpoint`、`$IndexName` 和 `$Models` 必须对应已准备的本组资源；`$DevelopmentRun` 与 `$TestRun` 分别设置为新的运行名称。`$ScorePython` 使用下述 Python 3.12 评分环境。运行失败不自动重试，同名输出不覆盖；失败题保留在全部 split 分母中。

## development 对照与证据覆盖

development 中 505 道题有句子标注；其余 302 题不进入完整证据指标分母。检索运行结果以对应 completion/summary 为准，最终答案质量另测。

`score_evidence.py` 保持官方完整可替代证据组规则：最终窗口中同文档区间并集完整包含任意一个证据组的全部句子，才计该文档覆盖；半句和区间空隙不计入。文档 Recall 与完整证据覆盖分别报告。它不预测支持/反驳标签，不是官方 SciFact 分类成绩。

原始标注归档、哈希和既有偏移映射位于 `runs/annotations/scifact-20260927/`，检索运行侧禁止读取。`score_evidence.py prepare` 仅核验这些冻结输入，不再依赖已退役的算法实验库存。`score` 使用新结果自身保存的原文坐标，对融合、精排和实际工具正文分别计分；可选 `--baseline` 保留与既有选定 RRF10 的逐题配对差值和相同分组 bootstrap。

`runs/annotations/` 是纳入版本管理的正式评分输入；`runtime/`、题目、Gold、划分和上游数据也属于评测集。`.state/` 与 `runs/development/`、`runs/test/` 保存本地索引状态和运行产物，清理前确认对应运行已结束，并保留仍需复用的索引与对照结果。

## 检索输出与评分

每份输出复制所选运行输入的 `dataset_id`、`dataset_fingerprint`、`namespace`、`corpus_fingerprint`、`split` 五个字段，再提供 `protocol` 和 `records`：

```json
{
  "protocol": {"unit": "document", "top_k": 10},
  "records": [
    {
      "id": "所选运行输入中的原始查询ID",
      "query": "未经改写的原始查询",
      "status": "ok",
      "hits": [{"doc_id": "官方文档ID", "score": 0.9}]
    }
  ]
}
```

这是字段示意，不是可计分的完整运行。必须按清单顺序包含所选 split 的全部查询；失败项保留 `status: "error", hits: []`，不删题、不缩小分母。`hits` 按分数降序排列，分数相同则保留提交顺序；`top_k` 至少为 10。文档 ID 使用 BEIR 原 ID；导入文件名 `SF-<ID>.md` 到原 ID 的映射来自运行清单，不使用临时 UUID 计分。

如果输出单位是块，设置 `unit: "chunk"`，每个块仍携带所属原文档的 `doc_id`。计分时保留同文档首次出现，避免多个块重复算中；不把该文档所有块自动当作正确证据。只返回十个块时，去重后可能不足十篇文档；本评分器不会擅自补齐候选。报告记录删除的重复块数量。

```powershell
pwsh -File eval/scifact-eval/run.ps1 -Score <retrieval.json> -RunName dense-baseline
```

评分使用锁定的 **ir-measures 0.4.3 / pytrec-eval-terrier**，不手写 nDCG、MRR 和 Recall 算法。输出 `Success@5/10`（Hit@5/10）、`R@5/10`、`RR@10`、`nDCG@10`、`P@5/10`，附逐题分数和实际依赖版本。失败请求按零分留在全 split 分母中，结果标记 `complete_with_errors` 并返回非零退出码。结果身份不符或缺题直接拒绝计分。

建议固定语料与候选预算，对比 BM25、Dense、融合和精排四个阶段，并将 development/test 的每次运行单独保存。默认不会调用回答模型或付费评委；文档相关性指标也不能代表最终答案正确率。
