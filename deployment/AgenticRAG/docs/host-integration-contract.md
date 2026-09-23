# R04 宿主接入与安装契约

2026-09-22；基线 `43e9af9de44212342850f3668826a0e5b4bf9a49`。本文件记录 R05/R12 的实施边界，关联 D02/D10/D14/D27–D29/D33–D35/D52–D55、T01/T07/T09。运行记录见 [R04](implementation-records/R04.md)，命令见 [环境矩阵](environment-command-matrix.md)。2026-09-23 用户删除本阶段发行安装与主包迁入任务；第 6 节原最终发行设想仅作未来设计参考，现行完成判定以 [checklist](implementation-checklist.md) 为准。

## 1. 实际接点与需要修正的地方

| 当前路径（本次读取源码） | 现状 | 后续接点 |
| --- | --- | --- |
| `codeplus/agent.py:Agent.run` | 旧 `_prepare_knowledge` 在循环前执行；collect 时流出正文；有 64000 自动升级；无运行级 finally | 使用可选策略；RAG 正文缓冲，统一终态与 finally；普通运行保留现行升级行为 |
| `Agent.run_to_completion` | 独立循环；回调会发送 `last_text`；到迭代上限仍返回它 | 同一策略对象类型与相同检查次序；字符串仍是返回类型，但只能返回批准正文或明确未完成说明 |
| `_execute_tool` / `_execute_single_tool_direct` / `_execute_tool_noninteractive` | 串行、并行和无交互三条执行路径；都有注册/启用/权限/参数检查；Hook 行为并不完全相同 | 不绕过原有检查；三入口汇入同一受理函数，泛 catch 前穿透 BudgetStop/取消 |
| `_maybe_persist_or_truncate` / `apply_tool_result_budget` | 单条 50000 字符与累计裁剪；UI 显示 `result.output`，history 可能只剩 2000 字符预览；磁盘失败可能保留全文 | 每次文本变换同步来源区间；写盘全文不产生阅读回执；最终请求门再限制全部输入 |
| `context/manager.py:auto_compact` | 同 client 最多三次摘要请求；丢掉 StreamEnd usage；泛 catch 将异常转换为失败字符串；保留尾部、替换前缀 | 所有摘要调用进入同一计量层；不得吞硬预算/取消；摘要不新增证据资格 |
| `conversation_pairing.py`、`serialization.py`、`client.py` | 请求前修复配对、合并消息、协议序列化和 cache 标记；Anthropic 可能发送 content_blocks；OpenAI 两条序列化丢弃 is_error | 来源映射必须穿过全部变换，最后由 SDK 的实际请求字节确认 |
| `client.py` / `tools/base.py:StreamEnd` | Responses 未传 max_output_tokens；缺失 usage 常变成 0；Compat usage 尾 chunk 写死 end_turn 并遗漏 length；EOF 可无终止事件 | 新的可选调用控制携带硬上限、完整 usage 和 terminal；默认调用协议兼容 |
| `agents/tool_filter.py` / `tools/agent_tool.py` | 注册表复制大多复用 Tool 实例；子 Agent 可共用父 client | 新建 run 专属工具与 scoped client；不往共享实例上写 kb/run 或 token 上限 |
| `app.py:LoopComplete / _update_session_summary` → `memory/session.py:generate_session_summary` | 在完成事件后后台调用共享 client.stream，usage未进运行账本 | RAG 不派发后台模型标题/会话摘要，只保存确定性元数据；普通任务不变 |

行号会在旧清理合入后改变；以文件、类和函数名定位，静态指纹见 `R04-static-evidence.json`。现有旧 KnowledgeContext、强制预搜索、WriteFile 内容改写均不是新接点，不予复用。

## 2. 两入口共用的最小策略

宿主新增 `codeplus/run_policy.py`，只放与 RAG 无关的轻量类型和协议，不导入开发包。`Agent.__init__` 在全部现有位置参数后增加 keyword-only `execution_policy: RunExecutionPolicy | None = None`。默认 None，现有调用者、事件与普通流式行为不变。适配层给一次运行构造一次策略，不能把它放在全局 Agent/Tool 单例中反复改绑定。

下列是待实现签名；不是现在可导入的 API：

