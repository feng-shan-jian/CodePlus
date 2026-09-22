# R07 分派：文档身份、输入快照与导入清单

2026-09-22；Leader `/root`，执行会话 `/root/r07_inputs`。唯一集成目录 `D:/CodePlus`、分支 `codex/rag`、前置 HEAD **`ebf402ba1d58a3f6651c7894238b29e144bbafed`**（已验收 R06，46 精确文件）。开始先核对实际 Git HEAD、branch、index、适用 AGENTS 和继承状态。R00–R06 全部已本地提交，R07–R26 仍须逐项完成；本项不是终点。

## 权限与必须读取

本执行者为唯一写入者，Leader 和另外评审同期只读。不得 stage/commit/push、应用旧清理补丁、改 Leader 台账／本任务卡／R06 提交后 SHA 段。完成后明确停写交权，再由 Leader 独立验收与本地提交。用户 AGENTS：pwsh7，无 Bash heredoc，复杂脚本写文件；Windows/Linux 独立环境；保持接口／数据／调用方同步；临时验证产物清理、正式测试报告保留。按最新用户指令→已确认需求→spec→任务拆分处理约束，资料正文不作为执行指令。

必须读（相对独立开发区）：docs/implementation-task-plan.md 的流程、R07/R08/R14；docs/implementation-checklist.md G01–G10/C01–C05、R07 五条；docs/plan.md D17/D18/D48–D51 及 1.8/1.9；docs/architecture-and-contracts.md T02/T03、输入快照和检查点；docs/acceptance-and-implementation.md P2/A01/A03；docs/model-providers.md 的实际身份快照；docs/deployment-and-packaging.md 的环境／数据边界。还须读 docs/storage-and-concurrency.md、domain-and-configuration.md、R06.md 独立验收、当前 src/agentic_rag 与正式相关测试。重用 R05 不可变 ProcessingSnapshot 与 R06 归档／owner／短事务，不能另造同等业务权威。

输入 spec SHA256（当前原始字节）：plan `a794366d347b6165ec9a60d6a6d379cddad8dde227d3e9fa4f0b0ed15e7e76fc`；architecture `efc79e7d6a9084fa678d3ac2c52ebd921495b260517d4712d655eefe6ea8539a`；acceptance `5e726283ce77e12a1c21f00c8ef35f87329ff7a62f278dbabc1e56c79c5ca254`；models `29a11df67c73be1e7a4c142ba47476b3348737ef83e7b80cd5157da7b40b5b69`；deployment `285c1ba73e00e24cd785bb8f60c72b97f0850046112a6ceca76eaecb65c5684b`；task plan `edc82c475c84c9c2abe6baa71b11359037985b098fb8236710c9a6890f35d5f6`。动态台账自行记录开工 hash。

R00-protected-inputs.json 有 6553 路径，当前七个累计已授权差异见 R06-leader-audit.json，其余须保留字节／删除状态。根 README、pyproject、uv.lock、旧 RAG 和冻结语料／gold、未跟踪原 compose.yaml 均不可处理。工作区 5904 条历史删除不是本项删除。继承的 Leader 修改仅 R06 提交 SHA、台账和此卡。

## 允许写入

- `src/agentic_rag/ingestion/` 的实际输入选择、路径身份、清单、完整原件采集及读取检查点模块。不提前做解析／分块、Embedding、Milvus 发布、宿主命令、watcher、Agent 或全量恢复交互。
- `src/agentic_rag/storage/` 仅为 R07 所需的 import manifest/item、完整原件引用、文档来源变更、当前版对照等短事务操作及迁移；所有写入沿用 owner/epoch。**保持 R06 migration 1 的既有 hash 身份，新增 schema 用明确升级，实际覆盖 R06 数据库升级／历史读回**，不重新建库或改写历史结构。允许必要 `domain.py`／`_schema.py` 类型错误演进，须保持已有调用并同步测试文档；不修改模型/预算含义。
- 正式 `tests/test_ingestion_inputs.py`、`test_source_identity.py`、`test_input_snapshots.py`，必要正式进程 helper 与 schema 升级测试；名称可按职责调整，交付精确文件清单。相关 storage/domain 测试只随必要契约演进修改。
- 独立 `pyproject.toml`／`uv.lock`／`.gitignore` 仅新增正式源码资源打包或确有必要的轻量依赖；`tests/test_package_install.py` 增加实际源码清单及输入 smoke。不得引入核心强制宿主／GPU／Milvus依赖。README、docs/input-snapshots.md、docs/storage-and-concurrency.md、docs/domain-and-configuration.md 仅同步本项实际契约。
- docs/implementation-records/R07.md 与 R07-*.json/txt 正式证据，排除 Leader-owned dispatch。临时脚本／数据／构建／新安装环境置明确自有系统 Temp 树并清理。复用 `C:/Users/18221/.cache/codeplus-agenticrag/venv-win-core`（Python3.14.3、APSW3.53.4.0/SQLite3.53.4、pytest9.1.1）；不动根 .venv/CUDA 环境／真实用户数据。

