# R08 解析、精确位置与完整处理检查点

2026-09-22；开发包 0.1.0、SQLite schema 3。正式实现为 `markdown-it-py==4.0.0`、`tokenizers==0.23.2`，只使用冻结 Qwen tokenizer 资产，不加载模型或调用 GPU。此文描述实现契约。

## 规范文本和原件

只接受严格 UTF-8（含可选开头 BOM）的 Markdown/TXT；无效 UTF-8、NUL 和其他 media type 返回文件级 `INVALID_INPUT`。不做替换解码。开头三字节 BOM 不属于规范正文；CRLF 和单独 CR 各变为一个 LF。其他 Unicode 码点、内部 BOM、Markdown 语法、空行及终止换行均保留，不执行 NFC、空白折叠或格式清洗。

`SourceMap.byte_boundaries` 长度为规范文本码点数加一：规范区间 `[a,b)` 对应原件字节 `[boundaries[a],boundaries[b])`，CRLF 的两个字节由同一个规范 LF 拥有。数组从 0 或 BOM 后的 3 起，到原件完整字节数止，严格递增；空文件/BOM-only 只有一个边界，不创建空 Span。完整原始字节仍由 R07 归档保存。SourceMap 同时绑定原件 hash/长度与规范 UTF-8 文本 hash。

证据区间统一 Unicode codepoint 半开区间；不是 UTF-8 字节、UTF-16 或 token 下标。`line_starts` 按规范 LF 建立，`lines(span)` 返回一基首/末行号，末行以 `end-1` 所在行计算；终止 LF 不被误认为下一行的证据。原字节范围回读后仅执行上述换行规则，即可逐码点对照规范原文。重复段落使用直接位置，未通过 `str.find()` 反推。

## 结构、章节和索引输入

Markdown 使用 CommonMark + table 规则；每个 token 的行 map 经自己构建的实际行起点转换为码点范围。保留 paragraph/list/list_item/blockquote/fence/code/table 等节点的 kind、嵌套 level、info、原文 span。父子块可能重叠，不把重叠结构节点重复拼入正文。TXT 按空行聚合段落，软换行保留在段落内。所有格式都保留 canonical 原语法。

`Section.span` 是标题开始至下一个标题开始的自身分区；无标题前言是根分区。标题路径表示当前最深标题祖先链。另存 `Heading.parent_section_id/level/subtree_span`：subtree 从本标题到下一个同级或更高级标题，包含全部子章节。R11 可据此导航父/子节点，按自身分区或 subtree 分页，并完整遍历；父内容没有因分区被丢弃。

空文件无 sections/chunks；纯空白或仅标题的分区仍归档并保留导航，标记 `no_body`，不生成模型输入。含正文的分区从规范范围开始处理，保留原标题语法；索引标题为 heading path 用 ` / ` 连接的显示文本，独立存入 `ChunkInput.index_title`。模型使用 `Title: {title}\n{text}`（无标题只用 text）。合成的 `Title:`、路径分隔符及拆分说明都不是证据原文。

## 分块和精确预算

执行顺序为章节范围 → 顶层结构块末端 → 句子边界 → tokenizer 候选边界。结构边界不单独把标题变为检索块。句子规则版本为 `canonical-offsets-v1`：中文 `。！？` 后可直接切分；英文 `.?!` 后需空白；连续空行也可作边界。它是确定性的轻量边界规则，不声称完整语言学断句。超长 fence/table/list 会拆分，`split_structures` 仅标记未完整结构，不插入正文或模板。

当前选定 `max_tokens=512/overlap_tokens=64`。max 是完整 Embedding 输入（标题、正文、特殊 token）的上限，同时不能超过冻结模型 profile 2048；64 是重叠正文重新编码后的 token 上限。选定参数见[选定配置](retrieval-selected.json)。实际片段每次重新完整编码、无截断；标题占满预算或单码点仍超限时返回 `INPUT_TOO_LONG`。

Qwen 的 NFC normalizer 会让分解重音 offset 留空隙，ByteLevel 对部分 emoji 会产生多个重叠 offset。offset 只作为切点候选，canonical 游标连续向前；去重的是候选切点，不是原文字符。不 decode token 再搜索原文。候选二分仅是装箱启发式，不假定 BPE 计数严格单调；每个接纳片段独立完整重编码验证。不能容纳完整 offset/grapheme 时可退到原码点边界，原文映射仍精确。重叠也单独重编码；若阻止新增正文进展则减少到零。

