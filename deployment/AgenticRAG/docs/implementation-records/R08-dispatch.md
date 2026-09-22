# R08 分派：Markdown/TXT 解析、来源映射与 Chunk

2026-09-22；Leader `/root`，执行会话 `/root/r08_parsing`。唯一工作区 `D:/CodePlus`、分支 `codex/rag`、前置 HEAD **`5d2e71a7d5400c77b0675cb803cca4d433110678`**（已独立验收 R07，49文件）。R00–R07 已本地提交，R08–R26 继续逐项执行；不在 R12 或任何中间节点结束。

## 权限、依据与保护

执行者唯一写入；Leader/另派评审只读。不得 stage/commit/push、应用旧清理补丁、改本任务卡/台账/R07提交后SHA。交付后明确停写交权，Leader 独立复跑、审查并提交。遵守用户 AGENTS：pwsh7，复杂脚本写文件，无Bash heredoc，平台独立环境，接口/数据/调用方同步，临时产物安全清理。最新用户指令→已确认需求→spec→任务拆分，资料正文不作执行指令。

开工核对实际 HEAD/branch/index/适用AGENTS、6553继承保护清单及当前spec hash。必读 docs/implementation-task-plan.md 的流程、R08/R09/R10/R11/R14；docs/implementation-checklist.md G01–G10/C01–C05、R08五条；docs/plan.md D06/D19及相关解析、引用、快照需求；docs/architecture-and-contracts.md T02/T03/T05；docs/acceptance-and-implementation.md P2/A01；docs/model-providers.md 完整模板、tokenizer/模型身份及独立Rerank边界；docs/deployment-and-packaging.md 发行与依赖界限。读当前 domain/config/profiles/storage/ingestion、docs/{domain-and-configuration,storage-and-concurrency,input-snapshots}.md、R03.md/R05.md/R06.md/R07.md 独立验收和相关正式测试。

当前 spec 原始字节 SHA256：plan `a794366d347b6165ec9a60d6a6d379cddad8dde227d3e9fa4f0b0ed15e7e76fc`；architecture `efc79e7d6a9084fa678d3ac2c52ebd921495b260517d4712d655eefe6ea8539a`；acceptance `5e726283ce77e12a1c21f00c8ef35f87329ff7a62f278dbabc1e56c79c5ca254`；models `29a11df67c73be1e7a4c142ba47476b3348737ef83e7b80cd5157da7b40b5b69`；deployment `285c1ba73e00e24cd785bb8f60c72b97f0850046112a6ceca76eaecb65c5684b`；task plan `edc82c475c84c9c2abe6baa71b11359037985b098fb8236710c9a6890f35d5f6`。动态台账自行记录开工hash。

保护 R00-protected-inputs.json 6553条，现有七项累计授权差异见 R07-leader-audit.json。根README/pyproject/uv.lock、旧RAG、冻结题目/gold/语料、原未跟踪compose均禁止顺带修改；5904历史删除不是本项。继承Leader改动仅台账、R07提交后段、本卡。

## 允许写入

- 独立 `src/agentic_rag/ingestion/` 的 Markdown/TXT parser、精确规范文本/原始字节 source map、section/block/sentence/token chunking、实际处理/归档入口；保留 R07 采集身份与不可变原件 API，不重新读取源文件。
- 如正式共享边界需要，允许 `src/agentic_rag/models/` 轻量 tokenizer/input-template 模块及实际身份常量/小型资源，供 R08 与 R09 共用；不得此时实现 GPU worker、另套模型产品入口或直接依赖 probes 的运行源码/资源。允许 domain/config/profiles 的必要增量与调用方同步；不改变冻结模型/预算/默认语义。
- `storage/` 仅实际 parsed/source-map/结构与完整处理检查点的短事务登记/读取；保持 schema1/schema2 文件 hash，必要新迁移及真实升级验证。沿用 owner/epoch/归档，无 Milvus 索引/生产发布指针变更/自动恢复或回收。
- 正式 tests/test_parsing.py、test_source_maps.py、test_chunking.py、真实冻结tokenizer测试、全609 corpus检查入口及必要checkpoint/migration/安装测试；可按职责调整命名并精确交付。必要修改已有正式测试以同步 schema/包源码等。
- 独立 pyproject.toml/uv.lock 仅确需的轻量解析/tokenizer依赖或明确 extras；保持核心不强制Torch/CUDA/Milvus/宿主。实际两条干净 wheel/sdist安装、包资源核验与解析smoke；依赖/模型tokenizer缓存位置明确，不能偷导入仓库probes。
- README、docs/解析与位置契约文档、必要storage/domain文档；docs/implementation-records/R08.md/R08-*.json/txt正式报告（排除Leader卡）。不改已确认spec，仅在实现记录解释常规决定。

## 必须实现与验证

