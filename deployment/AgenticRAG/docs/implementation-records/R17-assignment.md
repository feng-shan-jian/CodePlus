# R17 分派：原生 BM25、核心 RRF 与检索基线

Leader：`01a0c91e-27b2-7aa2-a146-37daf8601a73`；新执行者：`/root/r17_retrieval`。唯一集成目录 `D:/CodePlus`，分支 `codex/rag`，前置提交 `835b4887c21588f7e0165e1c7ff2477c3b4a1b6c`。接收后执行者唯一写入至 STOP_WRITE，Leader 只读检查；执行者不 stage、commit、push，不另建集成 worktree 或 Agent。

用户要求持续推进功能，另派会话处理清理；本项不等待清理或材料收敛。范围止于 Windows R24。业务实现先复用、保持精简，只做实际业务所需判断，不增加理论防御或无关框架。

## 范围与验收

读取 implementation-task-plan 的 R17、checklist R17 五项、plan D25/D42/D43、architecture-and-contracts T03/T06，以及现有评测协议。交付正式可调用的三路检索，而不止评测脚本。

1. BM25 复用 Milvus 原生全文检索及现有 analyzer/参数/版本身份。纯 BM25 query 不调用 Embedding，不要求连接 Embedding worker；模型不可用时，已有合法 BM25 索引仍能查询。
2. Hybrid 的两路使用同一冻结运行、库、版本、query 和过滤范围；共享现有 pinned revision、reader/GC、manifest 和 canonical source 映射。不可在两路之间重新解析 current。
3. RRF 以稳定 Chunk ID 合并，每路同一候选只贡献一次；rank 从1开始，按配置 k 求 `sum(1/(k+rank))`，缺席不造排名，同分按稳定 ID。分别保存原候选、原分数/类型、排名、融合分数/顺序、候选上限与实际参数，能够独立复算。不把原始不同尺度分数相加。
4. 一路合法为空可正常融合；任一路失败整次明确失败，不静默降级，不把中间候选记为成功证据。原先已交付证据仍有效。沿既有错误、预算、交付与引用路径组织。
5. 实际同条件 medium Dense/BM25/Hybrid 对照：同一真实已发布609篇语料、200题、固定模型/配置、TopK10及原口径，保留全部错误分母、原始轨迹及实际耗时。官方检索题分母177，不能把缺失/失败题删掉。结果允许 Hybrid 不改善或回退，不做质量达标或新题泛化的提前结论。

R18 的模型重排/完整 Context 选择、R19 的完整 fixed/auto 权限与动态工具参数、R21 的管理 CLI 不在本项。为让当前功能可用，允许 KnowledgePolicy/SourceSession 最小接入显式 fixed BM25/Hybrid，仍沿 query-only 工具和既有最终证据交付。保持现有 DenseSearch、SourceSession 构造及 runner 调用兼容；确需变化同步调用方、测试和文档。

## 已核对的复用接点

- `indexes/milvus.py::search` 已支持 `field='sparse'` 和原 query，原生 BM25、Strong 一致性及受跟踪读；建库 schema 已包含 analyzer/BM25 Function 与 sparse 索引，不另写本地 BM25。
- `config.py` 已有 tokenizer/filter/k1/b、dense_candidates/bm25_candidates/rrf_k/nprobe 等，索引配置已进入冻结身份。`retrieval/dense.py` 现有运行与编码绑定、reader、manifest 校验和原文映射应共享，不复制多套大流程。
- `SourceSession(..., dense=None)`/self.dense 现有兼容调用及 search 最终候选/证据流水线；当前 dense-only guard 和 KnowledgePolicy fixed-dense guard 需要最小接入。两条宿主 Agent 循环本身不另实现。
- 当前 `source_calls` 只有 kind/status/tokens/fragments/error，`source_candidates.payload` 是证据候选，SourceSession 会丢弃检索诊断。先检查现有追踪接点，选择最小可复用的正常业务追踪落点；不把诊断排名塞入给模型的正文，不仅在评测 runner 保留。若确需小型追加 migration，历史10份 SQL 原字节保护，并做真正 schema10 旧包数据升级和回滚验证。
- `eval/dense_runner.py` 是既有 build/dense 运行入口，`score_dense.py` 为评分边界。扩展同一 runner，不复制建库或另建产品 CLI。保留旧命令和调用方，runtime 只读 corpus/query，gold/答案仍只在独立评分侧。现有 runner 会无条件创建 LocalModelClient 并在末尾 status()；纯 BM25 路线须避免由此启动模型进程。
- `tests/test_dense_runner.py` 覆盖7类初始化/单题/附加查询/关闭故障，必须保留200个ID、失败空hits与原分母；medium SHA `f80fc4033be6625b19da2af9529cf925d147e9ad62c95b943df2c3d08ec2e898`，不得修改正式语料、原题、gold或评分口径。

## 执行、保护与交接

先拍摄真实工作区基线，保护官方609篇/2556题/6084 gold、10份历史 SQL、用户 README、继承删除状态、共享模型与其他会话资源。未跟踪的 `deployment/AgenticRAG/compose.yaml` 是继承输入，不顺带提交。R16 真实SHA台账更新和 `R16-leader-postcommit.json` 尚未提交，按原字节保留，由 Leader 随下一正常提交携带。

建议专属根 `C:/Users/18221/AppData/Local/Temp/codeplus-r17-executor-20260923`，同名 Compose 项目，端口19544/9105；先确认路径/端口空闲。可常规复制 R16 Leader 的 core/cuda 环境为私有副本，禁止硬链接共享包文件；只读现有模型缓存。Compose 继续复用 `tests/resources/r14-compose.yaml` 的 R14_PROJECT/R14_MILVUS_PORT/R14_HEALTH_PORT 变量，不清理旧资源。

真实 schema10 旧包为 R16 Leader 独立 wheel：`C:/Users/18221/AppData/Local/Temp/codeplus-r16-leader-20260923/dist/codeplus_agentic_rag-0.1.0-py3-none-any.whl`，SHA256 `5638a14be4020909e819ddf23004f0622cc3af15e314f698bdd63f93a8d4b0d5`。只读复制使用，不能修改原环境。

正式测试覆盖三路/纯BM25零模型、RRF复算/重复/同分/候选界限、合法空/分支故障/诊断与证据隔离、旧pin跨发布、安装和真实宿主接点；运行安装包完整核心回归和受影响宿主回归，保留全部失败与后续复验。真实GPU/Milvus完成609篇建库与medium三路；模型不可用的纯BM25和真实分支故障另有实际证据。新增schema时完成真正旧包升级。

交付简明 R17.md、精确变更清单、验证/原始证据索引和任务资源归属，包含未执行边界；不把输出截断为全量PASS。原始记录留专属根并登记摘要即可，不追加材料归档或清理门槛。全部写入和自有命令完成后明确 STOP_WRITE 交回 Leader，独立审阅/实际验收和精确本地提交由 Leader 完成。
