# R10 首次索引发布、Dense 与完整语料基线
执行会话 `/root/r10_publication` 已实际创建；2026-09-22 Leader 登记台账后发送 WRITE_GRANTED，授予本任务唯一写权限。Leader 与只读评审不并行写入、运行测试或使用GPU。
前置R00–R09均COMMITTED，唯一集成基线为 codex/rag @ 9afe7e37982cf63c3e2199403499b289d8a520b1（R09，90精确文件，Leader251核心+6真实GPU通过）。Leader完成Git真实路径/parent/index核对。执行会话由Leader创建并显式授予唯一写权限后开始写入；Leader独立验收与本地提交，执行者不stage/commit/push；不动继承6553保护路径、根依赖/README/未跟踪compose/旧RAG，不建第二Agent或产品CLI。读用户约束、task-plan流程及R10/R11/R13–R16/R17/R23、checklist通用/专属、D03/D12/D38、T03/T06、R01/R02/R06–R09正式证据。

2026-09-22 交接勘误：下文8db278...为 `eval/runtime-inputs.json` 数据清单及 `INPUT_SHA256`，并非 Python 文件字节哈希；`eval/runtime_inputs.py` SHA256 为 `f2550c8ae6341f4f30f12fdc8b11aaf13b39b32def5a2d7f41b54429fd75ec3a`。二者均保持原 R01 输入边界。执行者以清单109文件、manifest SHA256 `1c18dcb30a653e40a5e66ad27ebc30ca2b633eaeb1daa4d712d3a1b1aab0f02b` 明确 STOP_WRITE 后，Leader 接回文件/测试/GPU/服务唯一写权限；独立验收从全新自有根和新服务开始。

允许独立src/agentic_rag/indexes/（按实际骨架）、retrieval/、ingestion/首次构建协调、storage新增publication/migration及必要catalog/ownership接口；复用既有IndexArtifact/KnowledgeRevision/RevisionMember/RunBinding/Mutation，不重复造业务状态机。配置/域增量兼容历史快照或显式迁移；SQL1/2/3 byte不变。独立依赖+milvus extra和包锁、正式tests、独立eval内部runner及R10正式文档/报告。若需要修改eval/RAG-eval/run.ps1/check.py这一R01公开接点，先交具体hunks说明与Leader核对冻结契约/旧清理交接；不能更改gold/scoring语义或旧Agent入口。

实际输入必须来自R01 eval/runtime_inputs.py（frozenSHA8db278a920b290ea9d6f63d5bea473960fb20c26fcb41c5078dececa421d4d0d），only corpus_paths+IDs/queries；全部609MD经正式R07 capture+R08 process_inputs+R09真实GPU提供方。R08基线4041chunks/6,456,520CP，仅参考重复性，不能读取gold或R08结果替代生产处理。开发200/177有gold/23null；官方TopK10用于评测，产品Context8及预算另域。runtime不import scoring；独立score进程读gold。失败逐题保留、不得过滤分母，保存完整requesterror。

R06当前仅candidate PREPARING登记；不存在生产index_artifacts/publish记录API，旧READY测试是直接SQL合成，不能当已实现。建立真实索引产物/校验凭据、状态、发布记录+libraries.current_revision原子短事务，核对owner epoch/baseCAS/snapshot/manifest，发布响应丢失按batch/revision读权威记录幂等核对。解析、编码、MilvusIO和全量文件hash不占SQLite长写锁；真实独立进程验证其他库读写。候选独立Collection包含完整成员，只有真实flush/index完成/load/Dense和BM25底层验证通过才切当前指针；首库失败明确未就绪。发布后服务失联不回滚指针、不把已发布当未发布候选删除。