```python
class RunExecutionPolicy(Protocol):
    async def start(self, context: HostRunContext) -> RunScope: ...

class RunScope(Protocol):
    binding: RunBinding              # run/kb/revision/config/deadline，不可变
    registry: ToolRegistry           # 此 run 新实例，只含允许工具
    client: LLMClient                # 此 run 独占 facade/SDK/HTTP 生命周期
    def model_control(self, purpose: ModelPurpose) -> ModelCallControl: ...
    async def admit_tool(self, call: ValidatedToolCall) -> ToolPermit: ...
    async def tool_finished(self, permit: ToolPermit, outcome: ToolOutcome) -> None: ...
    async def assess_output(self, candidate: BufferedResponse) -> OutputDecision: ...
    async def finish(self, outcome: RunOutcome) -> FinalArtifact | None: ...
    async def aclose(self) -> None: ...

class ModelCallControl(Protocol):
    output_cap: int                  # 单次、不可变、在 SDK 序列化前确定
    purpose: Literal['agent', 'compact', 'finalize', 'citation_repair']
    async def before_send(self, request: PreparedRequest) -> RequestPermit: ...
    async def settled(self, permit: RequestPermit, result: RequestOutcome) -> None: ...

# 向现有三种 LLMClient.stream 增加可选 keyword-only control；None 保持旧调用。
# PreparedRequest 包含 request_id、最终 HTTP body bytes、protocol、映射和输入上界。
# RequestOutcome 包含 delivery、raw_usage、terminal、异常类别与耗时；不写认证头。
```

`HostRunContext` 是入口、会话 ID、工作目录、明确的功能/任务种类、宿主模型配置和权限执行接口；不把整个 TUI 当成核心依赖。`start` 固定已发布版本并取得 pin；它在返回前失败必须自己撤销已获得资源。`RunBinding` 与候选来源元数据由核心赋值；模型只可提供 query、允许的检索方式、来源 ID 和分页请求，不可提供/覆盖 run_id、kb_id、revision_id、预算或成功标志。无权限的来源 ID 在核心再次拒绝。

`OutputDecision` 为 `accept(ValidatedArtifact)`、`repair(error_locations)`、`stop(reason, verified_partial)`；不负责调用模型。`ValidatedArtifact` 含不可变正文、SHA256、引用清单和对应 run；save 只能拿到这一类型。一次修正额度由 scope 原子消耗，不能由模型文本重置。只有现有 Agent 循环负责继续，不能让策略再启动一个 Agent 或在 `assess_output` 内偷偷请求模型。

两入口一致遵循下列顺序，允许抽取小 helper，不复制第三条主循环：

1. 可选策略 start；在 try 内固定 run，装配局部 registry、client、功能提示。不强塞首条检索；Agent 可先改写 query，但没有本轮有效取证就不能发布事实答案。管理、澄清和取消走明确非资料回答状态。
2. 检查单调 deadline/取消/迭代数。构建 system、工具 schemas；auto_compact 使用 `purpose=compact` 的 control，每一次摘要重试单独登记。所有局部会话变换发生后才准备主请求。
3. client 修复配对、协议序列化、cache 标记，传单次 cap；SDK request hook 取得最终 bytes，经 `before_send` 检查 cap、输入/累计预算并原子保留额度后才发出。stream 消费始终在 try/finally 中结算这次请求；正常结束、provider 拒绝、网络中断和取消都不得漏记。
4. 请求 terminal 与 delivery 先登记，再确认本次已送达的合格来源区间，最后评估答案。`TextDelta` 只进 buffer；RAG 不向 `StreamText`、event_callback、报告文件发出草稿正文。中间正文可以作为标记 draft 的内部历史供同一 Agent 推进，不转为正式消息或正式报告。ThinkingText 同样不作为面向用户的已验证答案；显示受控进度和工具状态即可。
5. 有工具调用时继续原有分批调度：registry/启用检查 → 原有权限及适用 Hook → 参数校验 → `admit_tool` 原子计数 → 执行 → `tool_finished` 登记成功候选 → 单条/累计变换和映射 → history。权限等待使用 `asyncio.timeout_at(deadline)`（同一loop单调时钟）；超时/取消撤销 PermissionRequest 的 future 并通知消费者移除待批项，迟到回调检查 future.done/cancelled 与 run nonce，不得重新唤醒执行；批准后仍重新检查deadline。未知、禁用、权限/Hook/参数拒绝只计 rejected；已受理后网络失败、无命中、取消或重试各计一次 accepted，不退款。search/open 分别计数，另有 rejected/总尝试及迭代上限，防止错误调用空转。
6. 无工具的正常终止响应进入引用校验。失败仅允许同一循环带错误定位修正一次；修正阶段 tools 为空，不能再次取证、调用子 Agent 或扩预算。修正正文重新完整校验。预算探索停止时进入最多一次 finalize，再允许最多一次 citation_repair；这两次总共使用预先保留的收尾预算，仍受总额/截止时间约束。
7. 正常/异常出口共用宿主 task-owner 收束步骤（下段），收束后 `finish` 原子保存终态、已验证输出/证据与实际停止原因，再释放 pin 和专属资源。两入口外层 finally 无条件进入幂等清理，即使 stream/callback/validation/save/finish 自身抛错也执行；取消传播原取消，close 的错误记次级错误，不掩盖主因。无法持久化终态不返回 completed；仍留 running 的异常记录由恢复协议处理，不能伪造成功。

