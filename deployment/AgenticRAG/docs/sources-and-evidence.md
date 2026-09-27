# R11 原文、交付与引用核心接口

本文件描述独立核心，不表示 R12 的真实 Agent/HTTP 适配已经完成。没有新的产品命令或第二套 Agent。

## 固定版本与读取

`SourceSession(catalog, actual_run_lease, answer_token_meter, dense=...)` 需要实际 RunLease；读取时重新检查 running 状态、active pin 和固定 run/kb/revision/config。`SourceRef` 仍是内部 5 UUID DTO，不能作为模型权限。宿主从可信 Dense 候选签发 `src_` 随机句柄；`open(source_ref, section_id=None, cursor=None)` 是模型参数面，后两参数互斥。`cur_` 是服务器持久化随机句柄，绑定 run、source token、版本、章节和下一 canonical codepoint；伪造、跨运行、跨来源和 EOF/越界均报错。当前 source 路径不被打开。

读取按 revision_members 中的 ChunkSet hash，核验原件、canonical、SourceMap 和持久结构。`Section.span` 是自身分区，默认打开命中分区；`Heading.subtree_span` 仅为层级导航。每次 open 提供当前节点和 previous/next_section_id、position/total_sections、未展示前后数量，目录不会随全书章节数无限增大。逐个节点加章节内 next_cursor 可以遍历全文。无标题文档使用根分区；仅标题/空白节点只有导航，没有候选；空文档没有伪造的空 Span。宿主只读 `directory` 返回完整内部目录，不是模型工具。

Dense 已核验 Chunk 的 anchor_span 仅由可信宿主写入 opaque source handle，不改变 SourceRef DTO，也不开放模型自选偏移。默认首次读取从命中起点开始；显式 section_id 从该节 own-span 起点开始。长文命中前段通过 previous_cursor 补读，命中后段通过 next_cursor 补读；每个 cursor 绑定自己的 range_end，向前段的继续读取不会越过 anchor 与后段重叠。has_more 同时反映前/后可继续范围；has_unread/unread_spans 另表示本 run 尚未被来源工具返回的精确区间（unread_means 字段明确此定义），不是 confirmed 或内部模型阅读资格。

正文保持 R08 canonical 码点；CRLF/BOM 只按冻结规范转换，不做 NFC。返回精确半开区间、末行按 end−1 的行号、当前章节未读区间和 has_more。原件字节位置另由 SourceMap 回溯。原文位置不通过 find 或重复文本匹配反推。

## 公开交付表示与可信边车

`ToolSourceResult.payload` 是机器元数据；真正可送给模型的表示是 `.text`。它先渲染不含正文的 JSON 元数据，再按确定顺序拼接来源 wrapper 和原 canonical 正文；`.body_mappings` 在拼接时直接记录 candidate_id/source_span/body_span。正文换行、引号、反斜杠和 emoji 保留码点，不作为嵌套 JSON 字符串转义。计量使用同一 `.text`，包含导航、身份、引用标记和 wrapper。

宿主把 `.text` 放入实际 tool result 后，只将生成时边车传播到对应最终 JSON 文本节点；外层 HTTP JSON 的 bytes hash 与解码后节点码点分别记录。裁剪必须取来源区间交集并更新正文偏移；摘要、文件路径、排名、UI、history 和生成的错误不能临时通过相同文本取得映射。

`DeliveryGateway` 是宿主内部能力，不装入模型工具。适配层先 `bind_tool_result(result, actual_tool_call_id)`，再对最终 raw HTTP bytes 和 `MappedSpan` 调用 `prepare`。它独立验证三种协议的合法 tool-result 节点与同一结果的 ID：Compat role=tool，Responses function_call_output，Anthropic user/tool_result 且不是 is_error，仅字符串或 text 子块。user/system/assistant、metadata、跨节点借用 ID、未知候选、失效范围、正文不同和映射重叠拒绝。

prepare 返回进程内对象能力，传回 request UUID/JSON 不能自签回执。实际适配层观察协议完整合法终止后才可 `settle(permit, 'confirmed')`；prepared/not_sent/rejected/unknown 无新增资格，confirmed compact 也无新增答复证据。同一次终态调用幂等，不扩大范围或重复计量；request ID 不可重用。R11 正式测试中的 confirmed 是受控可信适配层 fixture，绝不表示网络或模型已经收到资料。真实协议终态与交付由宿主适配层记录。

## 单次返回与被动记录

`context_tokens/context_chunks` 限制每次 source 返回；可选 search 上限进一步限制单次搜索。次数、已返回正文总量和片段数仍可记录，但不控制后续 search/open，也不预留收尾配额。正文裁切与去重保留精确原文坐标。

宿主计量接口只有 `identity/count(text)`；当前使用 UTF-8 字节保守上界，与回答模型、协议和 tokenizer 解耦。来源窗口只描述实际保留的原文映射，供位置去重和交付追踪使用，不是累计正文额度。

## 精确引用和历史回看

候选会展示 provisional evidence UUID/marker，但尚不能引用。confirmed 只激活实际映射的子区间；同 evidence 多次送达取有序 union，gap 不被补齐。`read_evidence` 重新核对 payload 与数据库 row/run 绑定，并从同 run、confirmed、非 compact 的 sealed receipts 重算来源区间，拒绝派生元数据损坏后扩大范围。这不是防御同用户重写整个数据库的承诺。

`open_citation(catalog, citation_id)` 接受答案里的 evidence UUID 或旧 saved citation UUID。读取核对原版本、已交付区间和归档正文；旧引用继续核对原保存的摘录。普通 Agent 自行生成文本与引用标记，适配层不审批、改写或修复答案。

历史只读路径允许终态 run，依赖保存版本归档和元数据，不依赖源文件、当前 revision 或 Milvus；不会给另一 run 新证据资格。返回文件、版本、章节、原文区间、行号、摘录和 hash。已确认原文与当前窗口独立，compact 后仍可复查。
