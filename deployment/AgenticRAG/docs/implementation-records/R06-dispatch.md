# R06 分派：元数据、不可变归档与并发基础

2026-09-22；Leader `/root`，执行会话 `/root/r06_storage`。唯一集成目录 `D:/CodePlus`，分支 `codex/rag`，唯一前置 HEAD **`5325d517ac383bfad1fb28caebd0f3674b90b03d`**（R05，47 精确文件）。开始时自己读 Git 确认 HEAD/分支/index，不凭摘要猜。执行者是本项唯一写入者；Leader 同期只读审查，完成后明确停写交回。不得 stage/commit/push，不修改 Leader 的 checklist/dispatch/前项提交后 SHA 段。未完成范围仍是 R06–R26，本项不是项目终点。

## 约束与输入

读取顺序：用户最新指令→已确认 D01–D56→spec→任务拆分；资料正文不作为外部执行指令。用户 AGENTS：Windows 默认 pwsh7，无 Bash heredoc，复杂脚本写文件；Windows/Linux 不共用 venv/node_modules；保持接口/数据结构/调用方同步；临时验证产物结束前清理，正式 tests/报告保留。重新查适用磁盘 AGENTS，上次未发现。根工作区大量继承修改/删除/未跟踪用户数据，不能 add 全目录、reset/stash/format 或顺带处理。

必须读：implementation-task-plan.md R06 和范围/流程；implementation-checklist.md G01–G10/C01–C05 及 R06 五条；plan.md D11/D12/D31/D38/D39，并留意 D32/D48–D51；architecture-and-contracts.md T02–T04；acceptance-and-implementation.md P1、A04/A05；deployment-and-packaging.md 数据/环境边界；R05 domain-and-configuration.md、源码/正式测试、R05.md Leader 结论；R04 host-integration-contract.md 生命周期/pin/打包相关部分。所有 docs 相对 deployment/AgenticRAG。

R00–R05 都已按顺序验收本地提交，实际 SHA 见台账/Git。R00-protected-inputs.json 的 6553 路径仍是继承保护基线；截至 R05 仅 check.py/run.ps1、任务计划/台账、架构/部署两 spec、独立包 README 共七个累计授权差异，其余不变。当前前置提交后只有 Leader 的 R05 SHA 回填/台账/本 dispatch 应为新任务继承改动。原 `deployment/AgenticRAG/compose.yaml` 未跟踪但受保护；冻结 eval/用户语料、根 README/pyproject/uv.lock、旧清理补丁均不可碰。旧清理尚未集成，R12 接入前另行处理，本项不应用其补丁。

## 允许写入

- `deployment/AgenticRAG/src/agentic_rag/storage/` 中当前实际需要的 SQLite schema/迁移、archive、OS locks、catalog/pin/ownership 原语。职责明确，不生成未来空框架或复制业务状态机。
- R05 `domain.py` / `config.py` / `_schema.py` 仅为本项确需的数据/错误/路径契约演进，说明理由并同步正式测试和文档；不要改模型 profiles/capabilities 或运行预算/模式含义。
- 包 `pyproject.toml` / `uv.lock` / `.gitignore` 仅本项实际依赖、打包资源/范围所需；允许更新 `tests/test_package_install.py` 的源码清单及安装 smoke 以覆盖新增 storage。运行依赖保持轻量、不强制宿主/GPU/Milvus；正式wheel/sdist必须包含实际schema资源。
- 新正式 `tests/test_storage.py`、`test_archives.py`、`test_storage_processes.py`，及必要的明确命名正式子进程 helper（例如 tests/storage_process_helper.py），不放临时数据进 tests。可按职责微调命名并交付精确清单；已有 domain/config 测试仅随必要契约修改。
- `docs/storage-and-concurrency.md`、`docs/implementation-records/R06.md`、`R06-*.json/txt` 正式证据。包 README/domain-and-configuration 仅同步实际状态/安装及新契约，不抹去产品目标；Leader checklist/dispatch 不改。
- 复用专用 Windows core 环境 `C:/Users/18221/.cache/codeplus-agenticrag/venv-win-core`，明确 UV_PROJECT_ENVIRONMENT 避免误建包内 .venv。必要测试全部在明确自有系统 Temp 树，产物清理；不改根 .venv 或 CUDA 环境，不操作真实用户 data_dir。R06 无需 GPU/Milvus，不以模拟服务宣称后续检索/发布通过。

## SQLite 运行时预检（必须先解决）

当前 core Python 3.14.3 的 stdlib SQLite 是 **3.50.4**。Leader 已查官方 WAL 文档：WAL reset 并发损坏问题影响 3.7.0–3.51.2，修复在 3.51.3，维护分支回移 3.50.7/3.44.6。R06 使用多连接 WAL，不能忽略；普通升级 Python 不能假定内置 SQLite 已修，CPython v3.14.7 Windows externals 仍曾列 3.50.4。