两处现有 `asyncio.gather` 首异常不自动取消并等完其他任务。RAG 在宿主共同 helper 持有 `RunTaskOwner`：登记每一个工具 asyncio.Task、待权限future、底层线程/worker请求ID及其真实完成句柄；作用域内创建，不能 fire-and-forget。任何异常、BudgetStop、取消、consumer-close 或正常退出先原子关闭 admission，撤销权限等待，再取消可取消任务，随后 `gather(..., return_exceptions=True)` 等待所有任务被回收并结算已受理尝试。run nonce/closing 标志拒绝迟到结果注册证据或写入历史；要等实际固定版本读取结束才可解除pin，取消 to_thread 的协程不等于底层线程已结束。

清理等待在 shield 下独立于已取消的调用栈，用有限 cleanup grace（配置值，非新增模型预算）。底层任务必须支持取消确认或保留可跟踪的真实完成句柄；超时不能假称已释放：记录 `cleanup_pending`、拥有者/在途句柄并保留保护pin与其需要的资源，由受控完成回调/恢复流程确认读者结束后释放。对其他run共用的worker/连接不做全局终止。主运行状态按原停止原因 failed/cancelled/incomplete，绝不 completed；不再执行任何模型/取证请求。R12 验证可取消路径完全drain、非即时取消路径不提前释放pin，R15验证异常恢复。普通任务的gather行为不在R04改动范围。

异步生成器 `run` 在消费者 break/取消时需要入口用 `contextlib.aclosing(agent.run(...))`；只写 generator 内 finally 而不确保 aclose 不足够。TUI、CLI、Remote 的消费点都属于接入测试范围。`run_to_completion` 保持 str 返回契约，通过 scope 持久结果及现有回调的增量 `run_status` 事件给调用者状态；非成功返回明确未完成说明和可用证据，不返回未核验 last_text 冒充答案。没有策略时不改变返回语义。

启用顺序明确区分：R12 必须完成 TUI 流式与 `codeplus -p` 的最小装配/消费者关闭，并用测试直接覆盖 `run_to_completion`；开发配置限定 fixed Dense，只暴露已实现的检索能力，不提前宣称 BM25/Hybrid/Rerank，最终默认 auto 不变。TUI 通过基础 `/knowledge use <id>` 选择库、`/knowledge ask <question>` 显式发起 QA，选择库不会让之后普通编程自动变成 RAG。R12 命令薄层只需 use/ask/off 及明确缺失能力提示；完整管理、恢复、模式和帮助在 R21 完成。Remote 在 R21 完成同等取消/finally验证前不得启用新 RAG：显式 knowledge 请求返回 `feature_not_available_in_entrypoint`，不得从共享命令注册/会话元数据意外装配策略或悄悄当普通问答；普通 Remote 不变。

## 3. 工具与功能隔离

首版 RAG 模型可见工具集合固定为 `knowledge_search` 与 `knowledge_open`，均为新建且绑定不可变 RunBinding 的 Tool；关闭 ToolSearch/MCP 动态发现。首轮可选择 query，fixed/auto 仅约束 knowledge_search 允许参数，与宿主 `--mode` 无关。继承 Conversation 的旧资料只作待验证线索，本轮候选 registry 初始为空。

新建 ToolRegistry，不能 clone 后修改共享工具状态；宿主每个执行入口还检查 run allowlist，防止模型提交 schemas 外工具。RAG 的 Agent/后台 team/worktree/Bash/MCP/LoadSkill/InstallSkill、任意文件读写与 SyntheticOutput 都不装配；即使父普通会话有它们也不继承。RAG 不启动记忆提取、归并或检索模型，不消费外部 team/mailbox 作为证据，不启用 coordinator。TUI 的 LoopComplete 不再为 RAG 派发 `_update_session_summary`（其 `generate_session_summary` 会调用共享client）；会话标题/摘要改用现成目标或已验证输出的确定性元数据，在scope结束后也不能补发模型请求。普通会话保留原设置。任何未来新增能发模型或创建任务的工具必须先加入同一计量/取消契约和验收，不能只把名字加到 allowlist。

