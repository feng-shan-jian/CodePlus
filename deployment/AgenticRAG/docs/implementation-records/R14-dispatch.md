# R14 分派：中断续跑、待恢复占用与放弃

状态：DISPATCHED。前置 R13 已实际提交 a4fede1c3f0a09202710e53debdbac3307c0911d，parent 44b861a235233f7072fda267e7a75321431c4e88；124 路径及 blob、空 index、保护范围已独立核对。唯一目录 D:/CodePlus，分支 codex/rag。执行会话 /root/r14_recovery 获明确 WRITE_GRANTED 后为唯一写入者；Leader、此前执行/清理/只读会话只读。不得 stage/commit/push，不另建 worktree。

## 基线与范围

R00–R13 已提交，实际前置 SHA a4fede1c3f0a09202710e53debdbac3307c0911d 与 R14-baseline.json 一起核对。先读用户 AGENTS、task-plan R14/流程、checklist G/C/R14、plan D39/D48–D51 与1.18、architecture T02–T04、acceptance A03/A04，以及 R13 最终源码/执行报告/Leader验收/清理/提交后记。完整项目仍为 R00–R26；R15 GC、R16 配置切换、R21 完整产品命令不在本项宣称完成。

允许修改 deployment/AgenticRAG/src/agentic_rag/ingestion/、storage/、indexes/ 中直接服务本项的协调/持久化/索引所有权代码及必要新文件，domain.py 与包导出可作兼容扩展；tests/ 本项正式行为/故障/升级/真实进程及专属资源，docs/ 本项契约使用说明、README 能力边界、implementation-records/R14* 执行证据。旧 SQL1–7 字节不变，追加 migration8。生产模型 worker 如确有必要先向 Leader 说明最小范围，原则上利用已有请求句柄与 produced_by fencing。保留现有外部接口和默认行为。宿主/root pyproject/uv.lock、冻结需求 spec、评测输入、R13 后记、R14 分派/基线/Leader记录/checklist 是禁止或 Leader 专属范围；白名单外先向 Leader 提出必要性，不自行扩大。

## 必需结果与独立验收

