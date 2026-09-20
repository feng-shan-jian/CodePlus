# 向量 + BM25 多路召回执行计划

状态：执行中，H01 代码 review 通过；每阶段由 leader 审查、验证通过后本地提交，再启动下一阶段。基线：`ef6bd17`，2026-09-20。初始工作区仅本计划未跟踪，暂存区为空；保留他人已有修改。

目标：将现有实验中的 BM25 和 RRF 接入日常知识库搜索，并用真实资料判断效果。分块、Embedding、追问处理、文档范围检索、精排模型和回答模型不在本轮范围。

## 1. 统一实现约定

### 1.1 用户能获得什么

同一个问题分别用向量检索和关键词检索寻找片段，合并去重后返回 Top-K。向量负责语义相似，BM25 负责正文关键词匹配。文件名不在当前检索正文中；本轮不承诺解决仅靠文件名定位资料的问题。

```text
问题 → 向量召回前 N 条 ─┐
问题 → BM25 召回前 N 条 ├→ RRF 合并、按 chunk_id 去重 → 前 K 条 → 现有 Agent
                       ┘
```

### 1.2 配置和模式

在现有 `knowledge` 配置内增加以下字段，复用现有配置读取、校验和多层合并方式：

```yaml
knowledge:
  retrieval_mode: auto
  retrieval_candidates: 50
  rrf_k: 60
  top_k: 5
```

| 字段 | 约定 |
| --- | --- |
| `retrieval_mode` | `auto/dense/bm25/hybrid`；默认 `auto` |
| `retrieval_candidates` | 每路候选数量，整数 1–16384；混合查询实际取 `max(配置值, 本次 top_k)` |
| `rrf_k` | 有限正数，默认 60；不得接受布尔值、NaN 或无穷值 |
| `top_k` | 保持现有含义和边界，表示最终返回数量 |

- `auto`：新混合库用 hybrid，已知旧向量库用 dense。返回元信息必须显示实际模式。
- `dense/bm25`：仅执行指定一路，按该路原始排名返回 Top-K；纯 BM25 搜索不调用 `encode_query`。
- `hybrid`：两路各取 N，执行等权 RRF，最后截取 Top-K。
- 在旧库显式请求 BM25/hybrid 时，明确报“不支持，需另建混合库”；不得静默改用 dense。
- 多路不要求线程并发。先保持现有同步服务和锁范围，避免本轮引入新的并发调度。

### 1.3 集合、版本与兼容

- 新库增加 Milvus 内建 BM25 函数和稀疏索引，复用现有实验配置：jieba search 模式、`hmm=false`、lowercase；BM25 `k1=1.2`、`b=0.75`。
- 新 profile 增加明确的索引版本与完整分词/索引配置；集合与 SQLite 继续绑定同一个 profile hash。
- 查询模式、N、RRF 参数不进入不可变 profile，也不改变文档 generation 或 chunk ID；调这些参数不要求重建库。
- 旧 profile 缺少新索引字段时，只识别为已知旧向量结构。仍验证完整旧 profile hash 和其余模型、解析、分块配置，不放宽未知版本或损坏配置校验。
- 保留旧库原先允许的查询、导入更新、删除和 pending 恢复能力。已有 `structure-offsets-v1` 禁止新导入的规则继续有效；当前 SentenceSplitter 旧向量库不能仅因缺少 BM25 就被额外禁止原有导入。
- 旧库写入及 retry 必须使用该库实际保存的 profile、集合结构和 hash，不能混入新服务实例默认 profile。升级 BM25 采用现有 create/import/use 流程，另建库，不原地改旧集合。
- 向量和 BM25 共用同一条 Milvus 记录及原文。写入、更新、删除、失败恢复继续复用现有提交流程；SQLite 仍只保留现有业务元数据，不建立第二套全文索引。

### 1.4 结果、异常与模块边界