可执行用户 Hooks 能运行任意命令/模型，无法保证预算：首版若该功能会执行这类 Hooks，在 start 前返回明确 `unsupported_execution_hooks` 配置错误；不能静默略过原有拒绝 Hook。无执行副作用的宿主权限规则仍完整保留。提示资料或来源 URL 不是授权，核心不自行访问 URL。模型身份/usage 元数据查询在启动前完成并记录，不在 run 中触发隐形调用。

报告保存是校验后的宿主动作，不是模型可调用的 `WriteFile`。入口把用户指定目标和 ValidatedArtifact 交给宿主既有 WriteFile 实例及原权限执行路径（参数、路径、覆盖、PermissionRequest、适用 Hook 均照旧），使用不可由模型伪造的内部执行能力。能力只批准该 artifact 的 hash/固定路径/一次写入；不能先得到权限再换正文。RAG allowlist 例外只识别此内部能力，不能靠同名工具或模型参数伪造。R20 使用 Agent 内部 ToolCallComplete 对象身份完成一次调用，受限 registry 仍只有 search/open；保留原 WriteFile 的读取缓存与文件历史，缺少实例时新建带缓存的 WriteFile。`host_runs.detail.answer_status` 与 `save.status` 分别记录答复和保存结果；不得声称文件已保存。写入及实际字节回读在宿主拥有的任务内完成，先记录路径/摘要再做后续截止检查；取消/超时构造新 outcome 时保留已经发生的保存事实。

R20 向 `HostRunContext` 追加兼容默认值的 `request`，向 `RunOutcome` 追加 `run_id/save/research`，向 `RunScope` 追加可选 `report_path`。`save.sha256` 是实际文件字节摘要，区别于 `ValidatedArtifact.sha256` 的逻辑 Markdown 摘要。completion 回调与 -p 最终结果同步新字段，普通 Agent 不装配策略时接口行为不变。公开进度与父链成本复用 `host_runs` JSON 和原 `runs.parent_run_id`，不增加表；具体参数及失败状态见 [开发使用说明](codeplus-integration.md)。

## 4. 请求回执与来源精度

候选、已送达与模型内部阅读是不同概念。本功能只能观察提供方接受并完成响应的请求，不能证明模型内部注意力或理解。正式字段使用 `delivery_confirmed`，展示“本轮已送达证据”；沿用 spec 的“已读资格”时仅表示这个可观测定义。

成功工具从 document_version 的**规范解析文本**归档产生 `SourceCandidate(binding, call_id, document_version_id, source_start, source_end, exact_text, text_sha256, succeeded)`。映射是可信 sidecar，不写成让模型回传的参数。SourceCandidate、MappedSpan 与 Receipt 的 source `[start,end)` 统一指该版本规范解析文本的 Unicode 码点区间，与 T05 一致，不能混用 UTF-8 原件字节、UTF-16 下标或 token 位置。原件 CRLF/BOM 等字节位置另经 source map 回溯；R04 不决定解析归一化算法。实际请求 body_start/end 指解码后对应 JSON 文本节点的码点区间，独立于 source 偏移；片段 hash 为该规范文本片段的 UTF-8 bytes，body_sha256 为 HTTP 原始 bytes。重复文本由构建时位置传播，不用 substring/find 推测来源。R04 的 CRLF/Unicode fixture 只测给定文本与序列化区间，没有替代 R08 的原件→规范文本映射验收。

来源映射跟随工具结果、裁剪/包装、history、compact 保留尾部、pair repair、serializer 各步：未发生变化的区间原样保留；裁剪取交集并调偏移；wrapper/文件路径没有来源；摘要替换的区间删除映射；pair repair 合成错误无候选，孤儿删除即无映射；serializer 输出 JSON path，并考虑 system 插入、连续 user 合并、Responses 一条变多条。不能依赖旧消息下标，也不能把未映射的相同文本临时匹配上。

最后 hook 解码实际 HTTP body，再按 JSON path、tool call ID、精确区间与 hash 检查映射；同时查可信成功候选及当前 binding。OpenAI serializer 没有 is_error，因此 wire body 无错误标志不能代表成功。Anthropic 的 content_blocks 非空时覆盖 content：仅实际 text 子块可获资格；tool_reference、图片等非正文无资格。原 content 的截断不会自动截断 content_blocks，最终 payload 门必须对两者实际发送内容计量并同步裁剪或拒绝，不能靠宿主现有字符计数保障。

`DeliveryReceipt` 保存 request_id/purpose/protocol、run/kb/revision、body_sha256、source version、原文 start/end、text_sha256、JSON path/body start/end、delivery 状态；原始敏感请求默认不落盘，仅保留必要片段和 hash。身份/参数/cap变更后重试是新 request_id。统计命中候选、UI 原文、加入 history、已写磁盘、已生成摘要或发送前观察均不产生合格 evidence。

