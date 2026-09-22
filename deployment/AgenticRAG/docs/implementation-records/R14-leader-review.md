# R14 Leader 独立验收

状态：COMMITTED。独立代码、运行验收、独立清理和 Leader 清理复核已通过；本地提交175f8f54ed9e24736a232f6b989d145f7f217dca已完成，161个精确路径/blob、parent与空index复核通过。唯一集成目录 D:/CodePlus，分支 codex/rag，基线 a4fede1c3f0a09202710e53debdbac3307c0911d。执行者 /root/r14_recovery 和独立清理 /root/r14_cleanup 均已 STOP_WRITE；Leader 持有唯一写权限，未 push。

## 冻结对象与代码复核

执行者 102 文件清单 R14-files.json SHA256 为 f417bbd935a05839cba24e8b32ad0ea378bef30751e5b2e6165c915f78b3be8b。Leader 逐项重新计算无差异，并阅读新增完整文档编码、恢复入口、SQL8、执行/IO/清理协调，以及 build/mutations/publication/ownership/database/Milvus 的实际改动、相关正式测试与使用契约。没有发现阻塞交付的问题。

旧 SQL1–7 字节保留。迁移新建并复制 artifact 表后删除原表、改回原名，不重命名原表；FK 检查和失败回滚覆盖真实 v7 已发布与待恢复数据。恢复以原输入/配置/基准版和完整检查点为边界，创建新 epoch/revision/Collection；旧身份和历史记录不可变。SQL publication、无变化 completion、abandonment 先于模型和服务回放。实际库锁/CAS/PID+birth 与迟到 IO 证据保持，清理只认领可靠归属、无依赖的已放弃候选。

已确认 Record.model_copy 本身重新校验模型；初期将它当作 Pydantic 默认不校验实现的推断错误已撤回，生产 runs.py 未为此增加重复校验。首次建库的 postcommit observer 已取消，保留现有提交前观察契约；严格首次建库仍要求全部原文件成功。

## 独立实际结果

| 验证 | 结果与证据 |
| --- | --- |
| 新环境、独立构建与安装 | PASS；R14-leader-setup.json。core/CUDA 实际从 site-packages 加载，每个生产 py/sql/json 与当前源码和 wheel 字节一致；共享 CUDA 22380 项元数据复制前后不变 |
| core 全量 | 622 passed / 29 skipped，867.39 秒；R14-leader-core-command.json、txt、xml、modules.json。35 个 R14 正式用例全通过 |
| 宿主全量 | 715 passed / 3 skipped / 1 既有 warning，36.00 秒；R14-leader-host-command.json、txt、xml、modules.json |
| wheel/sdist 干净安装 | 两路 PASS，两个重建 wheel SHA 相同；R14-leader-core-installations.json。核心隔离安装的归档、来源、引用和缺失依赖诊断均运行 |
| 完整冻结语料处理 | 609/609，4041 Chunk，0 失败，完整输入最大512 tokens；237.96 秒，R14-leader-core-corpus.json；不是检索/回答质量评估 |
| 真实恢复完整矩阵 | 13/13 PASS，exit0；R14-leader-real.json 与 command。12 次实际解释器 PID+birth 终止；连续两次恢复共三代不同候选，两次恢复的模型调用均为0 |
| 真实物理归属、清理和争用 | 6/6 PASS，exit0；R14-leader-lifecycle.json 与 command。同名碰撞、创建响应不明和物理替换保留；服务恢复后重试清理；两种双解释器争用均仅一位新拥有者 |
| genuine 已安装 v7→8 | PASS，exit0；R14-leader-upgrade.json 与 command。真实旧包先建 publication1/artifact2/编码3/17归档/引用/active pin；升级与故障回滚核对旧行、trigger、FK、归档、typed Run，原 pending 零重复编码 |
| 独立原始状态交叉核对 | PASS；R14-leader-crosscheck-second.json。直接打开20个实际数据库，核对完整性、FK、artifact/publication、typed Run、每个登记归档的字节数与SHA；独立读取新 Milvus 行和 float32 摘要，核对旧集合行数和未知归属集合原ID |
| 服务停止后的终态与历史 | PASS；同一 crosscheck 第二轮。实际停止 Leader Milvus，PUBLISHED 与 COMPLETED_NO_CHANGE 的 continue/abandon 返回原结果且不回拨后来版本，runtime_factory 未调用；原源文件已删除的 genuine v7 历史引用逐项相同 |
| 环境切换前真实 worker 退场 | PASS；R14-leader-real-worker-exit.json 和 lifecycle-worker-exit.json。4个与1个实际 worker 身份正常 idle 退出后才进入下一套 GPU 环境 |

最终独立 wheel：87c85c0b0e2f86fae95d1c9e841a66a00958cc5f763073c3b2a726d7abeea428；sdist：342a3a596551cef51b68b03191c3f1d8847f65a6d509fe52f77aa73b0377b77f；宿主 wheel：1ac2f7c8ca498bfe749f1458b990557f8cc98024f5b8509a1f30b5f1c187ebe5。三者与执行者交付一致。Leader 全部测试命令在最终冻结生产字节上运行，结束后再次验证102交付文件不变。

合计独立回归 1337 passed / 32 skipped。29 个 core 跳过是既有显式 CUDA/Milvus opt-in；宿主3项是无API key的网络用例和Windows/系统文件条件项，不计通过。本项真实 GPU/Milvus 由独立13+6场景和旧包升级分别证明；不声称执行全部旧 opt-in 或新的回答网络验收。