- 保留 `search(kb_id, query, top_k=None)`、`SearchParams`、引用 ID 及 `SearchHit/SearchResult` 结构。
- 使用已有 `score_type`：dense 为 `cosine_similarity`，BM25 为 `bm25`，融合为 `rrf`；不把 RRF 分数当作相似度或概率。
- 在现有 `retrieval` 字典补充请求模式、实际模式、实际候选数、RRF 参数及两路返回数量；原有字段保留，metric 准确反映实际策略。
- RRF 沿用 `sum(1 / (rrf_k + rank))`，排名从 1 开始；相同 chunk 合并，平分按 chunk ID 确定顺序。复用已有实现，不直接相加 BM25 与向量原始分数。
- 一路正常零命中时，允许另一条路贡献结果；任何一路执行失败则整次 hybrid 查询失败，保留路名和原始异常，不伪装成功或 no_hits。
- 两路数据库查询、融合及当前来源核对处于同一次知识库锁内。维持 READY、当前 generation、removed、revision 和库归属校验。
- 新增一个小型 `codeplus/knowledge/retrieval.py`，放共享分词/索引常量和纯 RRF 函数；不增加检索框架、工厂或通用接口层。`service.py` 不依赖 `evaluate.py`。

## 2. 分派顺序与交付规则

按 H01 → H02 → H03 → H04 → H05 → H06 串行执行。阶段共享配置、profile、service 和测试文件，默认每次只派一个实现者，验收完成再交接下一项。

| 任务 | 内容 | 前置 | 状态 |
| --- | --- | --- | --- |
| H01 | 提取共享融合逻辑，加入运行配置 | 无 | 代码 review 通过；清理阻塞见记录 |
| H02 | 新库 BM25 索引及旧库读写兼容 | H01 | 待执行 |
| H03 | 日常检索两路召回与 RRF 融合 | H02 | 待执行 |
| H04 | 现有 benchmark 支持三策略对照 | H03 | 待执行 |
| H05 | 真实 Milvus、模型和冻结资料集验收 | H04 | 待执行 |
| H06 | 集成审查、使用文档和清理 | H05 | 待执行 |

通用约束：

1. 执行者先读取本文件、仓库指令和直接调用方，记录 HEAD、工作区和暂存区状态；不得覆盖继承修改。
2. 每项只修改列出的职责范围；相关调用方或测试确需调整时一并交付并说明原因，不顺带重构其他模块。
3. 每项实现者交付未提交的可审查 diff、实际验证命令和结果、剩余问题及下一阶段依赖，不自行暂存或提交。用户已授权 leader 在 review 通过后精确暂存并本地提交；不推送。
4. 实现者先自查，验收者检查契约、真实路径和 diff，再更新阶段状态。测试通过不能代替真实 Milvus 和模型证据。
5. 临时验证脚本、调试文件和日志任务结束前删除；正式测试、明确用于后续复核的独立验收库和评测结果保留。模型缓存、用户原件、旧库和历史结果不得清理。

## 3. 各阶段任务卡

### 执行记录（leader 维护）

- 基线：`ef6bd17f066e86e0cddd21d1ab5a6a5672f66d0a`，分支 `codex/rag`；knowledge 回归 **47 passed / 4 skipped**，配置所在 `tests/test_mcp.py` **24 passed**。真实 opt-in 未执行，不计通过。
- 冻结集检查：31 文档、32 问题；指纹 `c428780933edf5f7bb2c4c35ad9928c87449d32e915eba1e9ed498d23da0fd1f`。
- H01 实现任务：`01a0bf6b-0ba0-7a01-8c68-ff5a7f20180a`；独立工作树 `C:/Users/18221/.codex/worktrees/4469/CodePlus`，已由 leader 对齐上述基线，分支 `c-woker/knowledge-h01`。
- H01 验收：RRF 原函数迁移、共享分词/BM25 参数与配置入口审查通过；`tests/test_mcp.py tests/test_knowledge_evaluate.py tests/test_knowledge.py::test_disabled_knowledge_has_no_optional_imports_or_network` 为 **51 passed / 1 skipped**（leader 独立复跑一致）；service/prepare/benchmark 为 **30 passed / 1 skipped**。日常检索仍为 dense；`git diff --check` 通过。必要调用方包含 `milvus_store.py` 和实验 CLI 的默认 RRF 参数。
- H01 清理限制：工作树 `.h01-pytest` 已删除；`C:/Users/18221/.codex/worktrees/4469/CodePlus/.h01-pytest-service` 尚有 32 个测试生成只读原件。自动审批拒绝强制及逐文件删除，仅返回 `blocked by policy`；普通删除因只读属性失败。该临时目录不复制、不提交；清理未完成不能记为通过。

