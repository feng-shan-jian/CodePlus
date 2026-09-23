# 宿主开发接入

这是中间开发能力，验收/提交状态以 `implementation-checklist.md` 为准。R13–R24 的生命周期、完整检索、模式、报告、恢复、命令、调度与评测继续执行；本阶段不验收双平台发行。

显式安装本仓库构建的 `codeplus` wheel 与本目录构建的 `codeplus-agentic-rag` wheel 到同一个 Windows CPU 宿主环境；GPU worker 使用另一个独立环境与 `local-models` 锁。不要从同名公共包推断宿主，不使用 `PYTHONPATH` 或仓库 cwd 使未安装源码可见。两份原锁的共享依赖版本不同；R12 已验证组合锁见 `implementation-records/R12-windows-integration-lock.txt`。该记录是开发接入证据，不宣称 Linux 正式发行安装或主包迁入通过。

宿主配置增加可选绝对路径 `knowledge_development_config`。该 JSON 严格解析为 `DevelopmentConfig`，包含完整 `knowledge`、`worker`、`answer_tokenizer`，以及显式的 `explore_output_cap`、`finish_input_upper`、`finalize_output_cap`、`repair_output_cap`、`compact_output_cap`、`max_iterations`、`max_tool_attempts`、`cleanup_grace_ms`。完整合成验收配置与试验值见 `implementation-records/R12-trial-config-02.json`；其中 Temp 数据/运行路径应换成自己的绝对路径，不能复制该机器的缓存位置当默认值。

TUI 和已有 Remote 浏览器会话使用相同的 `/knowledge` 命令。先创建库，再导入 Markdown 或纯文本；`create` 和 `use` 会选择库，普通消息仍按普通任务执行。

`ask/report/continue` 后的正文作为研究问题交给 Agent；即使正文以 `/knowledge` 或 `/session` 开头，也不会再次作为命令执行。

```text
/knowledge create 我的资料
/knowledge import "D:\资料\手册.md" "D:\资料\记录.txt"
/knowledge status
/knowledge sources
/knowledge ask --mode auto 这两份资料有哪些共同结论？
/knowledge reimport <文档UUID> "D:\资料\修订手册.md"
/knowledge remove <文档UUID>
/knowledge open <历史引用UUID>
/knowledge use <另一库UUID>
/knowledge off
```

`status` 显示当前发布版、实际模型身份与配置差异、待恢复批次及文件处理结果；未选库时列出各库。`sources --revision <版本UUID>` 回看历史来源，`open` 读取本库已保存引用的原文，即使该文档已更新或删除。恢复已有会话时读取选中的库 ID 和最近运行 ID，新建会话的选择为空。旧库绑定元数据继续忽略；历史正文不会作为新证据注入。

相同管理命令可直接用于 `-p`：先执行 `codeplus -p "/knowledge create 我的资料" --output-format stream-json`，取得库 UUID，再执行 `codeplus -p '/knowledge import "D:\资料\手册.md"' --knowledge-library <库UUID> --output-format stream-json`。非交互问题继续使用 `codeplus -p "问题" --knowledge-library <库UUID>`；`-p` 的选择只对本次调用有效。Windows 管道建议 Python `-X utf8` 或一致的 UTF-8 环境。

管理与问答的最终 JSON 都带真实 `status/stop_reason`。知识任务进程退出码为：完成 `0`，部分完成或未完成 `2`，等待明确选择 `3`，失败 `1`，取消 `130`；普通任务原退出行为不变。导入部分文件失败时保留可用文件及逐文件错误。取消与发布提交同时发生时，结果同时保留 `cancelled`、实际 `operation_status` 和发布回执，应按回执读取已提交版本。

失败文件使用 `/knowledge retry <批次UUID>`；原输入已变化时需明确追加 `--accept-input-changes`。中断批次先用 `/knowledge recover <批次UUID>` 查看，再选择 `/knowledge recover <批次UUID> --choice continue` 或 `/knowledge abandon <批次UUID>`。另一个进程仍持有 owner 时显示忙，结束后才能恢复。恢复与研究 `continue` 是不同操作。

配置模型改变后，`/knowledge model` 显示差异、proposal ID 和可用选择。初次重建使用 `/knowledge model <proposalUUID> --choice confirm`；失败后只能明确 `--choice retry` 重试，或 `--choice keep_original` 继续使用原发布模型。初次 `retry` 不授权重建，失败后的 `confirm` 不偷偷重试；空选择、取消和无交互输入均不表示同意。普通导入、删除无需额外确认。

TUI 的 Ctrl-C 与 Remote 的取消会请求停止真实后台导入/恢复/重建，线程和模型请求结束前仍显示忙。Remote 由发起连接拥有该任务；它断连会取消任务，旁观连接断开不会取消。已有权限弹窗在回答、取消或超时结束后关闭，命令完成事件在实际任务结束后发送。缺少可选包或配置时明确报错，不把知识问题退回普通聊天。

