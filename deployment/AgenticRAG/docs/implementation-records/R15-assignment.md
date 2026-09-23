# R15 分派：运行占用保护、自动索引回收与历史引用

Leader：`01a0c91e-27b2-7aa2-a146-37daf8601a73`。执行者：`/root/r15_gc`。唯一集成目录 `D:/CodePlus`，分支 `codex/rag`，实际前置提交 `e39de66e638b9bd2f7e6e597d355309bc9518e19`。从执行者接受写权至 STOP_WRITE，Leader 只读；执行者不得 stage、commit、push 或创建另一集成 worktree。

用户 2026-09-23 最新要求：主线继续功能实现，清理收敛由用户另派会话承担。R15 不等待 W1 材料整理、W2 收敛或独立清理，不新增清理任务。测试资源登记归属和实际状态供接管，不删除历史报告、共享资源或其他会话资源。本项产品功能中的索引 GC 仍属于 R15 必做范围。

## 目标与规范

读取当前 `plan.md`、`architecture-and-contracts.md`、`acceptance-and-implementation.md`、`storage-and-concurrency.md`、`implementation-task-plan.md` 和 `implementation-checklist.md`。完成 R15 五个验收条目，对应 T04、A05 和 D09/D12/D31/D32/D39；不缩减为显式调用的演示 GC。

- 运行绑定与回收资格使用同一事务协调协议，真实并发竞争不能删除刚绑定或仍在查询的版本。
- 当前版、完整运行版本、终态但实际读取未结束的运行、构建/恢复/重试及向量复用依赖全部受保护。
- 正常结束安全释放；崩溃占用须核实完整生命周期身份和锁，状态不明保留。
- 已确认闲置的本模块索引自动回收，回收中/失败/重启重试与并发归属正确。
- 全部历史归档保留；索引回收、原始文件更新或删除后，历史引用与原文定位仍可读。

## 必须保持的实现边界

1. `runs.start` 的 READY、编码兼容性核验和完整版本 pin，与 GC 资格检查及 RECLAIMING claim 使用相同 SQLite 短写事务边界；两个真实进程验证竞争的两个次序。
2. 使用现有 R14 的 durable IO、所有权 fencing、artifact intent/proof 和锁。核对 PID、启动身份、nonce、原生锁及事务内未变的身份；超时、单独 PID、client.close 或异常返回均不是完成证明。旧 schema8 中缺少所需证明的记录采取保守处理，不补造凭证。
3. 真实读取跟踪须覆盖直接 RunLease.finish/close、KnowledgeScope 及实际模型/SDK入口，不能只看宿主 future。终态但仍有 GPU/SDK 读取时保留 pin，实际结束后才能释放并触发维护。
4. 保护 current、所有 active pin、非终态 mutation 的 base/candidate、恢复的各代 artifact、vector_reuse 及未知在途 IO。保留 immutable publication/history；物理索引删除不删除文档/解析/结构/引用归档。
5. 自动维护在发布、释放、放弃和启动等适用位置有界触发；短 SQL 与外部调用分离，持久化 claim/error/retry，显式重试也有界。启动维护不得自动加载模型或恢复模型任务。
6. 物理删除前核对记录的 collection ID、创建时间、描述、endpoint 和数据库身份，同名替换资源必须保留。明确当前 SDK 缺少 expected-ID 条件删除时，外部管理员并发替换的原子性边界，不虚称能完全排除。
7. `source_archive.read_version` 已独立于索引状态；不要为历史读取放宽正式已发布 artifact 的 READY 查询约束。所有对外接口和 DTO 保持兼容，需要变更时同步全部调用方和正式测试。
8. 现行 RequestHandle 尚无 frozen worker identity。若 R15 需要，可对 models/client.py 增加最小、兼容的句柄出生身份快照接口并同步必要调用方；不能从可能轮换的 provider.metadata 事后推定本次执行身份。仅合入 R15 必需接口，不复制独立树未验 W2 的 session 轮换、payload GC、诊断或 load 优化。

## 允许与保护范围

允许 storage 的 runs/catalog/reader/GC 实现与追加迁移、必要的索引适配器所有权删除接口、sources/retrieval/policy/KnowledgeScope 的最小生命周期接点、上述最小 worker 身份接口、正式测试及本项说明。先沿实际调用链核对路径；不要猜文件名。

8份既有 SQL 逐字节保护，仅追加迁移并同步当前 schema 断言；真实旧 schema8 基线须来自此处已提交代码。历史已提交报告、用户 README、官方609文档/2556题/6084 gold、继承工作区输入、旧 compose 删除状态、共享模型/环境/cache、bf43 全树及历史审批阻断资源保持。不得混入材料压缩、W2 整包、R16 配置切换、R17/18 检索/rerank 或第二套 Agent。

前置提交后两份真实后记尚未提交，保持字节并由 Leader 随 R15 精确携带：`W1-bs02-leader-stage-execution.json`、`W1-bs02-leader-postcommit.json`。R15 的 baseline 要区分这些既有输入和本项新增内容。

## 验证和交付

- CPU 正式测试覆盖资格矩阵、两个真实进程的绑定/GC竞争、并发 collectors、未知占用保留、失败与重启重试、同名替换保护和不可伪造身份。
- 安装实际当前 wheel/sdist；完整 core 和必要 host 回归；从实际前置 schema8 wheel 创建数据库，删除旧源码后由新安装包升级，8份旧 SQL hash 不变，integrity/FK、历史记录保持。
- 使用真实 GPU 和专属 Milvus：旧任务在 V2 更新/删除发布后首次查询从未见过的旧文档，新任务看到 V2；真实未结束 GPU 和受控阻塞的实际 SDK 读取期间终态 pin 仍保留，结束后自动回收。
- 对实际 drop 前后、实际删除后但 receipt 前终止进程；服务故障/恢复、进程重启和两个 collector 竞争分别验证。不得用 mock 或静态扫描替代这些路径。
- 物理索引回收且原源文件删除后，用新进程、无 GPU/Milvus/reparse 读取已存引用及未引用的旧文档版本；核对真实原文和定位。
- 为任务自有临时根建议 `C:/Users/18221/AppData/Local/Temp/codeplus-r15-executor-20260923`，专属项目同名，端口19540/9101；先验证可用性，保留归属清单。共享环境仅作为只读复制来源，使用私有环境/cache。Leader 验收资源另行创建。
- 交付 `R15.md`、逐项 validation、文件原始 SHA清单、实际命令和失败记录、资源归属/进程PID与启动身份、未执行边界。工程自验完成后 STOP_WRITE 交回，等待 Leader 独立审查和实际验收；不自行提交。