| delivery | 判定与证据资格 | 用量处理 |
| --- | --- | --- |
| prepared / not_sent | 仅序列化或门前拒绝；无回执 | 确认 transport 未发出的保留量可释放，明确 not_sent |
| rejected | 收到明确提供方错误（例如 4xx/429）；无回执 | 有可信 usage 则记实际；无 usage 保守留上界，不能假定免费 |
| unknown | 发送后 timeout/取消/连接断开、只有 200/局部 chunk、无合法协议终止 | 无新证据；保留预留额，标 usage_unknown；不会自动当作未发送重放 |
| confirmed | 完整、匹配该请求的合法协议响应终止；length 也证明输入提交但不能证明答案完成 | 确認正文合格区间，结算实际或保守上界 |

先前已确认、同 run 的证据在后续 compact 后仍可核验，摘要本身不增加资格。`purpose=compact` 的请求仅作摘要，不能成为本次答复的新证据；最终/修正请求可重新发送已核验的原文，并照同一规则登记。继续研究是新 run，即使版本相同也必须重取证。

## 5. 计量、重试、硬限与终态

累计额度和上下文窗口分开：每个实际请求的输入（含 system/tools/cache）、输出（含计入 provider output 的 reasoning）都消耗累计 Token。重复上下文每次都计；Embedding/Rerank 工作请求记录独立模型用量与排队/推理时间，不冒充 LLM chat usage。`Usage` 保存 raw 值和归一化 `input_uncached/output/cache_read/cache_creation/input_total` 及缺失字段，不能把缺失值变成 0。

Anthropic input 与 cache read/creation 可相加；缺 cache 字段视为未知，除非该适配器已验证“不提供即零”的协议。OpenAI input/prompt total 包含 cached，归一化时减去 cached；cached 缺失则保留总输入已知、分项未知，不能重复相加。usage 多个 chunk 采用协议累计值合并，不把 message_start 与 message_delta 重复累加。超出预留/负数/不一致统计触发协议或预算错误并停止后续请求，不改历史结果掩盖超限。

每次调用原子保留 `input_upper_bound + output_cap`；已确认实际 usage 后只释放明确未用额度，缺总用量保留全额并增加 unknown_calls。unknown 分项但总额可靠时仍可按总额结算。账本 request ID 永不复用、只结算一次，并行工具/请求共享一个 run 的锁。not_sent 只有可信 transport 门记录才可释放；已受理失败不因无响应退费。

输入上界不能用字符/4估算冒充：R12 必须为支持的回答模型验证完整消息/工具/协议 overhead 的 tokenizer 计数上界，或保守预留该提供方已验证且强制执行的最大输入窗口。没有可靠上界或供应方不能强制 cap 时拒绝启用有硬预算的功能，记录 unsupported_budget_meter。窗口未知不声称可保障硬限；UI 的粗略估算只用于 compact 提示。最终 HTTP 门再次核对 cap、全部实际 payload 输入及上下文可用空间（input + output/协议保留空间 <= verified context limit）。验证窗和粗估是不同字段。

探索不能消费 `finish_reserve`。开始时检查该预留可覆盖一次有界 finalize 和一次有界 citation_repair 的输入及输出；提供给收尾的证据与草稿必须裁到这个已验证输入上界，优先保留必要证据，不扩大总预算。探索不足时停止 search/open，尝试有预算的收尾；修正不够则返回 incomplete 和可用证据。硬 deadline/总上限到达、用户取消或供应方超限后不再发收尾请求，只返回此前已验证内容/确定性状态说明。预留数值随 R23 冻结配置，R04 不凭空给生产预算。

三协议硬 cap 在 SDK 前传到请求：Anthropic `max_tokens`；Responses `max_output_tokens`；Compat `max_tokens`（只承诺已验证能遵守该字段的 provider，要求 max_completion_tokens 的变体须另做适配/验收）。cap=min(配置上限、剩余累计额度、上下文剩余输出)。thinking 固定预算必须低于相同 cap；放不下则明确配置/预算错误，不能自动把 cap 抬到 1024/64000。RAG 禁用 `set_max_output_tokens(MAX_TOKENS_CEILING)` 与自动接续升级，普通任务保留。

