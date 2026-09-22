# R11 原文、交付与引用核心接口

本文件描述独立核心，不表示 R12 的真实 Agent/HTTP 适配已经完成。实现状态和运行证据见 `implementation-records/R11.md`。没有新的产品命令或第二套 Agent。

## 固定版本与读取

`SourceSession(catalog, actual_run_lease, answer_token_meter, dense=...)` 需要实际 RunLease；读取时重新检查 running 状态、active pin 和固定 run/kb/revision/config。`SourceRef` 仍是内部 5 UUID DTO，不能作为模型权限。宿主从可信 Dense 候选签发 `src_` 随机句柄；`open(source_ref, section_id=None, cursor=None)` 是模型参数面，后两参数互斥。`cur_` 是服务器持久化随机句柄，绑定 run、source token、版本、章节和下一 canonical codepoint；伪造、跨运行、跨来源和 EOF/越界均报错。当前 source 路径不被打开。

读取按 revision_members 中的 ChunkSet hash，核验原件、canonical、SourceMap 和持久结构。`Section.span` 是自身分区，默认打开命中分区；`Heading.subtree_span` 仅为层级导航。每次 open 提供当前节点和 previous/next_section_id、position/total_sections、未展示前后数量，目录不会随全书章节数无限增大。逐个节点加章节内 next_cursor 可以遍历全文。无标题文档使用根分区；仅标题/空白节点只有导航，没有候选；空文档没有伪造的空 Span。宿主只读 `directory` 返回完整内部目录，不是模型工具。

Dense 已核验 Chunk 的 anchor_span 仅由可信宿主写入 opaque source handle，不改变 SourceRef DTO，也不开放模型自选偏移。默认首次读取从命中起点开始；显式 section_id 从该节 own-span 起点开始。长文命中前段通过 previous_cursor 补读，命中后段通过 next_cursor 补读；每个 cursor 绑定自己的 range_end，向前段的继续读取不会越过 anchor 与后段重叠。has_more 同时反映前/后可继续范围；has_unread/unread_spans 另表示本 run 尚未被来源工具返回的精确区间（unread_means 字段明确此定义），不是 confirmed 或内部模型阅读资格。

正文保持 R08 canonical 码点；CRLF/BOM 只按冻结规范转换，不做 NFC。返回精确半开区间、末行按 end−1 的行号、当前章节未读区间和 has_more。原件字节位置另由 SourceMap 回溯。原文位置不通过 find 或重复文本匹配反推。

## 公开交付表示与可信边车

`ToolSourceResult.payload` 是机器元数据；真正可送给模型的表示是 `.text`。它先渲染不含正文的 JSON 元数据，再按确定顺序拼接来源 wrapper 和原 canonical 正文；`.body_mappings` 在拼接时直接记录 candidate_id/source_span/body_span。正文换行、引号、反斜杠和 emoji 保留码点，不作为嵌套 JSON 字符串转义。计量使用同一 `.text`，包含导航、身份、引用标记和 wrapper。

宿主把 `.text` 放入实际 tool result 后，只将生成时边车传播到对应最终 JSON 文本节点；外层 HTTP JSON 的 bytes hash 与解码后节点码点分别记录。裁剪必须取来源区间交集并更新正文偏移；摘要、文件路径、排名、UI、history 和生成的错误不能临时通过相同文本取得映射。

`DeliveryGateway` 是宿主内部能力，不装入模型工具。适配层先 `bind_tool_result(result, actual_tool_call_id)`，再对最终 raw HTTP bytes 和 `MappedSpan` 调用 `prepare`。它独立验证三种协议的合法 tool-result 节点与同一结果的 ID：Compat role=tool，Responses function_call_output，Anthropic user/tool_result 且不是 is_error，仅字符串或 text 子块。user/system/assistant、metadata、跨节点借用 ID、未知候选、失效范围、正文不同和映射重叠拒绝。