### H01：共享规则和配置

**修改范围**：`codeplus/knowledge/retrieval.py`（新增）、`evaluate.py`、`codeplus/config.py`、`codeplus/validator.py`、`.codeplus/config.yaml.example`；现有配置/RRF 测试。

**执行步骤**：

1. 迁移现有纯 RRF 函数和固定 BM25 配置，统一实验与日常代码的依赖方向。耗时、实验状态和报告仍留在 evaluate。
2. 加入第 1.2 节配置；保留缺省字段、多层覆盖和显式关闭 knowledge 的原有规则。
3. 更新调用方和已有手算测试，增加少量有效/无效配置案例；不复制已有融合算法测试。

**验收**：原 RRF 手算、去重、空路、失败路实验测试通过；旧配置无需修改；非法值在配置入口拒绝；普通 CodePlus 启动不引入模型或数据库依赖。阶段内不改变日常检索策略。

### H02：集合能力及旧库兼容

**修改范围**：`models.py`、`milvus_store.py`、`service.py` 的 profile/建库/导入/恢复部分；`tests/test_knowledge.py`、`tests/test_knowledge_service.py`。

**执行步骤**：

1. 为新 profile 增加索引契约；新集合建立 dense、text、sparse 及 BM25 函数，两路索引均完成后才能标记建库成功。
2. 校验集合实际字段、函数和索引与绑定 profile 相符，不能仅凭 sparse 字段存在就宣称 BM25 可用。
3. 使用库实际保存的 profile 驱动集合访问和写入。修正导入/pending 材料中直接套用服务默认 profile 的位置，保留当前 generation 计算规则。
4. 支持已知旧结构的现有读写与 retry；新库建库或写入中断沿用原 NEEDS_REPAIR/retry 流程。
5. 更新 MemoryStore 等既有测试替身，并调整依赖“所有日常库都只有 dense”这一旧前提的测试；需要旧库时显式构造旧 profile，而不是删除保护场景。

**验收**：新库 profile/集合能力一致；当前分块的旧 dense 库可继续导入、查询和删除；旧分块的既有限制保持；新旧 pending 都按所属库恢复；未知 profile、hash 篡改或集合不匹配仍拒绝。SQLite 不新增索引副本或迁移状态体系。

### H03：接入日常两路检索

**修改范围**：`service.py`、必要的 `milvus_store.py` 查询适配；现有 service、Agent/引用相关测试。工具参数保持原样。

**执行步骤**：

1. 根据请求模式与库能力确定实际模式；显式请求不支持的模式时，在查询前报错。
2. dense/hybrid 生成查询向量，BM25 直接使用原查询文本；每次 hybrid 对两路各取实际 N。
3. 在现有读锁内完成两路查询、RRF、截取和来源读取；继续验证片段属于当前库、当前有效文档代次。
4. 填写 score_type 和检索元信息；保留引用及结果序列化契约。
5. 用少量关键测试覆盖 BM25 独有候选、双路重复、一路空结果、两路空结果、任一路故障、Top-K 截取及旧库模式分支。