每个输出正文必须满足非空白输入契约。超长纯空白区间可以不成为 Chunk，原字符完整留在 canonical；覆盖审计显式列出这些 whitespace 排除范围并逐字符验证。原始 heading 是可选正文覆盖的导航范围：通常随首 Chunk 保留；若标题与正文之间的巨大空白导致首候选只有标题，则从真正正文开始，缺口拆成 heading_navigation 与 whitespace 两类，不生成仅标题片段。标题仍在原始归档、章节导航和索引模板中。

独立必需分母由 parser 的正文分区扣除已解析 heading 导航范围后所有非空白码点构成；其他正文缺口直接失败。报告另记整个 canonical、eligible 范围、实际 union、排除范围、重叠字符数及每片段重叠 token，可复算 `union + declared exclusions = canonical`，不会由输出 chunks 反推一个总能通过的分母。冻结 609 篇实际格式没有需排除区间，正式 corpus 测试额外要求每篇有 Chunk 且全 canonical 覆盖。

## 供 R09 复用的 tokenizer API

`FrozenTokenizer(profile, cache_root)` 只读取 `<cache_root>/<model basename>/<locked revision>/` 中四个资产（tokenizer.json、tokenizer_config.json、vocab.json、merges.txt）。每份实际读入字节按 `profiles.py` 的 SHA256 核验；版本必须为 tokenizers 0.23.2，禁用 padding/truncation。加载不联网、不读取 probes，也不加载模型权重。R03 `AutoTokenizer` 的完整 ID 与 offsets 对照是正式测试，非字符估算。

- `document(text,title)`：Embedding 完整模板编码，`add_special_tokens=True`，由锁定 post processor 追加实际 EOS 151643。不可用 tokenizer_config.eos_token 代替它。
- `query(query)`：完整 `Instruct: {instruction}\nQuery:{query}`，Query 冒号后没有额外空格，实际特殊 token 同样计数。
- `rerank(query,text,title)`：使用独立 Rerank tokenizer，分别编码 prefix、inner（query/instruct/文档模板）、suffix，再拼 IDs；每段 `add_special_tokens=False`。不能将三段连成字符串后重新编码。完整 query/文档预算超限明确失败，不截断。
- 返回 `ModelSequence.ids/pieces/token_count`。pieces 保留独立编码单元；R09 应直接复用，仍需通过能力层的 batch/padded-token 限制。`encode/offsets` 是轻量辅助，offset 不授予来源映射权威。

本机验收缓存为 `C:/Users/18221/.cache/codeplus-agenticrag/models`；调用方必须显式传入缓存根。资产不随 wheel/sdist 分发；核心新增的轻量依赖不强制 Torch/CUDA/Milvus/CodePlus。Rerank 模板测试通过不代表 GPU 评分或质量已验收。

## 实际处理和短事务

调用 `process_inputs(catalog, owner, embedding_tokenizer)`：读取本批存储的完整 ProcessingSnapshot，核对实际 parser/chunker/embedding 身份，拒绝未完成原件项；仅从 R07 `read_input` 读取已核验归档。源路径删除、文件变化、默认配置变化均不参与本次处理。正常原件阶段完成后批次进入 PROCESSING。

成功真实生成规范文本、source map、完整 ChunkSet（含 parsed 结构与索引元数据）归档；DocumentVersion 绑定实际 raw/parsed/map hash 与采集时来源。所有归档完成、hash/结构/版本/配置检查和大 JSON 序列化均在 SQL 事务外；一个 owner/epoch 短事务同时登记归档引用、version、完整 sections/chunks、processing_items。事务再次核对 batch、原 raw 结果和存储的原快照内容。处理阶段以领域 `ImportItem(stage=chunked, output_hashes={raw,parsed,source_map,chunks})` 为唯一权威；processing_items 只增加 item/version/snapshot 关联，没有另造平行 stage。R07 的 raw input_results 不变。

文件级解析/预算失败保存 `ImportItem(stage=failed,error=...)`，没有完整版本；接纳失败或拥有权丢失直接传播，不写伪造失败覆盖未知成功。归档孤儿、半元数据或仅 `stage=chunked` 字样都不能复用。迁移3的完成触发器禁止后续向完成 version 增加 sections/chunks，历史集合不会漂移。

`read_processed(catalog,batch_id,item_id)` 返回核验后的 `(ImportItem,DocumentVersion,ChunkSet)`；不存在完整登记返回 None，已登记但 hash/结构/输入/快照不一致返回 CHECKPOINT_INVALID。核验按 archive hash、canonical 字节映射和完整数据库结构集合进行，不重跑 Markdown 或 chunker；实际正式测试将 parse/chunk 函数改为抛错后仍能复用完成检查点。失败条目返回 `(ImportItem,None,None)`。没有自动修复源文件、自动恢复、Milvus、发布指针或 GC；完整用户恢复/重试流程见[手动恢复](manual-recovery.md)。
