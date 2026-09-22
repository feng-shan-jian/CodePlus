# R12 预算实现设计（交付候选，非独立验收结论）

本轮真实配置仍为用户的 deepseek-chat / deepseek-reasoner，四次有界网络预检实际响应 model 为 deepseek-flash。别名默认模式不同，同一短问题输入分别 14 / 40 tokens；不得只按 response.model 选模板。配置未改写。

生产回答 meter 固定 DeepSeek-V4.1-Flash revision dba1be0a40aa45a94ad051997016db3960a90277 的官方 encoding/encoding.py（SHA256 502bdaec8a3fd88ebc24c4721a7038fbe42f2063c664638127056107920035c1），其 tokenizer.json SHA256 c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b。encoder 纯 Python 必要代码与 MIT 归属随独立包，tokenizer 为用户显式指定的绝对缓存路径，启动校验 hash，不随代码提交。当前官方模型身份参考 https://api-docs.deepseek.com/quick_start/pricing/ 。模板来源 https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/tree/dba1be0a40aa45a94ad051997016db3960a90277/encoding 。

上界单位是完整官方模板的 UTF-8 字节，不是精确 token。对 HTTP request hook 实际序列化 bytes 解码后，只允许已实现文本字段：完整 system、消息、思考历史、工具 schema/calls/results；所有会改变模板的参数绑定或拒绝，图像、远程状态/previous_response_id、未知字段拒绝。chat 默认 chat 模式、reasoner 默认 thinking/75；模板及响应身份、meter 版本写入冻结 run 配置。

上界证明依赖固定 tokenizer：normalizer 的 Sequence 为空；pretokenizer 的 Split 只分割不插入字符，随后 ByteLevel add_prefix_space=false；BPE 无 unk/continuing_subword_prefix/end_of_word_suffix，合并不会增加 byte 单元数；1283 个 added tokens 非空，无零宽匹配。post_processor 的 ByteLevel 配置虽含 add_prefix_space=true，但 tokenizers 0.23.2 Rust PostProcessor::added_tokens() 返回 0，process_encodings 只改 offsets/sequence_id，不插入 token。对应官方源码 https://raw.githubusercontent.com/huggingface/tokenizers/v0.23.2/tokenizers/src/pre_tokenizers/byte_level.rs 。生产检查固定版本与这些结构，拒绝未知配置。网络样本已证明特殊 token 字面量的服务端处理存在差异，无法据此证明任意转义上界，因此生产门统一递归检查完整 payload 的所有键和值、schema、arguments，发现 pinned added-token 字面量即拒绝；普通 Unicode 继续支持。此证明不依赖从十条样本拟合安全常数。

网络校准 R12-meter-preflight-v41.json 中 9/10 本地精确计数对齐；unicode/特殊 token 字面量场景差 111，该类型现明确不支持。该事实保留，不宣传精确计数，更不把预检当实际 Agent 验收。未知或不支持模型直接缺能力诊断，绝不改用户模型。Leader 明确扩展允许独立区 .gitattributes 精确锁定 vendored encoder 为 LF；干净 wheel/sdist 安装仍须实际核验 hash。

LLM 每次发送前持久预留完整输入上界 U 加输出硬 cap O，并验证 U+O 不超过已验证 context。请求的 raw usage、真实 terminal、发送状态分别保存。已发送且总用量未知保留整个预留；完整可靠 usage 到达后按实际结算，包含缓存和推理（协议 total 已含时不重复相加）。实际 usage 超出预留仍按真实值记账，记录违规并停止，不把账本截断成预算值。

开始运行时冻结两个独立有界收尾槽位（finalize 与一次 citation_repair），每个槽位含完整输入上界及输出上限。探索预算不能花掉它们。进入收尾前只保留有界历史/可信 sidecar，实际 HTTP 门再次计全模板；无法裁到槽位时失败，不补发无限输入。修正是同一个 Agent 循环的一次 tools=[] 请求，策略不另建模型循环。截止时刻基于 run 开始，包含装配、权限、模型/worker 排队、工具和 compact 全过程；硬截止或取消后不再收尾。

SourceSession 返回来源文本成本是单独的累计 source-return 限额，不是第二份 LLM input 用量。SourceSession._admit 已原子登记 search/open 次数，scope 不再次累计；拒绝项单独记。模型 ledger 只在真实 HTTP 发送门预留和结算，工具 ledger 只登记工具 admission/结果。compact 请求独立记账但不能新增 citation delivered 资格。

取消先关闭 admission，撤销权限和协程，再 drain 真实 executor Future / worker RequestHandle。未完成读者将 run 标为 terminal + cleanup_pending，但继续持有固定 revision pin；后台完成后才释放。已受理请求 settlement 在 run terminal 之前处理，迟到 tool 结果不得绑定或授权新证据。所有资源归属交付独立清理会话。

最终补充：reasoner 的完整10项网络校准也为9项精确、特殊字面量差111（R12-meter-preflight-reasoner.json），同样不能外推任意转义上界。生产在 arguments 经官方模板二次JSON解析后的对象上也检查控制字面量。同步输入计量/SQLite预留跨过deadline时，最终HTTP门拒绝；真实ledger退款为not_sent/0，transport为0次、Evidence为0。

transport完整终态与Evidence资格分别保存：合法length/max_tokens/incomplete(max_output_tokens)可证明输入送达，答案仍不得发布；failed/content_filter/refusal/明确error不新增Evidence，未知或缺终态原因也不授权。failed缺model保留失败，不误报身份变化；成功/合法截断身份缺失或改变失败关闭。完整usage的总量可结算，缺缓存/推理分项仍None，已知0不能当未知掩盖越界。

finish历史裁剪优先去重复普通消息/未校验草稿，再按完整旧tool-use/result ID对逐个淘汰，避免整条assistant多call被丢时连带移除仍能容纳的open。candidate IDs以后端实际paired/serialized映射记录，并与最后HTTP receipt交叉验证；原来有来源却没有任何一对能容纳时明确失败，不发送空来源修正。每次记录实际完整输入上界，绝不从摘要重建sidecar。一次引用修正反馈汇总全部citation/span及marker/shape错误；原文和引用规则不变，诊断仅含安全位置/长度/码点。保存前及最终accept前再次核对硬deadline，同步引用处理跨截止时零产物发布。已持久化终态后消费者取消仍传播，但不重写终态；真正未完成读者则先保pin，待真实完成释放。
