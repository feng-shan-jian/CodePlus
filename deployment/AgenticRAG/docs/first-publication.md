# 首次索引发布与 Dense 开发基线

R10 使用已有捕获、处理检查点、共享模型 worker 和库级 Mutation。独立核心提供首次完整建库及固定运行版 Dense 候选；没有另建 Agent、产品 CLI、旧库兼容层或第二套生命周期。原生 BM25 已用于底层发布验证，产品 BM25/Hybrid 路线仍由 R17 实现。R13 的更新和部分成功复用本协议，见[普通增删改](ordinary-mutations.md)；恢复交互与回收由 R14/R15 承接。

## 编码和物理索引

`indexes/manifest.py` 从 `read_processed` 重新验证的完整输入派生文档成员和 Chunk 清单。原文摘录、标题检索文本和模型模板分离；Embedding 仍调用冻结 profile 的标题/正文接口。BM25 索引文本固定为非空标题加换行和 canonical body；转换身份 `heading-newline-canonical-body-v1` 写入 artifact schema 指纹。UTF-8 限额明确为 65,535 bytes，超限错误，不截断。模型 token 限额不替代数据库字节限额。

每个版本使用独立 `ar_<namespace>_<store UUID>_<kb UUID>_<revision UUID>_e<owner epoch>` Collection。UUID 使用 VARCHAR36，来源/文本/编码/float32 向量 hash 使用 VARCHAR64，Dense 为1024维；禁用动态字段、auto ID 和alias。Strong读取，原生 standard/lowercase BM25 Function，IVF_FLAT/COSINE 与 SPARSE_INVERTED_INDEX/BM25/DAAT_MAXSCORE 同集合。完整语料试验 nlist=64、nprobe=64，是遍历全部IVF分区的开发基线，没有声称是最优ANN参数；Context8和官方Top-K10分属产品与评测配置。

所有写操作核对实际 catalog/store、库、批次、版本、epoch及当前PREPARING状态。旧 artifact字典不能授权写已发布集合。Milvus token仅由构造器运行时传入，与credential_ref分离，不写入任何schema、快照、日志或回执。

flush、两索引实际total/indexed/pending、sealed reload、loaded segment覆盖及真实Dense/BM25查询都在发布前校验。query_iterator分批读取完整集合，逐行核对全ID集、来源/版本/配置/文本hash、维度/有限数/L2和little-endian float32 SHA256；不使用默认query limit或R02的3000行快照。

短库不复制或填充行来满足索引阈值。R10 的实际 Milvus 3.0.1 对单条文本、纯标点和 Unicode 表情均返回两索引 indexed_rows=total_rows=1、pending=0 与 sealed segment。官方配置的 [1024 行阈值](https://milvus.io/docs/configure_indexcoord.md) 不能替代实际版本观测；本次未修改服务阈值，后续版本仍按实际索引完成条件验收。BM25 smoke 使用原有真实正文，并由实际 analyzer 判定 token 数；零 token 对应合法空结果，有 token 却空结果才判此 smoke 失败。Dense smoke 始终要求真实非空。

R13 删除最后成员时允许真实零行 Collection 和零 sealed segment；仍执行 Dense/BM25 空结果探针，不填充占位文档。上述非空 Dense 要求继续适用于有成员的版本。

## SQLite发布契约

仅追加migration4，原schema.sql、inputs.sql、processing.sql保持原字节。新增index_artifacts及不可变publications；旧人工READY元数据不伪造成可信发布。ProcessingSnapshot v1、RunConfiguration和原公共数据结构保持兼容。

候选及artifact登记为一次短事务，同批同revision重试幂等。归档读/hash、模型推理、Milvus写入/查询、proof序列化均在SQLite写事务外完成。验证入口调用正式适配算法，不接收任意passed标志或可替换子类签发的凭据。

发布事务检查owner、base CAS、snapshot、manifest和已验证artifact，原子写publications、current_revision_id和PUBLISHED批次。响应丢失后先查原batch/revision receipt；即使后续已发布新版，旧请求只返回旧receipt，不回拨指针。正常终态close只接受完全一致的原owner/epoch/receipt，失权仍报错。提交后的服务不可达是检索故障，不能回滚已发布指针或把索引当未发布候选删除。

`DenseSearch(catalog, run_id, provider, backend)` 从持久RunBinding解析revision→不可变artifact和该版编码profile。实际body、source URI、名称及spans来自对应DocumentVersion/canonical archive；源文件删除或新导入改名不影响旧运行。返回候选不等于R11已交付证据资格。

## 正式复跑入口

- `tests/test_publication.py`：纯合同/事务/传输替身，明确不作真实Milvus或语义质量证据。
- `tests/test_publication_milvus.py`：真实服务、真实生产Schema及显式合成1024维协议向量。
- `tests/test_publication_processes.py`：真实服务候选，真实进程终止提交前后；外部解释器在pending Milvus IO时读写另一库。
- `tests/test_publication_small.py`：不填充的单 Chunk 真实发布、纯标点/Unicode 的零 token BM25。
- `tests/publication_full_acceptance.py revalidate|restart`：对既有完整构建进行只读全量复核，或绑定同一持久运行，在新进程和真实服务重启前后核对候选、原文和来源。重启阶段允许同端点最多180秒数据恢复等待；正常检索不因此增加重试或回退。
- `tests/test_dense_runner.py`：隔离子进程故障注入，保留全部200条以及初始化/题中/暖查询/清理失败。
- `eval/dense_runner.py build|dense`：内部实验驱动，正式capture/process/R09 provider/首次build/DenseSearch；不是用户产品入口。只读R01 query-only manifest和外部指定开发IDs，不导入score，audit hook拒绝评测评分文件。
- `eval/RAG-eval/score.py --task retrieval --tier medium --input <report>`：独立评分进程，保留200/177/23及全部失败分母；官方token重合、原文span覆盖和回答正确性分别报告。
- `eval/score_dense.py`：评分专用进程调用未改动的 R01 `replay.score`，读取原题 gold 计算原文 span 覆盖；运行器与生产检索不导入它。

实际命令、环境、冻结代码hash、失败轮次和最终结果见R10实施记录。Linux、宿主Agent、完整恢复/GC/Hybrid/Rerank和最终质量门槛尚不由R10宣布通过。