SDK 当前 OpenAI/Anthropic 均默认重试 2 次。一 run 创建独占 HTTP client（redirects 禁止自动跟随、transport retries=0），使用现有 SDK `with_options(http_client=owned_http, max_retries=0, timeout=remaining_deadline)` 或等效独立构造；不改 parent 的字段或 event_hooks。request hook 观察 SDK 最终 request.content，所有 headers/JSON/cache 标记之后保留额度，transport 不再修改正文。显式模型重试由同一 Agent 路径处理，每次新 permit、同一 deadline、单独用量；compact 的最多三轮 prompt-too-long 请求也逐次计量。不能假设一次 stream 等于一个 HTTP attempt。SDK/HTTP 关闭所有权在 scope，finally 只关独占资源，不能关闭 parent 共享连接；MockTransport 实验证明的是本机 SDK 的一次请求行为。

`auto_compact` 与三条 tool 泛 catch 前必须显式 `except (BudgetStop, asyncio.CancelledError): raise`；普通异常可以沿用原错误路径。RAG compact 失败且无法满足下一请求窗口时停止，不忽略错误继续。手动 compact 发生在活跃 run 也用该 control；在 run 之外不创建 RAG evidence。

另有SDK异常包装边界：本机OpenAI/Anthropic把HTTP request hook抛出的普通BudgetStop包装为APIConnectionError，现有宿主再转为NetworkError。因此每次SDK调用在发起前创建独占 `PreSendGate(request_id)`，门前预算拒绝先把可信not_sent原因写入该对象再抛出。RAG客户端的SDK异常捕获层在通用APIConnectionError→NetworkError转换**之前**检查该request的gate，恢复BudgetStop并结算delivery=not_sent；不凭错误文本猜测、不读取别的request状态、不按网络失败重试。没有gate拒绝记录的异常仍为unknown/真实网络错误；新重试使用新gate。before_send原子预留失败不扣额度；若已预留而门前最终核验拒绝，则仅凭此可信not_sent记录释放预留。`PreSendGate` 小实验用真实SDK+MockTransport证明三协议transport收到0次且包装异常仍可恢复；未改当前宿主client。

terminal 与 usage 各自独立：Anthropic 要真实 message_stop 及 stop_reason；Responses 读取 completed/incomplete/failed 及 incomplete_details；Compat 保留 choice.finish_reason，等到 stream 正常关闭再发一次总结事件，usage 尾 chunk 不能覆盖 reason。没有 terminal 的 EOF 标 unknown，不因已有文本判正常。新 RequestOutcome 携带真实终态；兼容默认 StreamEnd 的老调用者，不能从默认 0 推导“用量确为零”。

| 退出条件 | Run.status / stop_reason |
| --- | --- |
| 正常终止、取证/引用检查通过、非预算截断 | completed / finished（不表示语义质量已通过） |
| 预算或迭代结束，已验证部分答案可交付 | partial / token_budget、time_budget、search_limit、open_limit、iteration_limit 或 context_limit |
| 无证据、无命中、修正失败/不足、length/无 terminal，且无可交付部分 | incomplete / no_evidence、no_hits、citation_invalid、budget 或 provider_truncated |
| provider/工具依赖或持久化错误使运行不能继续 | failed / explicit_error；已核验片段可附带但不改 completed |
| 用户取消、消费者关闭/取消 | cancelled / user_cancelled 或 consumer_closed；不得发额外模型请求 |

来源额度耗尽由核心异常 `SourceBudgetExceeded.reason` 传递，适配层不解析英文诊断文案。`context_limit` 表示返回材料窗口的片段数或 Token 容量限制；`token_budget` 表示累计探索额度，不能把片段数耗尽计为 Token 耗尽。最小原文片段及元数据也无法装入窗口时，保留实际限制维度。计量器身份变化、非法计数及未分类的 `source_budget` 错误不属于正常额度耗尽：停止后续模型请求，记录 failed / explicit_error 及硬失败诊断。

有合格证据且剩余硬预算足够的来源软停止允许有界收尾；收尾没有合格证据仍为 incomplete / no_evidence。`context_limit` 也可来自请求门的硬上下文拒绝，`BudgetStop.hard` 仍阻止任何额外收尾请求；此时为 incomplete / context_limit。`budget` 保留为无可交付内容/清理未完成的通用原因，不能生成 partial / budget。所有预算原因共用 domain 的定义，终态仍在持久化前验证。

不要仅因无 tool_calls 或 LoopComplete 事件判 completed。无命中与查询失败分别记录；Tool 错误可在探索预算内由 Agent 决定重试，但单次错误不是成功候选。报告正文通过而保存失败时 artifact_status=failed，完成说明必须明确保存失败。

## 6. 可安装开发包与未来单份实现设想