**验收**：CLI、TUI、Remote、Agent 通过原服务自动消费混合结果；来源与分数含义正确；错误能够通过原工具错误路径呈现；更新、删除及跨库隔离没有退步。纯 BM25 的 search 不调用查询编码。

### H04：复用 benchmark 做策略对照

**修改范围**：`benchmark.py`、`__main__.py` 的 benchmark 参数、`tests/test_knowledge_benchmark.py`、`docs/knowledge-benchmark.md`。

**执行步骤**：

1. 给 benchmark 增加可选 `--mode dense|bm25|hybrid`、`--candidates N`、`--rrf-k K`，仅覆盖本次运行的配置，不写用户配置或数据集协议。未传时沿用现有配置。
2. 仍调用同一个 `KnowledgeService.search`，继续使用固定数据集中的 Top-K、问题、gold 与评分函数。
3. 报告记录请求/实际策略、候选数、融合参数和实际 profile；源码指纹包含新增 retrieval 模块及有关配置逻辑。
4. 回放只根据保存的命中重算，不连接模型/数据库；对回放或 check 传入改变检索的参数要明确拒绝，避免产生虚假的重新检索报告。
5. 一次正式报告内校验知识库 revision 不变，查询异常计入原有失败与覆盖统计。沿用独立 runs 目录和旧报告可回放能力。

**验收**：同一混合库可以生成三份不同策略的可比较报告；旧命令和旧报告仍可用；原冻结数据集与评分定义不变。新增参数的 CLI 和覆盖行为有正式测试。

### H05：真实功能及效果验收

**修改范围**：必要的正式集成测试；本地被 Git 忽略的独立验收库、正式评测结果。不修改用户资料、现有库绑定和旧报告。

**执行步骤**：

1. 使用 Windows 原生 `.venv`。通过现有受管部署入口准备 Milvus，使用已固定的本地 Qwen；不升级依赖或替换环境。
2. 用独立小库验证真实中文分词、BM25 独有命中、融合、更新删除、pending 恢复和引用读取。额外构造旧 dense 库验证能力识别及原有读写行为。
3. 读取冻结数据集 `.codeplus/benchmarks/workbin-v1`，核对原件和问题指纹。另建一个明确命名的混合验收库，导入 corpus 中同一批 31 份原件；保留旧库及既有绑定。
4. 在同一个新库、同一版资料和相同 Top-5 下运行 dense、BM25、hybrid。主对照以本轮同库的 dense 结果为准，历史报告只作为回归参考；不能把旧库与新库的差异全部归因于检索策略。
5. 固定默认 N=50、RRF=60 完成第一组。复用现有进程内 service 进行预热和少量完整重复，分别统计冷启动与热查询耗时；BM25/模型预热开销不能混入不同策略的热查询对比。
6. 输出逐题覆盖变化，重点检查 Q20、Q21、Q23、Q28、Q29，同时检查所有原先通过的题。阅读失败样本的实际片段，不只比较汇总数字。
7. 若出现退步，先记录默认参数的完整结果与原因，再按需有限对照 N=20/50 和 RRF=20/60；不改题、不改答案、不改 gold 位置、不放宽评分。保留所有试验结果，不只保留最好的一组。
8. 为混合结果中的不同引用核对来源可读、库与 generation 正确；核对旧库状态和旧引用仍可用。小型临时测试集合清理，正式混合验收库和报告保留，记录其 ID/路径。

**报告必须包含**：

| 项目 | 记录内容 |
| --- | --- |
| 可比条件 | 数据集/原件/问题指纹、库 ID 与 revision、分块与模型、实际策略及参数 |
| 正确性 | 查询错误数、单轮找齐题数、证据区间覆盖、追问分组结果、逐题改善与退步 |
| 耗时 | 预热/重复次数、包含查询编码及融合的热查询 P50/P95，准备与冷启动单列 |
| 边界 | 无答案题仅记录结果；未调用回答模型，不宣称拒答或回答正确率提升 |
| 结论 | 推荐配置及理由；无明显收益或有退步时如实写明；已观察题仅称回归集 |