1. 提供持久的恢复检查/计划、显式继续和放弃。启动/打开 Catalog 不自动执行；缺少选择只返回可操作状态。恢复计划显示原批次清单、冻结实际配置、与当前默认配置差异、检查点可复用/不可用理由。不得只按 outcome 行数宣称有效。
2. 沿用原 batch/input/config/base；已归档原件是唯一处理输入，后续源修改、移动、删除、目录新文件和默认 profile 变化不混入。快照阶段未可靠保存的输入明确失败；输入清单缺失明确不能恢复。可验证相同原件的修复不静默引入当前内容。
3. 完整文档编码检查点必须同时校验输入、配置、解析产物、完整 Chunk 次序及 float32 摘要；已可靠提交且有效的完整文档零重复编码。未提交工作从原快照重算；已提交却缺失/损坏的不可变产物可明确失败保留进度，不绕过不可变守卫。strict build_first_revision 也需可恢复并保持全成功语义；不要转成普通部分成功或复制第二套状态机。
4. 同批多次恢复须有新 owner_epoch 和新不可变候选代次、revision、物理 Collection；旧 artifact 创建身份不变。现有 index_artifacts.batch_id UNIQUE 须通过追加迁移及明确当前候选选择解决，不能删除 epoch 检查或覆盖历史 artifact。旧异步写入最多落在旧物理候选，不得污染新候选或发布指针。
5. 所有继续/放弃/模型加载之前核对 SQL 权威 publication 与 immutable COMPLETED_NO_CHANGE completion，receipt=None 不代表未完成。已完成、已放弃的重复请求返回原结果；在之后已有新版时绝不能回退指针/重开占用/删除已发布索引。终态回放不依赖旧 GPU/Milvus，兼容未来历史索引回收。
6. retry_failed 是已正常结束后的新操作；本项继续是原批次，不调用 retry_failed 来替代恢复，也不自动接受新源字节。保留 R13 对后续发布、ABA 和路径身份的保护。
7. 待恢复占用跨进程保存，同库新导入/删除/重建入口都拒绝；旧发布版查询与其他库修改继续，无长写事务或等待用户时持续 GPU 占用。
8. 继续/继续、继续/放弃争用同 OS 锁 + CAS/真实进程身份。旧 token、worker produced_by 和迟到服务结果被隔离。放弃先取消发布资格并解除/转换依赖，再精确回收确认无依赖且归属可靠的候选；保留全部历史原文/解析/来源/引用/任务记录，清理失败持久且可重试。
9. 原模型/revision/tokenizer/组件不可用时明确失败、保留进度和旧版；恢复原环境后可以继续，不写回当前用户配置，不换 CPU/API/新默认模型。已完成结果回放不应无故要求旧模型存在。
10. 新建物理 Collection 的所有权证据不能只凭名字或先登记 artifact。冻结 endpoint/database、创建代次/随机标记，真实 create/describe 往返核对 description、collection_id、created_timestamp 后保存证据。创建前同名碰撞、create响应不确定、凭据未落盘时死亡、同名物理替换都应保留未知资源并换新候选。旧版无证据 Collection 不按当前名字反推可靠所有权，保留且维持旧查询/向量复用。当前 SDK DropCollectionRequest 没有 expected-ID 条件删除，describe→drop 非原子；通过内部不复用名称/生命周期互斥收窄范围，如实记录外部任意重建及 SDK 重试的能力边界，不能虚报彻底解决。
11. 真实进程在快照、解析、编码、索引、发布事务前后终止重启，记录实际解释器 PID+birth，不能只杀 venv launcher。覆盖至少两次恢复代次、提交前死亡/提交后成功响应丢失（publication 与 COMPLETED_NO_CHANGE）、后来已有新发布版仍回放旧结果、缺/损检查点、源及默认配置变化、环境不可用后恢复。
12. 真实 GPU/Milvus 迟到结果 gate：实际 worker RequestHandle 未结束跨旧操作关闭/新 owner 后才完成，元数据拒收旧 produced_by；实际 SDK insert 在通过拥有者校验后、发送前被 gate，换代后释放真实调用使其完成到旧 Collection，并独立证明新候选/当前指针不变。受控运输延迟可用，mock token 单测不能冒充真实 IO。
13. genuine 安装的已提交 R13(v7) 包先建立真实发布 artifact/成员及有意义的待恢复检查点，再安装新包升级。核对旧行/不可变触发器/FK/归档/历史来源与引用/pin；只 capture 的 v7 fixture 不足以覆盖移除 batch UNIQUE 的迁移风险。真实新包 wheel/sdist 两路安装、当前必要核心/宿主回归需通过；与本项无关的未来依赖范围另由 R25 验证。

## 环境与保护

Windows pwsh7；复杂脚本写自有临时根，结束后由另派独立清理处理。只读模型与共享 core/CUDA 参考环境；新环境用 copy 与自有 uv cache，禁止修改共享硬链接属性。宿主集成回归沿用 R12-windows-integration-lock.txt 哈希锁；R13 开放依赖范围失败仍保留，不为偶然新版 SDK 修改无关宿主源码。

执行者临时根 C:/Users/18221/AppData/Local/Temp/codeplus-r14-executor-20260922；Leader独立根 .../codeplus-r14-leader-20260922。专属 Compose codeplus-r14-executor-20260922 建议19538/9099，Leader19539/9100，先确认空闲。只操作有明确归属的本项资源，GPU串行。共享模型、参考环境、uv缓存、固定worker协调锁、既有ignored/用户目录和所有foreign Docker资源保留。pytest-2 在R13清理首检已不存在，不伪造存在基线或重建它。

保护冻结 MultiHop609文档/2556题/6084gold与scorer、继承6531项eval修改、旧compose删除、未跟踪新compose、root README叙事及R13提交后记。不建第二套Agent，不更改未来产品默认或替用户确认D16。

## 交付与交权

先给出实际接缝和实现/验收安排；实现后交 R14.md 逐条 PASS/FAIL/BLOCKED、全部实际命令/cwd/解释器/安装路径/依赖/模型配置/数据指纹/退出码/报告、最终文件SHA清单与自有根/实际PID+birth/Compose容器卷网络清单。保留全部早期失败，不把not executed说成通过，不保存凭据或隐藏推理。

完成自测后 STOP_WRITE 交回Leader；Leader读实际最终补丁并独立运行必要验收，失败退修，通过后另派独立清理，复核并精确本地提交，再进入R15。执行者不自行标ACCEPTED/COMMITTED，不 stage/commit/push。