冻结发行名 `codeplus-agentic-rag`，可导入命名空间 `agentic_rag`。开发源码 `deployment/AgenticRAG/src/agentic_rag/`，独立 pyproject/lock/正式 tests；只按 R05 当前需要建文件。domain/config 不导入 codeplus、torch、transformers、pymilvus。`agentic_rag.adapters.codeplus` 才惰性导入宿主；适配层不存在第二套聊天客户端或 Agent 循环。

未来如决定迁入主包，可把同一目录移动为仓库根 `agentic_rag/`，由 CodePlus 主发行包收录；届时应保持稳定 `import agentic_rag`，避免两个发行包同时拥有同一命名空间，删除重复业务实现，并验证数据 schema、ID、配置语义与历史引用不因迁移改变。本阶段不执行该迁移。

开发关系：R05 的干净核心安装不依赖宿主；R12 的开发环境显式安装本地 CodePlus wheel 与开发 RAG wheel（或分别 editable 用于日常开发），不添加到普通宿主强制依赖，不用 PYTHONPATH 或 cwd 解包。开发包不声明可能形成循环的 `codeplus` 硬依赖，也不从同名 PyPI 项目盲取宿主；适配导入时给明确缺失宿主/版本不兼容错误。本阶段保留独立发行结构。

依赖组冻结职责：核心基础只包含已需要的轻量 schema 依赖（R05 用 pydantic，与宿主约束取交集）；`milvus` 放 R02 已验证的 pymilvus/client 依赖；`local-models` 放 torch/transformers/tokenizer 等本地模型 worker 依赖，CUDA wheel 索引与环境锁单列，不把 CPU torch 误当验证版本；`dev`/dependency-group 放 pytest/build 等开发工具，不能进入运行依赖。Windows CPU 宿主与 CUDA worker 可用独立环境；future API extra 只有正式实现后才增加，不先建空组。R03 能力事实不等于任意平台包已兼容；本阶段不设计主发行专用 extras。

未来若正式发行 Compose 资源，可采用 `agentic_rag/resources/compose.yaml`，使用 `importlib.resources` 读取并明确导出到用户部署目录；不应维护两个手工副本或依赖源码当前目录。本阶段以现有 Compose/真实 Milvus 功能验收为准，不要求发行包内资源交付。

R04 当时记录根 force-include 指向旧 `deployment/knowledge/compose.yaml`；该历史诊断不能代替当前打包核查。R05 独立包显式包含必要源码/资源/LICENSE 并排除评测与运行数据；本阶段不修改根构建配置以迁入主包。

现行门槛为：R05 已验证开发 wheel 与 sdist→wheel 的核心安装；R12 验证 Windows 开发宿主接入；R23/R24 验证完整 Windows 功能、真实 worker/Milvus/Agent、历史引用及质量。跨平台发行安装和主包迁入不属于现行门槛。

## 7. 宿主精确白名单与旧清理顺序

此表仅为后续任务允许提出改动的接口清单，R04 没有修改其中任何生产文件。执行时 Leader 在新基线上逐项授权，发现额外文件先说明实际调用链，不能泛化为整个 codeplus/。

| 后续阶段 | 具体路径 | 责任 |
| --- | --- | --- |
| R12（R19完善同接点） | `codeplus/agent.py`、新增 `codeplus/run_policy.py` | 可选策略、共用工具受理/输出决定、finally、所有 terminal；保留两个循环 |
| R12 | `codeplus/client.py`、`codeplus/tools/base.py` | 可选 ModelCallControl，三协议硬 cap、raw usage/terminal、独占 SDK/HTTP、兼容旧事件 |
| R12 | `codeplus/context/manager.py`、`codeplus/conversation.py`、`codeplus/conversation_pairing.py`、`codeplus/serialization.py` | 来源 sidecar、变换传播、compact 计量/穿透；默认序列化输出不变 |
| R12 最小 CLI/TUI，R21 完整装配 | `codeplus/__main__.py`、`codeplus/app.py`、`codeplus/config.py`、`codeplus/validator.py`、`.codeplus/config.yaml.example`、`codeplus/commands/handlers/knowledge.py`、`codeplus/commands/handlers/__init__.py` | 惰性 feature/任务配置、TUI与-p消费者 aclosing、use/ask/off薄层；R12开发限定fixed Dense，不复用权限 `--mode`，不实现全套管理 |
| R12 限制门，R21 正式启用 | `codeplus/remote.py` | R12只允许显式拒绝新RAG请求/共享状态误装配的限制门；R21才接完整策略、流式批准正文和aclosing，启用时补取消/断连回归 |
| R20/R21 | `codeplus/app.py`、`codeplus/commands/handlers/knowledge.py`、`codeplus/commands/handlers/__init__.py`、`codeplus/commands/completion.py`、`codeplus/commands/handlers/session.py`、`codeplus/memory/session.py` | 完善基础管理命令、报告和会话元数据；旧清理删过的命令文件按新适配薄层重建，不恢复旧实现 |
| R21 精确补充 | `codeplus/web_content.py` 的既有事件 handler | 消费 `permission_resolved`，按现有 perm-id 关闭已回答、取消或超时结束的权限弹窗；不新增页面 |