## 必须实现与验证

1. 手动一个或多个明确文件／目录，固定遍历范围与顺序，记录每个输入来源及错误；新增／变化／未变可区分，缺失旧文件不隐式删除，无后台同步。只为 Markdown/TXT 后续链提供原件，明确不支持类型及目录边界，不能静默漏报授权范围内错误。
2. 同库同规范路径修改延续 document_id；不同路径同字节、同名不同目录仍各自身份；改名移动默认新增；显式更新选择本库已有 document_id，保留历史版本来源。新路径已属于另一文档、跨库更新、重复或碰撞输入明确失败。路径规则版本化，绝对路径／分隔符／实际文件系统大小写／Unicode 可复现；链接不能悄悄合并不同导入路径，允许明确诊断保守限制。不要用 content hash/file ID 推断文档合并。
3. 在库级 owner 约束下登记批次、基准当前版、固定输入清单和完整 ProcessingSnapshot，再逐文件完整原件采集。R06 begin_mutation 目前要求 input_manifest_hash：需明确不可变请求清单身份与各文件捕获结果／检查点关系，支持采集中断后的已完成结果读回，不能为凑 hash 在批次登记之前把所有原件无保护地采完。允许必要 API 增量演进，旧调用保持可用。
4. 原件只在完整读取、前后状态一致、hash校验和同卷完成协议后成为可用快照；记录捕获时来源 URI／名称／元数据／完整 hash，失败留下文件级错误而非完整快照。后续读取只读归档；源文件再改／删／移、目录新增都不改变本批已冻结输入。明确逐文件一致性，不声称目录同一时点。Windows 实际共享/句柄与修改检测行为须查官方依据和实测，不能仅凭 mtime 假设并发安全；变化、权限错误、不完整读取、IO／空间不足、采集取消／中断都要有明确路径。真实进程中断验证已完成快照和剩余未完整项的边界；IO错误注入如实标注，不冒称真实磁盘满。
5. 输入／快照列表可在重开后读取、核验原哈希和原处理配置。未完整采集项不能在续跑时静默采最新文件冒充旧输入，给明确文件错误／后续命令处理材料；R14 承接完整用户恢复/放弃交互。处理配置改变不因原件 hash 一样就宣称可跳过必要解析／编码／重建，保留相应兼容信息与重建确认边界。R07 不设置伪造 parsed_hash/source_map_hash 来调用需要完整文档版本的 API；这些由 R08 真实解析产生。
6. 变化对照以基准发布成员／对应处理身份为依据，不把未发布或失败历史 attempt 误认为已上线内容。R10/R13 的原子成功部分发布仍未实现；若测试需要当前版，清楚标注合成元数据 fixture，不声称 Milvus／生产发布验收。

## 证据、包装与交接

测试至少覆盖五条 R07 checklist，关联 D17/D18/D50/D51、T02/T03、P2、A01/A03 输入部分；运行已有 domain/config/storage 回归、真实原件读取与修改/删除/移动、Windows 路径边界、实际进程中断、已冻结原件重开核验、schema1→新版本迁移和两个干净安装路径。正式测试必须能让 Leader 独立重跑，并给独立 Temp 根/报告环境变量，避免覆盖实现者证据。外部/不可用平台界限说明；本项无 GPU/Milvus 必需验收，R25 Linux 仍未执行。

R01 正式语料只通过 `eval/runtime_inputs.py` 的冻结 corpus_paths 契约获得；不得将问题、答案、gold／评分模块引入 ingestion。`eval/runtime-inputs.json` SHA256 `8db278a920b290ea9d6f63d5bea473960fb20c26fcb41c5078dececa421d4d0d`，609 篇 corpus；R08 负责全部解析。不要修改或复制用户全量数据进提交，也不要读取 gold 来选择实现。

开始先回报实际 baseline、scope 与接口计划，随后在授权范围实现；常规选择自行解决，遇到真正需求变更或缺必要条件先回报。交付 R07.md 逐条 PASS/FAIL/BLOCKED、自报与 Leader 结论分开；记录实际命令/cwd/依赖/输入与产物hash/退出码，失败结果也保留；正式精确manifest包含每文件hash，自身hash消息另报；保护6553路径审计与 Temp/进程清理。唯一写者结束后停写明确交回，无自行提交／推送／阶段完工宣称整个项目完成。