**验收**：真实功能链路通过且三策略对照完整。环境阻塞、模型未运行、opt-in 跳过分别记 `not executed/blocked/skipped`，不能折算为通过。功能接入完成与效果提升分开判断；没有收益不自动宣称任务失败，也不能宣称已提升回答质量。

### H06：集成审查与交付

**修改范围**：本轮 diff、`docs/knowledge-architecture.md`、`docs/knowledge-setup.md`、`docs/knowledge-benchmark.md`、示例配置与本计划状态。

**执行步骤**：

1. 审查共享规则只有一个实现；确认 runtime 不依赖实验报告代码，没有额外检索框架或重复状态机。
2. 核对旧库 profile/pending、两路异常、当前来源过滤、引用和所有入口的一致性。
3. 完成知识库相关测试和配置测试；合并完全部改动后执行一次全仓回归，修复本轮引入的问题。真实集成按 H05 的明确开关单独记录。
4. 写清默认模式、显式模式、旧库升级命令、分数含义、实际效果及未执行项。文档中的文件名匹配、追问与回答质量边界保持准确。
5. 删除本轮临时脚本、调试文件和临时数据；保留正式验收资料。检查 `git diff --check` 与精确文件清单，交付未提交 diff。

**交付清单**：代码修改清单、测试命令及结果、真实三策略对照报告、独立混合库 ID 与切换命令、已知限制、未执行项、临时文件清理结果。

## 4. 执行命令参考

当前即可执行的基线检查，默认使用 PowerShell 7：

```powershell
Set-Location D:/CodePlus
git status --short
git diff --cached --stat
git log -1 --oneline
.\.venv\Scripts\python.exe -m codeplus.knowledge benchmark --dataset .codeplus/benchmarks/workbin-v1 --check
$knowledgeTests = @(Get-ChildItem tests/test_knowledge*.py | ForEach-Object { $_.FullName })
.\.venv\Scripts\python.exe -m pytest @knowledgeTests -q
```

H04 完成后才能使用以下新增参数；`$hybridKbId` 必须替换为 H05 实际创建并核对的库 ID。运行前沿用现有配置确认服务地址和数据目录，不打印凭据。

```powershell
$datasetPath = '.codeplus/benchmarks/workbin-v1'
$hybridKbId = '<H05实际创建的混合库ID>'
.\.venv\Scripts\python.exe -m codeplus.knowledge benchmark --dataset $datasetPath --kb-id $hybridKbId --managed-local --mode dense
.\.venv\Scripts\python.exe -m codeplus.knowledge benchmark --dataset $datasetPath --kb-id $hybridKbId --managed-local --mode bm25
.\.venv\Scripts\python.exe -m codeplus.knowledge benchmark --dataset $datasetPath --kb-id $hybridKbId --managed-local --mode hybrid --candidates 50 --rrf-k 60
```

以上三条生成正式质量报告；独立 CLI 进程的模型加载和首题耗时不能直接作为稳定性能结论，热查询对照按 H05 的进程内预热方案执行。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
git status --short
```

## 5. 可直接复制的分派语句

> 执行 `D:/CodePlus/docs/knowledge-hybrid-retrieval-plan.md` 中的 **H01**。先核对仓库指令、HEAD、工作区与前置条件；严格遵守第 1 节的实现契约和第 2 节的交付规则。只完成本阶段及必要的调用方、正式测试调整，保留已有修改。完成后交付未提交 diff、实际测试结果和待验收事项，不自行推进下一阶段，不暂存、提交或推送。临时验证文件在交付前清理。

后续分派将 H01 替换为 H02–H06，并附上上一阶段的验收结果、实际变更文件和未解决问题。验收者按对应任务卡确认通过后，再分派下一阶段。
