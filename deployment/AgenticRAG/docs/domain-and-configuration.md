# R05 领域对象与配置契约

2026-09-22；记录 schema_version=1，开发发行 codeplus-agentic-rag 0.1.0。本文记录 R05 数据类型、装配、指纹和模型能力协议；R06 已增加独立的 [存储与并发基础](storage-and-concurrency.md)，R07 增加 [输入快照](input-snapshots.md) 与 SQLite schema 2，原领域／配置类型接口保持不变。解析器、worker、检索和宿主接入仍待后续任务；R05 类型本身不做索引重建或文件系统写入。

## 记录和身份

`domain.py` 的 KnowledgeBase、Document、DocumentVersion、Section、Chunk、KnowledgeRevision、RevisionMember、IndexArtifact、ImportBatch、ImportItem、Run、RunPin、SourceRef、Evidence、Citation 使用显式 UUID。名字、路径、原件 hash 都不是文档身份：不同路径同内容保留不同 document_id，相同身份的后续内容形成新的 document_version_id。存储层负责跨对象外键、库归属与事务，类型本身不声称已经验证数据库存在性。

所有记录使用 Pydantic 的 frozen/strict/extra=forbid，嵌套对象也冻结，集合用 tuple；JSON 数组读回 tuple，不保留外部 dict/list 引用。`model_validate_json` 是 JSON 入口，Python 构造需实际 UUID/枚举/datetime。`model_copy(update=...)` 重新验证，拒绝绕过不变量的修改。禁止把 Pydantic 的 `model_construct` 或 `object.__setattr__` 当成外部数据入口。未知版本/字段、布尔冒充整数、非法 SHA256、NaN/Infinity、非正区间均拒绝。

Span 使用解析文本的 Unicode codepoint 半开区间 `[start,end)`；多个区间须有序且不重叠，不是原始字节 offset。原件字节映射与文档内范围核验留 R08；空文档没有非空证据区间。DocumentVersion 要求带时区的采集时间与结构化来源元数据。批次发布状态必须有 published_revision_id，待恢复必须有原阶段；这些是记录一致性检查，实际状态迁移/CAS/事务仍属后续实现。

Run 保存完整解析后的 RunConfiguration 与其 hash，任务只绑定一个 kb_id/revision_id；completed、partial、incomplete、failed、cancelled 与 stop_reason 校验一致。无可交付内容的 incomplete 同样保留 token_budget 等具体预算原因；未知 token 用量为 null，不伪装成 0。Evidence 表示可信交付回执登记的正文，必须含 delivery_id；单纯构造该对象不授予引用权限，R11/R12 的存储与回执路径仍是权威。Citation 的跨对象/原文摘录核验尚未实现。

统一 ErrorInfo 包含 code、stage、message、retryable、可选模型 request_id 与宿主 call_id；RagError 携带此记录。空结果不作为错误替代品。错误不得携带密钥；适配器应在生成 message 前脱敏。

## 显式装配和作用域

`assemble_configuration(defaults=..., configured=..., explicit=...)` 仅接收 `knowledge` 内的映射。优先级由低到高是调用方默认、持久功能配置、本次显式装配值；未出现的字段使用 Schema 默认。递归合并对象，数组整体替换，null 是显式值；profile 数组整体替换防止跨来源拼出混合模型。`origins` 只记录调用方提供的字段叶节点和数组，不伪造 Schema 默认来源；省略的 `retrieval.mode` 是 Schema 的 auto。对象替换标量/null 后删除旧父节点来源。

核心不读文件、环境变量、CodePlus 全局配置或当前目录，不解析用户默认路径。适配层选取知识库功能配置后传入单一 Schema。StorageConfig 拒绝相对路径、上级路径段、UNC 与 URI 中的认证/query；credential_ref 仅为名字，不是密钥。它是跨平台字符串 Schema：例如 `/tmp/data` 可表示 Linux 绝对路径，不能因此推断在 Windows 已解析成正确本机绝对路径。R06 打开目录前必须按实际 OS/文件系统重新确认绝对本地路径、权限、符号链接/挂载与数据归属；R05 不创建它。

`resolve_run(config, 'qa'|'report', RunOverride(mode='fixed'|'auto'))` 冻结本次 retrieval 与相应预算。单次用户覆盖只接受 mode；不改默认配置，不接受宿主权限模式、top_k、模型或库。fixed 固定路线并保留多轮搜索/阅读，auto 允许的工具选项由后续 R19 决定；R05 只提供配置，不声称检索路线已可执行。