回答模型复用当前 CodePlus provider，不修改用户配置。R12 硬输入预算目前只验证官方 DeepSeek 端点的 `deepseek-chat`、`deepseek-reasoner` 文本模式，实际响应身份为 `deepseek-flash`（V4.1）。固定官方 tokenizer 文件由 `answer_tokenizer` 指定并校验哈希；文件不随发行包或 Git 提交。模型、端点、模板、tokenizer 或参数不受支持时明确失败，不换模型、不猜估算值。三协议宿主适配的 cap/terminal/usage 分别有受控 SDK 测试，不能据此宣称三个服务商都具备生产计量能力。

知识库默认使用 auto，功能配置可改为 fixed。单次覆盖使用 `codeplus -p "问题" --knowledge-library <UUID> --knowledge-mode fixed`，或 `/knowledge ask --mode auto <问题>`。未传选项的旧入口保持可用。优先级为本次显式选择、功能配置、auto 默认值；即使显式值与配置相同，也在 `host_runs.frozen.mode` 中保存 `explicit` 来源。模式、QA/report 预算和版本在开始时冻结，改配置不会影响在途任务。宿主 `--mode` 仍只表示权限模式；知识库非交互运行遇到 ask 权限时拒绝，不能把没有交互输入当作同意。

fixed 的 search 工具只接收 query，每次使用绑定路线和重排要求，可改写问题、多轮搜索和 open。auto 额外接收 `strategy: dense|bm25|hybrid` 和 `rerank: bool`；未提供的选项使用运行基础配置，不继承上一次调用。候选数、模型、库、版本、预算不能由模型覆盖；核心直接调用也执行相同的模式约束。BM25 不请求查询 Embedding；无重排时不连接模型，启用重排时只调用精排模型。失败返回阶段、类别、call_id、允许动作和本次选择，下一次 retry/改选必须是 Agent 新的显式调用，程序不自动降级。

`KnowledgePolicy(..., task_kind='report', mode='fixed')` 和 `load_policy(..., task_kind=..., mode=...)` 接通两类预算，旧调用默认 qa。R20 的 report 提示同一 Agent 在既有 JSON 的 `markdown` 字段组织结论、比较、分歧、证据、推断标识和局限；完整响应不能在 JSON 前后添加序言或步骤宣告。中文问题由同一模型生成/改写英文 query，中文答复，英文直接引文保留原样。预算类型与检索模式正交，模式来源另存 JSON，不修改旧 `RunConfiguration` 的序列化与指纹。语义质量和约束保留需要实测，程序引用校验本身不保证结论正确。

知识 compat 仅在不提供工具的 finalize/citation_repair 请求通过可选 `ModelCallControl.json_output` 使用提供方原生 `response_format={"type":"json_object"}`；agent 探索保留普通工具调用，compact 和普通宿主请求也不带此字段。请求预览和最终 HTTP 原始正文门使用同一目的选择，原固定 encoder 的 response format 模板完整计量实际字段，不添加经验常数。计量身份为 `deepseek-v41-full-template-json-object-utf8-upper-v2`；已保存运行的冻结身份不改写。严格 JSON 解析、精确引用校验和既有一次修正仍保留，不剥离前缀或另增重试。

原生格式参数不能替代实际输出校验：真实提供方曾返回空白及损坏的引号转义。收尾超窗时，先移除冗余普通消息，再对当前可信正文做完整工具结果的等价去重；只有去重后的完整请求能装入原窗口才采用。比较当前 document_version、精确区间和实际正文，保留完整 call/result 配对；大结果试算不合时保留原历史继续既有淘汰，不提前丢掉仍可容纳的小结果。不会从归档补回正文或增加预算。

报告与续研使用原有入口：

```text
/knowledge report --output "D:\Reports\certificates.md" --mode auto 仅比较2024年的认证，说明证据与局限
/knowledge continue --output "D:\Reports\certificates-followup.md" 优先补查夜间资格的分歧
/knowledge continue --run <运行UUID> 继续补查尚未解决的问题
codeplus -p "生成中文研究报告" --knowledge-library <库UUID> --knowledge-report report.md --output-format stream-json
codeplus -p "继续补查分歧" --knowledge-library <库UUID> --knowledge-continue <运行UUID> --knowledge-report followup.md --output-format stream-json
```

`--knowledge-task qa|report` 可显式选任务类型；`--knowledge-report` 默认选择 report，不能与 qa 同用。TUI `continue` 默认承接本会话最近运行，也可指定 `--run`；`-p` 必须明确给 `--knowledge-continue`。新一轮默认 QA，有输出路径时使用 report 预算。相对路径按宿主工作目录解析，路径来自用户入口，模型不能提交任意 WriteFile。`KnowledgePolicy` 接收可选 `report_path`、UUID 类型 `parent_run_id`，`load_policy` 接受它们的字符串形式。