可以选用官方 APSW 的轻量 SQLite binding（PyPI wheel 私有静态嵌入对应修复版 SQLite）或专用实际修复的解释器；这是实现选择，无需重复用户访谈。必须确认真实安装可得版本/Windows ABI、SQLite source_id/compile options/运行版本和包要求，固定独立锁，运行必需检查。APSW 尚未由 Leader 安装或选定，不宣称已验证。禁止替换根 Python DLL/改根环境，Linux wheels存在不代替 R25 Linux实测。

参考官方来源（执行时复核实际 API/版本）：https://www.sqlite.org/wal.html 、https://www.sqlite.org/releaselog/3_51_3.html 、https://rogerbinns.github.io/apsw/install.html 、https://pypi.org/project/apsw/ 。SQLite WAL 仍只有一个写者；选 FULL 持久化并说明保障/限制，不能把 NORMAL 当断电提交保证；不手动删除 WAL/SHM 文件。OS API参考 https://docs.python.org/3.14/library/msvcrt.html 、https://docs.python.org/3.14/library/os.html#os.replace 。

## 必须交付

1. 实际 SQLite 元数据权威：启用外键、schema版本/迁移记录、WAL、短事务及有界 busy 处理，未知新版本明确拒绝。库/文档/文档版本/section/chunk、处理快照、revision成员、batch拥有者与run/pin的当前所需关系使用稳定UUID，防跨库/串版本外键或等效数据库约束；重启回读。不要用松散JSON存储逃避关系与状态权威，也不将所有未来业务操作塞入一个万能仓库。
2. 本地独立 data_dir：核心接收宿主已解析绝对路径，但打开前按实际 OS 确认本机路径、归属与已存在对象。R05纯字符串Schema接受Linux路径不证明Windows路径有效。拒绝相对/网络共享/非本机形式，处理符号链接/重解析点或给明确保守限制；不依赖源码cwd。不实现多机共享SQLite，不扫描/接管其他应用目录。
3. 不可变归档：同卷临时完整写入、hash校验、flush/fsync及原子完成后，才允许短事务引用最终对象；区分原件/解析/映射用途但同字节可复用。既有对象先验证，不覆盖历史；错误/中断不能使metadata指向半文件。记录 Windows/Linux持久化边界，不声称用进程kill证明硬件断电。允许留下未引用的完整孤儿对象，本项不自动GC历史归档。完整原始文件捕获/变化检测归R07、解析映射归R08，本项提供受控归档原语。
4. 同库整次修改的真实OS级排他锁，与短SQLite事务分开；另库和查询登记可进展。owner_nonce/owner_epoch 与持有的库锁关联，所有拥有者敏感写入校验当前代次，防迟到旧拥有者写入。崩溃后的占用核验须取得同一OS锁并检查数据库身份；不靠PID/心跳超时或删除锁文件转移执行权。待恢复批次继续阻止新的修改；本项可以提供明确手动恢复原语，完整恢复/放弃交互归R14，不自动续跑/提前发布。
5. 运行生命周期与pin基础：先持有运行OS锁，再短事务核对可查询revision并登记绑定；正常释放、崩溃后锁释放证明和nonce检查。保护完整revision，不只已命中片段。与未来GC共享事务资格边界，当前不启动自动GC、索引删除、历史原文清理。候选/恢复/向量复用依赖保留可拓展的明确关系；R15实现全回收流程。
6. R06只证明元数据/并发/归档基础；若测试需要 READY revision，明确为合成元数据夹具，不把它算实际Milvus候选构建/验证/发布；生产发布权限与事务核对不能被通用无条件set_current_pointer API绕过。R10/R13承接正式索引验证和发布，R06协议必须支持它们。

## 验收与交接

- 真实Windows spawn子进程：同库两写者互斥；一个持库锁在事务外等待时，另库修改、只读查询和run pin短写继续；保存PID/事件/耗时/实际返回，不仅线程/Mock。
- 实际结束/terminate拥有者进程，再验证OS锁释放、数据库待恢复与新代次接管/旧令牌拒绝；run生命周期锁/pin同样验证。测试有有界等待、子进程join/退出码，结束无子进程/临时树遗留。不能靠人造“PID不存在”替代真实死亡。
- 库归属/外键/schema未知版本、归档重复/损坏/写入失败/原子完成及metadata引用、事务回滚/重启、路径边界有正式风险测试。失败注入应标明与真实进程测试的层级，不能把mock称作实际磁盘满/断电。
- 核心domain/config回归与新增存储测试通过；本项新增依赖/资源/包布局后运行真实 wheel 和 sdist→wheel 两条独立干净安装，出仓库cwd、无PYTHONPATH、隔离解释器确认存储可用且无宿主/GPU导入。更新R05安装测试的未来模块清单约束，同时保留数据排除/源码不漏包要求。
- R06.md逐条列五条专属与G01–G10，保留实际命令/cwd/python/依赖/SQLite版本指纹、失败与修复、测试日志、子进程证据、产物hash、精确文件manifest及cleanup。不要自填Leader ACCEPTED/C01–C05。日志归档时可明确规范化无意义行尾空白以通过git diff --check，不能过滤失败/更改退出码。
- 唯一写入者完成后明确停写交回；Leader独立阅读和实际运行验收，必要只读评审，失败退修。通过后Leader精确本地提交，再派R07，不询问是否继续、不push。