工具注册使用现有 ToolRegistry API 在适配层创建新 registry，R04 不预先批准改 `codeplus/tools/__init__.py` 或 `tools/agent_tool.py`；R12 证明无必要则不动。保存复用 `codeplus/tools/write_file.py` 与权限模块现有行为，也不预先批准改其规则。新包集成测试与已有宿主回归按实际功能风险选择执行；本阶段不迁移源码或复制两套测试。R04 的 `test_host_contract.py` 是正式保留的小实验，不能命名成端到端通过。

旧清理输入来自 `C:/Users/18221/.codex/worktrees/144f/rag-cleanup-review`。本次核对 README/changes.json/independent-review/patch：patch SHA256 `ddaeacc7e481ea29958bfcef7dcb1e7a1f15fc79a8a38242a3012ef46511aedc`；changes SHA256 `33ee88e37f4e9194f9d7641390d05ebcaa761dea05cb35563389f833a2b449d8`。56 路径的当前状态与精确差异记录在 R04-static-evidence；旧报告 718/197 passed 是历史工作树证据，不能当本次结果。

合入顺序和唯一责任冻结如下：

1. R04 只交接口实验/设计，Leader 独立验收后单独本地提交。R05–R11 在独立目录推进。
2. **R12 写宿主 hook 前**由 Leader 单独分派“旧清理整合”，新的唯一执行者接管当前最新 HEAD 的精确补丁；旧任务保持只读。逐文件核对 before/after，保存新基线，禁止整包盲 apply 或整工作树覆盖。
3. R01 已交付的 `eval/RAG-eval/check.py`、`run.ps1` 不接收旧 patch；`dataset_io.py`、`replay.py`、native report wrapper、正式 R01 tests保持权威。旧 patch 对 `tests/test_multihop_evaluation.py` 不直接应用；如需验证清理，只增加与既有冻结协议一致的 seam 用例，由 Leader/R01 归属复核。eval README/benchmark 仅接必要文档接缝，不改变原题/609语料/gold/upstream指纹/评分分母。完整继承 MultiHop 替换仍未提交，不借此任务顺带入 Git。
4. agent/config/commands/app/remote/session/packaging 等碰撞以“先清旧实现、验收普通宿主、单独提交”为序；根依赖锁仅按清理实际需求裁剪。清理执行者解决本阶段冲突，Leader独立复跑当前候选树并只 stage 精确范围；继承 README/compose 删除按归属保护。旧报告的“顺带提交评测和旧 compose 删除”建议不沿用。
5. 清理提交后重新定位本表接点、复跑 R04 实验；R12 执行者只在清理后基线接新策略，负责新接口冲突，不能回头套旧清理 patch 删除新 hook。旧临时目录的历史删除阻塞不由 R04 处理，也不作为清理已完成条件虚报。

R12 必测两入口正常/无命中/工具失败/权限拒绝/三路执行/并行预算、每种裁剪/compact/pair修复、三 provider caps与真实终态、取消/consumer-close/finally、一次引用修正、未知usage/隐藏重试、普通任务输出及共享 client 未污染。联网模型/真实 Agent 的完整功能与质量仍由后续 R23/R24 核验；本设计和 MockTransport 只解除接口可实施性问题。

R20 退修在同一 `ModelCallControl` 追加默认关闭的 `json_output`。仅知识 compat 禁用工具的 finalize/citation_repair 启用原生 `response_format={"type":"json_object"}`；agent 探索、compact/无 control 的普通调用均不带字段，其他协议不扩展。`client.py` 在 SDK 序列化前按 control 加固定字段，policy 的完整请求预览遵循同一选择，meter 只接受该精确格式并交给已固定 encoder 的 system response format 模板。原始 HTTP bytes 仍由最后发送门校验和预留，meter 的冻结身份记录模板契约；原 tokenizer/encoder、模型、预算和旧运行记录不变。公开文本仍严格解析为单个 JSON 对象，报告正文位于 markdown 字段。提供方若把 DSML 等工具标记返回在正文中，它们只是文本，不会解析为宿主工具调用。