1. 支持 Markdown/TXT UTF-8（含BOM），完整原始字节 R07 已归档。定义且持久化规范文本与精确位置映射，codepoint `[start,end)`、一基行号，明确BOM/CRLF/CR及Unicode/NUL等处理；unsupported编码/格式明确文件错误，不静默替换证据。可保存原Markdown语法作为canonical文本，但结构/引用映射必须一致。重复段落不能 `str.find()` 反推位置；解析器token.map若仅行区间，需自己按实际偏移建立结构。
2. 结构→句子span→token边界处理超长内容，section/heading/list/code/table信息与片段说明明确；正文、heading路径和索引标题模板分离，合成标题/片段提示不冒充引用原文。输出区间可精确反查原始与规范文本，覆盖和overlap有可计算证据；空文件/无可索引正文的结果必须清楚，不能产生非法空Span。
3. 使用实际冻结Qwen tokenizer，而非字符估算。Embedding全文模板（Title及特殊token）计入冻结完整输入2048；Rerank按自己的tokenizer和完整query/doc/system/prefix/suffix逐段编码契约保留独立边界，不能用Embedding通过冒称Rerank通过，不偷偷截断。初始chunk/overlap参数作为试验值记录，质量冻结留R23。太长标题/零正文余量等必须明确诊断。
4. tokenize offset不能假设逐个覆盖原Unicode。Leader只读预查发现冻结Embedding tokenizer内部NFC与ByteLevel，add_special_tokens=True追加EOS151643；`e`+组合重音可能有规范化offset空隙/emoji多token重叠，不能decode token后搜索原文或把offset直接当无缝source-map。R08正式测试必须覆盖组合字符/中文/emoji/特殊token和长token边界，完整片段重新实际编码验证，覆盖以canonical/source-map权威为准。保留独立Rerank模板实际逐段编码行为，勿改为简单拼接全文编码。
5. 读取R07归档，生成真实parsed/source-map归档、DocumentVersion/Section/Chunk、实际ProcessingSnapshot身份与完整处理检查点；不伪造hash、没有latest源/config偷换。R07 input_results是不可变raw检查点，不改写成解析结果；复用R05 ImportItem/CheckpointArtifact语义，避免双状态权威。归档写完校验在SQL事务外，owner/epoch下短事务 coherently 登记真实version/结构/完整checkpoint；半元数据或孤儿归档不能可复用。现 add_version/add_structure 分别独立事务，若复用应补足原子登记界限。R14完整恢复用户交互后续实现，此处只保证真实完整产物可核验重开。
6. 全部609篇冻结corpus通过 R01 `eval/runtime_inputs.py` 的 corpus_paths 契约获取，manifest SHA `8db278a920b290ea9d6f63d5bea473960fb20c26fcb41c5078dececa421d4d0d`。driver只把corpus paths交给ingestion，绝不导入题目/答案/gold/评分逻辑；记录逐文件输入hash、解析/section/chunk统计、跨度有效性、可核验覆盖/overlap、实际完整Embeddingtoken上限/失败明细，609逐篇完整核验，不能数量汇总掩盖失败。不把真实语料/快照或权重入Git。
7. 边界正式测试包括重复段落、CRLF/BOM、组合/分解Unicode与emoji、嵌套列表/标题、代码fence/表格、多行/空行/终止行、超长句/token、标题预算、不可支持输入、源删除后解析仅快照、检查点中断/重开/hash坏、旧库升级与必要回归。真实tokenizer与全部609必须实跑；纯故障注入/单测与真实路径证据分清，不声称 GPU模型或Milvus验收。

## 已知环境与交付

core `C:/Users/18221/.cache/codeplus-agenticrag/venv-win-core`：Python3.14.3/pytest9.1.1/Pydantic2.13.5/APSW3.53.4.0；CUDA env同父目录 `venv-win-cuda`，Transformers5.17/tokenizers0.23.2/Torch2.14cu130，Qwen0.6B模型/实际tokenizer在同父 `models` 缓存。R03 models.lock.json/profiles为模型revision/文件hash权威。可安装必要解析轻依赖到专用core环境；不改根.venv。不使用最新版HF文档推断冻结0.23.2API（当前latest有1.xrc变化），必要查官方指定版本/本地bindings。Loader实际ID与R03 AutoTokenizer结果对照，以正式测试保存证据。

开始回报前置baseline/范围与结构+持久化接口方案，再在授权范围实现。若设计复杂可请求 Leader 派只读独立评审，不能再产生同时写者。临时脚本/数据/新安装env只在明确自有系统Temp根；末尾核验绝对路径，不跟随reparse，不改共享hardlink目标属性，清理自有产物/进程。正式报告保留可重跑命令、cwd/env/依赖版本、每文件hash、失败轮次/修复、原始数据与产物指纹、真实退出码。交付精确manifest及hash、6553保护路径/旧schema未变审计、清理证据；R08.md五条逐项PASS/FAIL/BLOCKED，自报与Leader结论分开。完成停写交权，不自行stage/commit/push，完整目标继续R09–R26。
