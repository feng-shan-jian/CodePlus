# 领域对象与配置契约

本文记录领域类型、配置装配、快照指纹和模型协议。存储、输入归档及解析契约分别见[存储与并发](storage-and-concurrency.md)、[输入快照](input-snapshots.md)、[来源映射](parsing-and-source-maps.md)。

## 记录和身份

`domain.py` 的 KnowledgeBase、Document、DocumentVersion、Section、Chunk、KnowledgeRevision、RevisionMember、IndexArtifact、ImportBatch、ImportItem、Run、RunPin、SourceRef、Evidence、Citation 使用显式 UUID。名字、路径、原件 hash 都不是文档身份：不同路径同内容保留不同 document_id，相同身份的后续内容形成新的 document_version_id。存储层负责跨对象外键、库归属与事务，类型本身不声称已经验证数据库存在性。

所有记录使用 Pydantic 的 frozen/strict/extra=forbid，嵌套对象也冻结，集合用 tuple；JSON 数组读回 tuple，不保留外部 dict/list 引用。`model_validate_json` 是 JSON 入口，Python 构造需实际 UUID/枚举/datetime。`model_copy(update=...)` 重新验证，拒绝绕过不变量的修改。禁止把 Pydantic 的 `model_construct` 或 `object.__setattr__` 当成外部数据入口。未知版本/字段、布尔冒充整数、非法 SHA256、NaN/Infinity、非正区间均拒绝。

Span 使用解析文本的 Unicode codepoint 半开区间 `[start,end)`；多个区间须有序且不重叠，不是原始字节 offset。R08 已提供原件字节映射与文档内范围核验；空文档没有非空证据区间。DocumentVersion 要求带时区的采集时间与结构化来源元数据。批次发布状态必须有 published_revision_id，待恢复必须有原阶段；类型检查本身不替代存储事务和后续发布流程。

Run 保存完整解析后的 RunConfiguration 与其 hash，任务只绑定一个 kb_id/revision_id；completed、partial、incomplete、failed、cancelled 与 stop_reason 校验一致。旧运行保留 token_budget 等历史停止原因；未知 token 用量为 null，不伪装成 0。Evidence 表示可信交付回执登记的正文，必须含 delivery_id；单纯构造该对象不授予引用权限，R11/R12 的存储与回执路径仍是权威。历史 Citation 与新 evidence 标记均通过归档读取。

`BudgetStopReason` 保留旧记录的停止原因读取；当前仅单次返回上限使用 `context_limit`，没有跨调用预算执行。`budget` 仍仅用于 incomplete，completed 仍仅接受 finished。`Record.model_copy` 已执行完整验证，`RunLease.finish` 在 SQL 更新前拒绝非法组合。此次不改变记录字段、ErrorInfo 线格式或 SQLite schema，旧合法记录继续可读；新增原因会被旧开发版本的严格 Run schema 拒绝，不承诺将新运行记录降级给旧包读取。

统一 ErrorInfo 包含 code、stage、message、retryable、可选模型 request_id 与宿主 call_id；RagError 携带此记录。空结果不作为错误替代品。错误不得携带密钥；适配器应在生成 message 前脱敏。

## 显式装配和作用域

`assemble_configuration(defaults=..., configured=..., explicit=...)` 接收知识配置映射及可选的同级 `worker` 设置。优先级由低到高是调用方默认、持久功能配置、本次显式装配值；未出现的字段使用 Schema 默认。递归合并对象，数组整体替换，null 是显式值；profile 数组整体替换防止跨来源拼出混合模型。`origins` 只记录调用方提供的字段叶节点和数组，不伪造 Schema 默认来源；省略的 `retrieval.mode` 是 Schema 的 auto。对象替换标量/null 后删除旧父节点来源。

核心不读文件、环境变量、CodePlus 全局配置或当前目录，不解析用户默认路径。适配层选取知识库功能配置后传入单一 Schema。StorageConfig 拒绝相对路径、上级路径段、UNC 与 URI 中的认证/query；credential_ref 仅为名字，不是密钥。它是跨平台字符串 Schema：例如 `/tmp/data` 可表示 Linux 绝对路径，不能因此推断在 Windows 已解析成正确本机绝对路径。R06 打开目录前必须按实际 OS/文件系统重新确认绝对本地路径、权限、符号链接/挂载与数据归属；R05 不创建它。

`resolve_run(config, 'qa'|'report', RunOverride(mode='fixed'|'auto'))` 冻结本次 retrieval 与任务类型。单次用户覆盖只接受 mode；不改默认配置，不接受宿主权限模式、top_k、模型或库。fixed 固定路线并保留多轮搜索/阅读，auto 允许选择 strategy 和 rerank；两者都不改变配置中的候选及单次返回上限。

候选/context、RRF、parser/chunker/index 参数由功能配置提供。选定参数见 [retrieval-selected.json](retrieval-selected.json)，通过现有 assemble_configuration 合并。旧 budgets/run budget 字段只在兼容读取边界保留其原始数据，不生成活动执行配额；旧快照与运行身份保持可读。

下面为可运行的 Schema 示例，数值仅展示装配，不是推荐或冻结参数：