Milvus使用R02实际版本组合v3.0.1/pymilvus3.0.2，正式适配不import probes。Probe是4D synthetic、int64/VARCHAR4096，生产必须真实1024/UUID与版本来源字段，禁动态字段、Strong、同collection原生BM25 standard/lowercase SPARSE_INVERTED/DAAT_MAXSCORE。nlist/nprobe明示实验选择并记录，不把probe1当最优参数。Collection物理名绑定namespace/kb/revision/buildepoch且仅操作模块owned资源，无alias/sharedcollection统计。查询固定RunBinding revision+编码快照不读最新默认，返回实际候选IDs/来源/version/分数与完整处理span；原文从该版canonical archive。

索引文本必须明确UTF8 bytes限额；512模型token不能证明VARCHAR4096足够，不能静默截断。任何indextext转换都须固定身份并保持引用原文。真实读取全部行（iterator非默认limit）与expected ID集、text/source/config hash/vector1024+finite+norm逐条核对，indexed_rows/total_rows/pending0及sealed reload证明实际index完成；正常空结果≠数据库失败。

只用本任务独占Composeproject/ports/volumes，启动前核对现有容器和归属。原untrackedcompose不可动，不prune/stop其他SonarQube/Postgres。可复用正式fixture资源约定但实际schema/运行走生产。记录专属服务真正restart后查询；健康与数据面就绪分开，有限恢复wait仅在restart阶段，同endpoint无fallback。测capture/parse/encode/insert/flush/index/load/validation/publish、cold/warm、实际模型queue/加载、archives/SQLite/temp/volumesallocated（含WAL/元数据，不冒称单collection净大小）。

验收：首次609全量+开发集真实Dense基线；实际候选失败/异常集合/索引超时不发布；缺服务不返回空成功；原子publish前后真实进程kill/响应丢失readback不重复；真实新客户端及服务restart固定版query+source；scope/hash/install core regressions与新增migration真实升级。R13普通部分成功/更新、R14完整恢复UI、R15GC、R17BM25产品路线未实现不暴露为可用，但接口保留同一state machine扩展位置。

交付R10.md命令/cwd/env/spec/data/model/codehash/exitcodes，逐项5条及G/C，失败尝试与修复、真实Milvus/GPU/多进程/安装与单测分别标清；正式rawobservations和precisefilemanifest/hash。无写权交接前Leader不并行GPU/测试。临时ownedroot按安全无reparse遍历清理，weights/env用户语料不进Git。先回复结构、schema/发布原子性方案与实际环境，必要只读评审后实现；结束明确停写/GPU交权。

前置只读接口核查（/root/r10_readonly_publication_review，仅静态）：Mutation.close当前无条件require(ACTIVE)，需正确识别同owner已PUBLISHED终态安全释放，失权仍拒绝；publish重试应先读原批次权威receipt，后续已发布其他版时也只能返回旧receipt不能回拨pointer。历史SQL1/2/3字节不改，追加migration4；旧人工READY记录没有可信artifact不能伪造迁移为已发布。候选清单/hash须由read_processed完整验证结果派生，不能接收任意passed=True凭证。R02snapshot最多3000行，不能用作4041chunks全量校验。固定run可保持现有公开RunBinding结构并用内部不可变artifact绑定，或明确同步调用方/存储迁移；不要为字段重复而破坏历史run兼容。


当前环境需开工复核：Windows pwsh7.6.5，core C:/Users/18221/.cache/codeplus-agenticrag/venv-win-core（Python3.14.3、editable当前包、无Torch）；CUDA同父venv-win-cuda（最终R09 wheel非editable，Torch2.14.0+cu130、Transformers5.17.0）；固定models同父目录，RTX4070Laptop8188MiB。当前协调目录无active，仅稳定锁。R09 Leader实际wheel 3a8603ddb94d51f9bdd0a2dd011b493c34b97bd3d20f38c3b81f045c17825990、sdist a1537562bd5ebe16834988aaf601aa2703ef5c9307d92cfd153ee2e7a3eef007。代码/依赖变化后验证core和目标worker同实现摘要，不用旧驻留实例假称新包。R02资源已清理，Docker既有其他项目容器必须保护。Leader当前未提交改动只有R09提交后真SHA台账/后记与R10分派/只读前置报告，不由执行者覆写。实际数据/资源所有权基线自行精确保存。