## 失败记录与边界

执行者五条早期失败全部保留：定向回归首轮、real前三轮、upgrade首轮。第三轮 real 的旧 RUNNING JSON 是 exit1 命令的部分快照，不是通过结果或仍在运行；对应 .pending 仅是待独立清理临时物，正式JSON/日志/命令留存。

Leader 附加 crosscheck 首轮 exit1，见 R14-leader-crosscheck.json：已完成20数据库、归档和真实物理检查，随后脚本错误地从 ImportBatch DTO 读取不存在的 owner_nonce，未完成离线回放。finally 已恢复专属服务。保留首版脚本 SHA 7c5d8e4e63e28cf963d1d0a3ef5d46d467e6a3370ef9536f3de67e477f88a361；改为读取实际 mutation_batches nonce/epoch 后从头重跑，第二轮完整 PASS/exit0。没有为此修改生产代码。只读 PowerShell foreach 管道语法定位错误已纠正，不作为产品测试失败或通过证据。

真实 late_model 使用已由 GPU 计算产生、尚未送达句柄的 finished 帧受控延迟；不宣称 GPU 在帧产生后仍计算。late_insert 是通过 owner/physical/IO 登记后才暂停真实 SDK，换代发布后旧调用只写旧 Collection。PyMilvus drop 没有 expected-ID 原子条件；内部名称不复用和生命周期互斥不能解决任意外部管理员在 describe/drop 或 SDK 重试间替换集合的竞态，无可靠证据则保留。

## 保护与后续

R14-leader-precheck.json 独立复核6553保护输入、202原生产文件、127原scoped文件、SQL1–7、冻结spec、R13后记、411 ignored条目、HEAD/分支/空index；仅任务内授权差异。三个 ignored 工作树目录另核对存在。独立回归没有修改冻结102文件。保留已知共享uv目录size-only历史观察，不推断原因，不冒充模型内容全量哈希检查。

Leader 根 C:/Users/18221/AppData/Local/Temp/codeplus-r14-leader-20260922，Milvus19539/health9100；Executor 根 .../codeplus-r14-executor-20260922，Milvus19538/health9099。二者全部临时环境/数据/脚本/日志和专属容器/卷/网络交给新独立清理会话，保留所有正式证据、共享资源、外来服务、继承评测/compose/用户README与其他任务目录。清理前后冻结和复核另记录。

R14 提交后、R15 写入前进入用户另行授权的 W1 收敛窗口；候选为独立任务的 batch1-v2 patch（f8fd0f17a0a5db02c800bcf847ac659ac607eae205020f25d5a864057d80fd40），尚未应用，须另行独立接入验证。R00–R26 全目标继续；本记录不表示 R15 GC、R16切换、R21产品命令、R23人工校准或整体目标已经完成。

## 独立清理与最终批准

独立清理已移除两个本项临时根、6容器/6卷/2网络、1863内部reparse节点、583729-byte的第三次real临时.pending及自己的辅助根；四端口无监听，没有终止进程。143审阅文件、206生产文件、6553保护输入、102执行交付、7旧SQL、7spec、6个R13后记、411 ignored项均匹配冻结值。外来2容器/5卷/4网络保持。清理未改源码、正式测试、旧报告或.gitattributes，没有stage/commit/push。

Leader随后独立重新计算冻结文件SHA、检查3个ignored目录实际存在、HEAD/分支/空index、所有目标根/.pending/端口及完整外来Docker稳定字段，并独立读取前后两份完整共享gzip：172661节点无新增删除；6139项变化全为uv/cache普通非reparse目录的size，其余字段、所有普通文件和model/core/CUDA/worker元数据均相同。原因未知；原始严格全等after FAIL保留，不宣称共享元数据完全相等或模型内容全量SHA通过。

清理报告R14-cleanup.json SHA256为468998acf2e2f319c9a295ac6d93a873b593fc001b93fa269df83ee3c335ad51；Leader独立复核R14-leader-postcleanup.json SHA256为a5e34408c4bfccfc5748ffd22efc814e48c2f5fcb4005b869cd8ce5c33f5f5f6，状态PASS_WITH_METADATA_OBSERVATION。清理的只读PowerShell解析错误和严格元数据FAIL分别留存。Leader冻结审计首轮遗漏已授权checklist变化、第二轮误计已退出CIM采样子进程，均保留原FAIL；第三轮完整冻结通过，未修改产品实现。

全部R14必要验收已完成，批准精确本地提交。R13六个提交后记按既定约定随本次正常提交保存，不amend；继承eval/compose/用户README和外部收敛任务目录不纳入提交。后续仍按W1→R15→W2→R16–R26推进。

## 本地提交后记

实际提交175f8f54ed9e24736a232f6b989d145f7f217dca，parent a4fede1c3f0a09202710e53debdbac3307c0911d；161个精确路径与Git blob一致，index空，206生产文件与继承保护输入保持。详见R14-leader-postcommit.json。提交前仅移除r14-compose.yaml末尾2个空行字节，前缀字节完整保留且两份docker compose config解析结果相同；原infra/unit失败日志保留原字节，.gitattributes只加两条精确文件例外。首次和第二次cached空白检查失败及混合LF/CRLF的辅助脚本断言失败均保留于R14-leader-stage-first-fail.json、second-fail.json和R14-leader-whitespace.json；最终cached check通过，产品代码没有变化。本后记与实际SHA/最终辅助清理记录随下一次正常W1提交保存，不amend，不push。R15尚未获写权。