prepare 返回进程内对象能力，传回 request UUID/JSON 不能自签回执。实际适配层观察协议完整合法终止后才可 `settle(permit, 'confirmed')`；prepared/not_sent/rejected/unknown 无新增资格，confirmed compact 也无新增答复证据。同一次终态调用幂等，不扩大范围或重复计量；request ID 不可重用。R11 正式测试中的 confirmed 是受控可信适配层 fixture，绝不表示网络或模型已经收到资料。R12 负责真实协议终态、超时、不透明 SDK 包装及实际发送行为。

## 三份独立账本

SQLite migration 5 新增 source capabilities/calls/usage/candidates、sealed delivery receipts、derived Evidence、当前 retained window 和 saved Citation。SQL1–4 字节保持不变。源调用事务短小；归档读取、完整文本计量和 HTTP JSON 检查在事务外。

1. searches/opens 按已受理尝试累计，失败和重试仍计数；超限拒绝另计 rejected。RunUsage 同步更新；RunLease.finish 使用持久最新 usage，避免旧 lease 内存覆盖计数。
2. returned_tokens/returned_fragments 保存所有已返回工具文本的累计成本；探索 token 上限与时间从同一 run 起点扣减，保留 finish reserve。它不是完整 LLM 累计用量，R12/R19 仍需记录所有实际 HTTP 请求。
3. window_tokens/window_fragments 由 search/open 共同扣减 context_tokens/context_chunks，不能每次重置。只有可信宿主已经执行明确 history 裁剪/保留后，才可用 `retain_prepared_window` 同步一个已校验最终 payload；完整 JSON（含 wrapper）按同一回答模型 meter 保守计量。该方法不能由模型调用，也不宣称交付；不改变历史成本或已 confirmed 资格。generation 防止旧 permit 回滚后来的窗口预留。

Token meter 由宿主注入，必须是回答模型的真实计数或有依据上界，并固定 identity；核心不使用 embedding tokenizer、字符除四或默认猜测。正式测试的 UTF-8 byte unit 明确是受控算法 fixture。完整 LLM messages/tools/overhead、输出与收尾/修正预留的硬门属于 R12/R19。

## 精确引用和历史回看

候选会展示 provisional evidence UUID/marker，但尚不能引用。confirmed 只激活实际映射的子区间；同 evidence 多次送达取有序 union，gap 不被补齐。`read_evidence` 重新核对 payload 与数据库 row/run 绑定，并从同 run、confirmed、非 compact 的 sealed receipts 重算来源区间，拒绝派生元数据损坏后扩大范围。这不是防御同用户重写整个数据库的承诺。

`CitationRegistry.validate/save(evidence_id, spans, quotes)` 核对本 run、版本、成员、章节 own-span、送达区间和逐码点摘录。直接摘录不允许 NFC 替换；多段必须有序不重叠，跨未交付空隙的连续摘录拒绝。多段 quote_hash 对精确 quotes 数组编码计算，连接符/省略号不是原文。

save 返回稳定 Citation 及 evidence_marker→citation_marker 映射。`render_markdown(draft, saved)` 核对真实已保存记录，替换 provisional 标记并生成配对脚注；拒绝未知、缺失、未使用标记以及模型自写来源脚注定义。该核心 renderer 将全部 `[^...]` 语法保留为引用标记，包括代码例子。正式正文在宿主缓冲后调用；R12 的一次原 Agent 修正尚未由本项实现。程序核验不证明语义支持。

`open_citation(catalog, citation_id)` 是历史只读路径，允许终态 run，依赖保存版本归档和元数据，不依赖 source 文件、当前 revision 或 Milvus；不会给另一 run 新证据资格。返回 file/version/section/ranges/lines/quotes 和 hash；归档缺失/损坏明确失败。已确认原文资格与当前窗口保留独立，compact 后仍可复查历史已送达片段。R15 的真实索引 GC 竞争另行验收。
