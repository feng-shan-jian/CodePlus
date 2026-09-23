# 宿主开发接入

这是中间开发能力，验收/提交状态以 `implementation-checklist.md` 为准。R13–R24 的生命周期、完整检索、模式、报告、恢复、命令、调度与评测继续执行；本阶段不验收双平台发行。

显式安装本仓库构建的 `codeplus` wheel 与本目录构建的 `codeplus-agentic-rag` wheel 到同一个 Windows CPU 宿主环境；GPU worker 使用另一个独立环境与 `local-models` 锁。不要从同名公共包推断宿主，不使用 `PYTHONPATH` 或仓库 cwd 使未安装源码可见。两份原锁的共享依赖版本不同；R12 已验证组合锁见 `implementation-records/R12-windows-integration-lock.txt`。该记录是开发接入证据，不宣称 Linux 正式发行安装或主包迁入通过。

宿主配置增加可选绝对路径 `knowledge_development_config`。该 JSON 严格解析为 `DevelopmentConfig`，包含完整 `knowledge`、`worker`、`answer_tokenizer`，以及显式的 `explore_output_cap`、`finish_input_upper`、`finalize_output_cap`、`repair_output_cap`、`compact_output_cap`、`max_iterations`、`max_tool_attempts`、`cleanup_grace_ms`。完整合成验收配置与试验值见 `implementation-records/R12-trial-config-02.json`；其中 Temp 数据/运行路径应换成自己的绝对路径，不能复制该机器的缓存位置当默认值。

先通过核心创建并发布可查询版本。TUI 使用 `/knowledge use <库 UUID>` 选择，`/knowledge ask <问题>` 提问，`/knowledge off` 清除选择。普通文本不会因选择库而隐式变成知识库请求。非交互使用 `codeplus -p "问题" --knowledge-library <库 UUID>`；`--output-format stream-json` 的最终 result 带真实 status/stop_reason，正文是已校验引用产物。Windows 管道建议 Python `-X utf8` 或一致的 UTF-8 环境，避免采集端和输出端编码不同。

回答模型复用当前 CodePlus provider，不修改用户配置。R12 硬输入预算目前只验证官方 DeepSeek 端点的 `deepseek-chat`、`deepseek-reasoner` 文本模式，实际响应身份为 `deepseek-flash`（V4.1）。固定官方 tokenizer 文件由 `answer_tokenizer` 指定并校验哈希；文件不随发行包或 Git 提交。模型、端点、模板、tokenizer 或参数不受支持时明确失败，不换模型、不猜估算值。三协议宿主适配的 cap/terminal/usage 分别有受控 SDK 测试，不能据此宣称三个服务商都具备生产计量能力。

知识库默认使用 auto，功能配置可改为 fixed。单次覆盖使用 `codeplus -p "问题" --knowledge-library <UUID> --knowledge-mode fixed`，或 `/knowledge ask --mode auto <问题>`。未传选项的旧入口保持可用。优先级为本次显式选择、功能配置、auto 默认值；即使显式值与配置相同，也在 `host_runs.frozen.mode` 中保存 `explicit` 来源。模式、QA/report 预算和版本在开始时冻结，改配置不会影响在途任务。宿主 `--mode` 仍只表示权限模式；知识库非交互运行遇到 ask 权限时拒绝，不能把没有交互输入当作同意。

fixed 的 search 工具只接收 query，每次使用绑定路线和重排要求，可改写问题、多轮搜索和 open。auto 额外接收 `strategy: dense|bm25|hybrid` 和 `rerank: bool`；未提供的选项使用运行基础配置，不继承上一次调用。候选数、模型、库、版本、预算不能由模型覆盖；核心直接调用也执行相同的模式约束。BM25 不请求查询 Embedding；无重排时不连接模型，启用重排时只调用精排模型。失败返回阶段、类别、call_id、允许动作和本次选择，下一次 retry/改选必须是 Agent 新的显式调用，程序不自动降级。

`KnowledgePolicy(..., task_kind='report', mode='fixed')` 和 `load_policy(..., task_kind=..., mode=...)` 接通两类预算，旧调用默认 qa。report 在 R19 只选择预算，报告保存与继续研究由 R20 接通。预算类型与检索模式正交，模式来源另存 JSON，不修改旧 `RunConfiguration` 的序列化与指纹。

候选排名与融合/精排诊断不进入模型正文，详见 [检索调用与追踪](retrieval.md)。其他工具、MCP、子 Agent、团队、外部通知入口不可进入知识库 run。可执行或异步 hooks 不支持；同步 prompt hooks 进入同一完整计量。Remote 明确返回 feature_not_available。活跃知识库任务由自己的循环管理 compact，UI 在取消收束前仍保持 busy；普通手动 compact 不并发修改其上下文。

每次实际 HTTP 请求先对完整最终序列化输入留出保守上界及输出硬上限。未知用量保留预留，已知违约保留真实超限事实并停止。开始时保留 finalize 和一次原 Agent citation repair 的输入与输出预算；软探索耗尽可收尾，硬截止/取消不补发模型。source-return 成本、已受理 search/open 和实际 LLM token 分开登记，不重复计数。无依据、工具失败、截断、修正仍失败等返回明确非 completed 状态；无效答案和思考不先流式显示。

引用只来自实际传输正文与可信偏移 sidecar 的交集。spill、pair repair、三协议字段展开和 compact 不从文本猜回来源。摘要不能新增引用资格。引用不匹配只允许同一 Agent 修正一次，错误给具体偏移/码点/长度；不放宽精确引用标准。

官方 DeepSeek compat 正常请求在两条 Agent 循环的 compact/通知之后、SDK stream 之前，按完整消息、工具定义和实际 JSON 转义做窗口裁剪。裁剪只取当前消息中可信 sidecar 覆盖的正文；spill 预览不从落盘全文或归档补回正文。工具元数据的 `returned_spans` 与正文同步，过期行号移除；裁剪 open 后签发从实际保留尾部继续的 cursor，保持原 cursor 范围。最后真实 raw-body 门仍独立计量和校验，不在 permit 中改 bytes。

合法显式 cursor 翻页通过 `SourceSession.open(..., before_read=...)` 的可选宿主回调，在既有次数/时间/累计预算受理与 cursor 校验之后，移出对应上一页的 tool pair，保留同消息其他调用。prepared/not_sent 只同步当前窗口，历史 confirmed 引用资格仍保留，累计用量不减少；读取失败会恢复上一页供后续收尾。完整最小请求仍装不下时明确停止。R18 的原始对照与失败记录保持不变，R19 的新宿主验收不替代 R23 质量与数值冻结。

取消后先停止受理、撤销权限、结算已受理请求，再收束实际协程、线程和 GPU 请求句柄。grace 内未完成则终止 run 并保留 cleanup_pending 与 revision pin，真实 worker_finished 或确认进程结束后才能释放。已持久化终态后才到达的消费者取消仍向调用者传播，但不重写已经提交的终态；返回记录与数据库一致。完整崩溃恢复由 R15 继续实现，当前诊断不得擅自释放无法证明完成的读者。