数值预算、候选/context、RRF、parser/chunker/index 参数均要求显式试验配置，parameter_status 目前只允许 experiment。R03 已测模型输入边界与这些质量参数分开；R23 才冻结质量与生产预算。parser/chunker 的实现与版本是快照身份，R05 没有对应执行器，不能因字段被接受就声称解析实现可用。

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
    "budgets": {
        "qa": {"searches": 3, "opens": 3, "total_tokens": 12000, "duration_ms": 120000,
               "finish_reserve_tokens": 3000, "finish_reserve_ms": 20000},
        "report": {"searches": 7, "opens": 9, "total_tokens": 36000, "duration_ms": 360000,
                   "finish_reserve_tokens": 6000, "finish_reserve_ms": 40000},
    },
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
| config_fingerprint | 完整已解析配置，含实际 profile 与预算 | 不只保存可变名称；用于恢复和审计 |
| document_encoding_fingerprint | parser、chunker、实际 embedding/tokenizer/模板/runtime/输入限制 | 排除 profile 显示名、Rerank、预算、存储、检索路线 |
| index_fingerprint | 文档编码指纹与索引/analyzer 配置 | 只变 BM25 参数改变索引身份、不改文档编码身份 |
| profile.identity | 当前完整实际模型 profile，含运行实现/设备与输入限制 | 排除显示名称；作为 worker 实例/请求身份 |

同名 profile 更改实际配置会改变身份；只改名称不改变编码身份。拒绝未支持的 tokenizer/template/revision 比误认兼容更早失败。保守地把 query instruction、设备/运行限制纳入 embedding 身份，未声明它们跨配置可兼容；未来若放宽需版本化兼容证据。单改 QA/报告预算或 Rerank 不改变文档编码/索引身份。R16 再用这些边界提示重建并执行确认流程，R05 不自行重建。

R07 InputManifest 仅标识本批固定请求；InputCheckpoint/RawSnapshot 另存逐文件完整原件与错误，不替代要求 parsed/source_map 的 DocumentVersion。批次仍直接关联此处完整 ProcessingSnapshot，重开/接管不读取新默认配置。变化对照使用基准发布成员与该版配置，编码不兼容给 requires_rebuild_confirmation；不存在“原件 hash 一样就一定不用重建”的简化。配置字段接受实验 parser/version 也不代表 R08 对应执行器已完成。

## 能力协议与安装边界

EmbeddingProvider 的 embed_documents/embed_query、RerankProvider 的 rerank 接收显式 profile 和 RequestContext；候选和响应使用 UUID，不按排序位置猜来源。响应校验请求/模型身份、ID 集合、embedding 顺序、1024 维有限归一化向量、Rerank `[0,1]` 分数及稳定排序，记录 queue/load/inference 用量。错误明确 stage。当前没有实现者；`require_provider` 返回 CAPABILITY_UNAVAILABLE；`require_optional_dependencies` 只用 find_spec 检查依赖，不加载 GPU，缺依赖返回 DEPENDENCY_UNAVAILABLE，依赖存在也不证明设备或模型可用。

RequestContext 使用 `time.monotonic_ns()` 域的绝对 deadline_monotonic_ns 作为同机临时硬时限，可选 deadline_at 仅作带时区审计。不能跨重启复用单调值，R09 握手必须确认同机/同启动时钟域，排队/加载/推理共用同一截止点；R05 不实现调度或取消。

发行包含六个领域／配置 Python 源码、九个 storage Python 源码、五个 ingestion Python 源码、schema.sql/inputs.sql 两个迁移资源、MIT 许可证和 metadata；sdist 另含 pyproject/README/uv.lock，以及 Hatch 为重建保留的包级 .gitignore 排除清单。tests、probes、eval、docs、Compose、缓存、权重及用户资料不发布。真实 package-install 测试构建直接 wheel 与 sdist→wheel，用两个新环境和非仓库 Unicode/空格 cwd，清空 PYTHONPATH，以 `python -I -B` 核验 site-packages 导入、metadata、完整配置／存储／原件快照调用、缺依赖诊断及未加载 GPU/宿主。构建工具采用独立核心环境的锁定 Hatchling，不依赖根打包配置；运行环境仅安装锁中 Pydantic 与 APSW 核心依赖。GPU、Milvus、Linux/宿主联装及最终同一实现迁移尚未验收。
