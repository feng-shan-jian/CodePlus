# 运行、读取与索引生命周期

R15 在既有 R14 所有权、实际 IO 和物理资源凭证上追加 schema9。旧八份 SQL 保持原字节；`lifetimes.sql` 只追加生命周期事实、GC claim/attempt 和维护游标。旧运行没有新凭证时保留占用，不根据旧 PID 或已过时间补造身份。

`start_run` 在 SQLite 短写事务中检查 current、READY 和编码身份，登记完整 revision 的 pin 及持有者的 nonce、实际进程启动身份和原生锁身份。GC 在相同 `BEGIN IMMEDIATE` 边界重新检查 current、active pin、非终态 mutation 的 base/candidate、revision dependencies、实际 reader 和旧 mutation IO，然后取得 RECLAIMING claim。物理服务调用在事务外执行。

`DenseSearch.search` 的 operation reader 跨越模型请求和 SDK 入场；终态 finish 不能在两者间撤掉保护。模型 reader 记录认证 hello 得到的 frozen RequestHandle 身份，并依据该句柄的 finished 消息或同一 worker 的实际死亡结束。`wait_finished` 也使用这个冻结身份，不读可能变动的 client metadata。

Milvus 正式适配器在 `search`、`read_vectors`、`inspect` 和 `validate` 的每次实际 SDK 调用周围登记 durable reader，覆盖直接 BM25 调用和 mutation 的向量复用。实际调用未发出时的参数错误，以及实际返回后本地校验错误，都不会制造未知 IO。已发出的 SDK 请求超时或断链则保留 pending；客户端死亡不能证明服务端已停止。Python operation 的死亡只结束本地跨调用占用，不替代 SDK 完成凭证。

finish 先登记运行终态；仍有真实 reader 或宿主 cleanup 时继续保留 pin。实际读取结束后，维护用同一持有者释放终态 pin。已知外部完成但 SQLite receipt 写入暂时失败时，Reader 保留原锁及本地完成证据，既有维护重试落库；进程丢失后仍按未提交凭证保守处理。Scope 在释放 cleanup、退役最后一个适配器前补写已认证句柄的完成，避免 finished frame 已到而后台 receipt 尚未落库的空隙。

自动维护在 Catalog 启动、正式适配器接入、发布、放弃和运行/读取释放时触发。没有可用适配器时只处理元数据，不导入 PyMilvus、连接服务或加载模型。接入适配器后，同一 Catalog 的后台维护每轮最多两次物理尝试；reader/pin 每轮各扫描 16 行，artifact 窗口最多 32 行。三种扫描均持久轮转，长期存活、未知或无物理凭证的队首不会挡住后面的记录。空闲续轮间隔为两秒，物理失败按 attempt 指数退避到最多 60 秒；显式 `maintain_indexes` 可立即重试，单轮上限 32。持续接入的适配器会继续推进积压，不要求新业务调用。

适配器按完整冻结 StorageConfig 匹配，多个同配置实例各自登记。`close()` 立即关闭新的业务 admission，并把 close 时已有的 artifact 范围交给同一个 GC 协调器有界退役。每轮最多两次物理尝试，跳过受保护或无凭证项；失败持久化后结束本次退役。`wait_closed(timeout)` 区分后台已安排和 transport 实际关闭。Scope 的迟到句柄先完成 receipt 和 pin 释放，再关闭唯一适配器；未知 SDK 不因关闭而改为完成，后续适配器接入/生命周期边缘仍可继续核验。

GC 复用 artifact 生命周期锁和 R14 的创建凭证：在删除前比较 endpoint、数据库、collection ID、创建时间及 description。并发 collector、失败重试与崩溃恢复还检查 claim nonce、进程启动身份和同一原生锁。已发布旧版可回收，但 immutable publication receipt、revision、全部文档原件/解析/结构及引用归档均保留。缺失实际集合可由服务确认 absence 后提交 RECLAIMED。

当前 Milvus SDK 没有按 expected collection ID 条件删除的接口。因此本模块锁能够排除本模块自身的竞争；外部管理员若在 describe 与 drop 之间替换同名集合，无法由该 SDK 提供跨系统原子条件。已发生且可观察的同名替换会保留资源并记录失败，不虚称能排除外部管理员的瞬间替换。

正式已发布查询仍要求 READY。历史引用和 `source_archive.read_version` 读取归档，不需要索引、原始文件、GPU、Milvus 或重新解析；GC 不删除历史。真实执行状态、已运行失败和已通过证据见 [R15 实施记录](implementation-records/R15.md)。