引用整份校验通过后，宿主才用原 WriteFile 的权限、读取后覆盖和错误规则落盘。已有文件必须先在普通宿主通过 ReadFile 读取，且此后没有变化；新建的非交互会话无法继承旧读取缓存。权限拒绝、无交互 ask 或实际写入错误均返回失败，不自动批准。`acceptEdits` 等宿主权限模式的意义不变。继续研究必须选择新的报告路径，历史报告路径即使已读也不能复用。

`ValidatedArtifact.sha256` 对应逻辑 Markdown UTF-8；`RunOutcome.save.sha256` 对应实际回读的文件字节，Windows 换行转换时两者可能不同。`save` 还给出绝对路径、字节数、`saved|failed|interrupted` 和原因。`answer_status` 与保存结果分别存于 `host_runs.detail`；保存失败的整体状态为 `incomplete/report_save_failed`。写成后才取消/超时仍保留真实保存结果，整体运行仍标取消/未完成。报告正文与运行状态需一起读取，部分答案文件不会伪装为完整完成。

所有终态的 `RunOutcome`、completion 回调 `run_status` 和 `-p` 最终 JSON 都包含 `run_id`；无有效 artifact 的预算停止也可显式继续。`research.rounds` 给出每轮父子关系、库/版本、预算、状态、停止原因、用量和保存信息，`research.total_usage` 汇总本条父链，任何未知 token 分项继续保持 null。TUI 显示运行 ID、本轮及累计 token；适配层 `research.history(catalog, run_id)` 可重新读取逐轮账本。

继续使用 `Catalog.start_current_run(parent_run_id=...)` 原子绑定开始时最新发布版。公开进度写入现有 `host_runs.detail.progress`，保存目标、按序的真实用户请求/约束、覆盖/待查问题、公开发现及来源线索。模型输出可添加 `progress`，包含 `covered/pending/findings/revised/unverified` 字符串数组；旧 `{markdown,citations}` 答复仍可用，其已校验公开答案正文作为待核后备线索，不复制附加的原文脚注。中间失败轮不会清掉祖先记录。历史发现始终待核，只有本轮 search/open 正文的实际送达回执能产生新证据，即使版本相同也不继承旧 evidence ID。换库、运行仍在进行、目标或记录缺失均给出明确缺口；没有自动后台续研。

候选排名与融合/精排诊断不进入模型正文，详见 [检索调用与追踪](retrieval.md)。其他工具、MCP、子 Agent、团队、外部通知入口不可进入知识库 run。可执行或异步 hooks 不支持；同步 prompt hooks 进入同一完整计量。活跃知识库任务由自己的循环管理 compact，UI 在取消收束前仍保持 busy；普通手动 compact 不并发修改其上下文。

每次实际 HTTP 请求先对完整最终序列化输入留出保守上界及输出硬上限。未知用量保留预留，已知违约保留真实超限事实并停止。开始时保留 finalize 和一次原 Agent citation repair 的输入与输出预算；软探索耗尽可收尾，硬截止/取消不补发模型。source-return 成本、已受理 search/open 和实际 LLM token 分开登记，不重复计数。无依据、工具失败、截断、修正仍失败等返回明确非 completed 状态；无效答案和思考不先流式显示。

引用只来自实际传输正文与可信偏移 sidecar 的交集。spill、pair repair、三协议字段展开和 compact 不从文本猜回来源。摘要不能新增引用资格。引用不匹配只允许同一 Agent 修正一次，错误给具体偏移/码点/长度；不放宽精确引用标准。

官方 DeepSeek compat 正常请求在两条 Agent 循环的 compact/通知之后、SDK stream 之前，按完整消息、工具定义和实际 JSON 转义做窗口裁剪。裁剪只取当前消息中可信 sidecar 覆盖的正文；spill 预览不从落盘全文或归档补回正文。工具元数据的 `returned_spans` 与正文同步，过期行号移除；裁剪 open 后签发从实际保留尾部继续的 cursor，保持原 cursor 范围。最后真实 raw-body 门仍独立计量和校验，不在 permit 中改 bytes。

合法显式 cursor 翻页通过 `SourceSession.open(..., before_read=...)` 的可选宿主回调，在既有次数/时间/累计预算受理与 cursor 校验之后，移出对应上一页的 tool pair，保留同消息其他调用。prepared/not_sent 只同步当前窗口，历史 confirmed 引用资格仍保留，累计用量不减少；读取失败会恢复上一页供后续收尾。完整最小请求仍装不下时明确停止。R18 的原始对照与失败记录保持不变，R19 的新宿主验收不替代 R23 质量与数值冻结。

取消后先停止受理、撤销权限、结算已受理请求，再收束实际协程、线程和 GPU 请求句柄。grace 内未完成则终止 run 并保留 cleanup_pending 与 revision pin，真实 worker_finished 或确认进程结束后才能释放。已持久化终态后才到达的消费者取消仍向调用者传播，但不重写已经提交的终态；返回记录与数据库一致。完整崩溃恢复由 R15 继续实现，当前诊断不得擅自释放无法证明完成的读者。
