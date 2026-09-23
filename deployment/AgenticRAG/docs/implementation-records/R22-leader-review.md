# R22 Leader 独立验收

结论：**ACCEPTED，待精确本地提交**。基线 f0e1538beeedfe96d6be7ed99e422d4026ffc3b7，唯一工作区 D:/CodePlus / codex/rag。现有单 GPU worker 调度、截止时间和库生命周期满足本项已执行负载；生产实现、配置/profile、数据接口与历史 SQL 均未改。R23/R24 质量及总体验收仍未完成。

## 独立复验

Leader 逐项核对执行者9份非自身交付文件、最终 manifest SHA 和1689份原始证据。独立普通文件复制已结束的执行环境，重建两包 wheel/sdist、重新安装 core/cuda、pip check；143份宿主和74份RAG生产资源在源码、wheel和实际安装中逐字节一致。测试从私有 site-packages 加载，并记录实际模块SHA。host wheel 8029589bd17f6a52b70f352b0c92428ef693d629e2ce3786d9345928831b3ccb；RAG wheel e53c92bef0fe9523e22cfa93bfca7fc6f3bd4756ee12dd9eff5cb43a51ec8da2。

独立协议与需求性质 **24 passed / 0 skipped**；真实 GPU **8 passed / 0 skipped**。分层覆盖同级FIFO与4:1边界、两个真实宿主与实例复用、排队/运行取消、加载绝对截止、实际权重切换到期、队列满、真实32MiB分配上限OOM/恢复、断连/worker死亡、设备/依赖拒绝及闲置退出。受控引擎和注入故障未冒充正常GPU吞吐。原执行者0.15s截止未达到loading前提的失败及后续分层证据保留。

独立CUDA压力 **60/60**，另有2次预热；48前台、12后台，每个后台实际2×2048 tokens。双宿主各参与前后台，同profile同模型UUID、load_count=1。直接复算原始host trace与返回结果，FIFO及44个已观察后台排队的选择点满足额度；数据库审计和时间线复核使用独立脚本，不仅采信正式驱动PASS。

| Leader 实测 | 结果 |
| --- | --- |
| 前台 queued→validation P95 / max | 846.4 / 848.1 ms |
| 前台 submit→finished P95 | 877.5 ms |
| 后台完成间隔 max | 479.3 ms |
| 后台文档吞吐 | 4.737 docs/s |
| 压力前台/后台 allocated峰值 | 1145.2 / 1250.1 MiB |
| 真实Hybrid+Rerank并发搜索8次范围 | 4162.7–6378.2 ms |
| 整批129文件、257 GPU批次导入 | 175.756 s |
| 实际Agent报告 | 16102 ms；3803 tokens |

时间线使用同一OS的host接收时钟，包含IPC抖动；queue_ms另含输入校验。2ms观测余量不是CUDA时钟。热同profile压力、真实切换搜索、提供方/工具完整报告、整批发布是不同分母，不据此承诺全设备SLA。现有4:1与有限批次在本设备负载下有实测依据；GPU只在实际批次完成后让出。

## 实际产品与数据审计

私有 Milvus19555/health9116、RTX4070 Laptop、只读模型缓存和原批准回答提供方完成独立普通产品链。单次长导入修改Vega并添加128篇明确生成的负载文档；实际首个decoder层后启动现有CodePlus Agent报告，同时运行8次核心搜索。成功Agent来源调用、导入GPU窗口、不断推进的encoded计数与同一pending owner共同证明实际重叠；同库第二修改返回LIBRARY_BUSY。旧运行始终blue/harbor，发布后新运行red/mountain，文档ID相同而版本不同。

报告run 850408c5-6cf7-4316-814c-d79d52374092，completed/finished，2 search / 0 open；saved文件 982 bytes，SHA256 a4db59bc1f249774532fd001af300b77993840e41ba7fe33b72ceaef9471c1eb。程序保存和引用有效不替代语义质量与指令遵循，未把0次open称为打开成功。预算沿既有工程配置，无本项调大或强制额外工具调用。

Leader 对执行者及自身两条链分别用 SQLite mode=ro&immutable=1 与归档字节核对旧/新成员、raw/parsed/chunk哈希、引用区间原文、quote_hash、真实确认交付回执、报告逻辑与文件SHA、模型账本/实际计费及终态。数据库和sidecar原字节均未改变。真实reader取消时execution_finished=False且pin仍active，实际完成后pin才released，迟到结果未成为成功；最终active pins/pending readers均0，无pending mutation，全部worker正常idle退出。

## 审查结论与适用性

新增正式验收复用已有worker、GPU loader、Catalog、Milvus和Agent；观察器只记录真实消息，未替换输出或调度。性质判据检查FIFO、优先额度与后台完成，未复制scheduler选取算法作为期望。最终驱动收紧报告正常完成、实际tokens/两宿主、工具与编码真实重叠的断言，执行者旧原始结果离线通过，Leader则直接运行最终驱动通过。

没有生产变化，两路历史安装/冻结配置与11旧SQL的既有证据仍适用；本项独立构建安装验证当前包，没有无意义重跑相同包的所有历史矩阵。R20/R21语义反例继续作为R23校准输入，未改评分分母或答案提示。

G01–G03：前置提交、独占写权、冻结基线与分派卡通过。G04–G08：实际改动审查、全部R22条目、分层命令/环境、最终补丁适用性通过；原失败保留。G09：Leader与执行者的自有Python/端口均结束、三个容器停止、卷和私有证据保留，清理由用户另派。G10：说明、数量与已验边界一致。C01：ACCEPTED；C02–C05由精确stage、提交校验与提交后记兑现，不在此提前宣称完成。

执行者冻结6537继承路径；Leader只按授权推进其中阶段台账，其余6536、根双语README、官方语料与旧SQL保持。本次精确16路径，包括R21提交后记和R22分派卡。完整证据索引见 [独立验证](R22-leader-validation.json)，提交范围见 [提交前记录](R22-leader-precommit.json)。未push或发布；R23人审、指标与阈值冻结及R24总体验收继续推进。