```python
from uuid import uuid4
from agentic_rag.config import assemble_configuration, resolve_run, RunOverride, ProcessingSnapshot

data = {
    "storage": {"data_dir": "C:/example/knowledge", "milvus_uri": "http://127.0.0.1:19530", "namespace": "example"},
    "processing": {
        "parser": {"implementation": "markdown_txt", "version": "experiment-1", "normalization_version": "experiment-1"},
        "chunker": {"implementation": "structure_sentence_token", "version": "experiment-1", "max_tokens": 400, "overlap_tokens": 40},
        "index": {"schema_version_name": "experiment-1", "nlist": 1, "bm25_k1": 1.2, "bm25_b": 0.75},
    },
    "models": {"embedding": "local_embed", "reranker": "local_rank"},
    "model_profiles": [{"name": "local_embed", "capability": "embedding"}, {"name": "local_rank", "capability": "rerank"}],
    "retrieval": {"route": "dense", "rerank": False, "dense_candidates": 20, "bm25_candidates": 20,
                  "rerank_candidates": 20, "rrf_k": 40, "nprobe": 1, "context_chunks": 5, "context_tokens": 3000},

}
config = assemble_configuration(defaults=data).knowledge
snapshot = ProcessingSnapshot.capture(uuid4(), config)
run = resolve_run(config, "qa", RunOverride(mode="fixed"))
assert config.retrieval.mode == "auto" and run.retrieval.mode == "fixed"
assert ProcessingSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
```

## 模型约定与兼容身份

当前 backend 只允许 `qwen3_local`，profile 的 capability 必须与 models 选择一致。模型/tokenizer 精确 revision、tokenizer/config/vocab/merges hash、完整模板、1024 维、最后有效 token pooling、float32 L2、yes/no 分数及 bf16/SDPA/cuda:0 取自 R03。未知 backend/API/CPU、未验证 revision 或 tokenizer/template 组合直接失败；没有任意 URL 接入。instruction 可显式改写但成为新试验身份，不继承 R03 合成样例质量结论。

R03 保守输入上限为完整 2048 token，batch<=4，`batch_size * max(complete_tokens)<=4096`；Schema 可配置更小上限，不能扩大已测包络。必须计入标题、query、instruction 和特殊 token；文档模板与证据正文分离。`validate_input_batch` 只校验适配器提供的实际计数，R09 必须用各自 tokenizer 完整计数且不截断。分块 max_tokens 小于输入上限也不证明加模板后可用。

指纹算法为 UTF-8、排序键、无多余空格/NaN、保留数组顺序的 JSON；外层包含 fingerprint_version=1 与 scope，取 SHA256。ProcessingSnapshot 保存完整配置并验证以下指纹：

| 指纹 | 包含 | 排除 / 意义 |
| --- | --- | --- |
| config_fingerprint | 完整已解析配置，含实际 profile 及旧快照兼容字段 | 不只保存可变名称；用于恢复和审计 |
| document_encoding_fingerprint | parser、chunker、实际 embedding/tokenizer/模板/runtime/输入限制 | 排除 profile 显示名、Rerank、预算、存储、检索路线 |
| index_fingerprint | 文档编码指纹与索引/analyzer 配置 | 只变 BM25 参数改变索引身份、不改文档编码身份 |
| profile.identity | 当前完整实际模型 profile，含运行实现/设备与输入限制 | 排除显示名称；作为 worker 实例/请求身份 |

同名 profile 更改实际配置会改变身份；只改名称不改变编码身份。拒绝未支持的 tokenizer/template/revision 比误认兼容更早失败。保守地把 query instruction、设备/运行限制纳入 embedding 身份，未声明它们跨配置可兼容；未来若放宽需版本化兼容证据。旧预算字段或 Rerank 的变化不改变文档编码/索引身份。模型切换使用这些边界决定是否需要重建确认。

R07 InputManifest 仅标识本批固定请求；InputCheckpoint/RawSnapshot 另存逐文件完整原件与错误，不替代要求 parsed/source_map 的 DocumentVersion。批次仍直接关联此处完整 ProcessingSnapshot，重开/接管不读取新默认配置。变化对照使用基准发布成员与该版配置，编码不兼容给 requires_rebuild_confirmation；不存在“原件 hash 一样就一定不用重建”的简化。R08 真实执行仅接受 PARSER 常量与 canonical-offsets-v1 chunker 身份，其他实验名被 Schema 接受也不能据此运行。真实处理复用既有 ImportItem/CheckpointArtifact 语义，stage 不在其他表重复维护。

## 能力协议与安装边界

EmbeddingProvider 的 embed_documents/embed_query、RerankProvider 的 rerank 接收显式 profile 和 RequestContext；候选和响应使用 UUID，不按排序位置猜来源。响应校验请求/模型身份、ID 集合、embedding 顺序、1024 维有限归一化向量、Rerank `[0,1]` 分数及稳定排序，记录 queue/load/inference 用量。错误明确 stage。缺少提供方时 `require_provider` 返回 CAPABILITY_UNAVAILABLE；`require_optional_dependencies` 只用 find_spec 检查依赖，不加载 GPU，缺依赖返回 DEPENDENCY_UNAVAILABLE，依赖存在也不证明设备或模型可用。

RequestContext 使用 `time.monotonic_ns()` 域的绝对 deadline_monotonic_ns 作为同机临时硬时限，可选 deadline_at 仅作带时区审计。不能跨重启复用单调值，R09 握手必须确认同机/同启动时钟域，排队/加载/推理共用同一截止点；R05 不实现调度或取消。

发行包含 `src/agentic_rag` 的 Python 源码、模型资产锁和全部 SQL 迁移，以及 MIT 许可证和 metadata；sdist 另含 pyproject、README、uv.lock 与 Windows CUDA 依赖锁。tests、eval、docs、Compose、缓存、权重及用户资料不发布。`tests/test_package_install.py` 核验 wheel、sdist→wheel 的源码和资源集合，并在仓库外干净环境检查安装与核心行为。核心包不强制安装 Torch、CUDA、Milvus 或 CodePlus 宿主。
