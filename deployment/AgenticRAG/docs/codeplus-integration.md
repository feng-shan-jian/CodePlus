# R12 宿主开发接入

这是中间开发能力，验收/提交状态以 `implementation-checklist.md` 为准。R13–R24 的生命周期、完整检索、模式、报告、恢复、命令、调度与评测继续执行；本阶段不验收双平台发行。

显式安装本仓库构建的 `codeplus` wheel 与本目录构建的 `codeplus-agentic-rag` wheel 到同一个 Windows CPU 宿主环境；GPU worker 使用另一个独立环境与 `local-models` 锁。不要从同名公共包推断宿主，不使用 `PYTHONPATH` 或仓库 cwd 使未安装源码可见。两份原锁的共享依赖版本不同；R12 已验证组合锁见 `implementation-records/R12-windows-integration-lock.txt`。该记录是开发接入证据，不宣称 Linux 正式发行安装或主包迁入通过。

宿主配置增加可选绝对路径 `knowledge_development_config`。该 JSON 严格解析为 `DevelopmentConfig`，包含完整 `knowledge`、`worker`、`answer_tokenizer`，以及显式的 `explore_output_cap`、`finish_input_upper`、`finalize_output_cap`、`repair_output_cap`、`compact_output_cap`、`max_iterations`、`max_tool_attempts`、`cleanup_grace_ms`。完整合成验收配置与试验值见 `implementation-records/R12-trial-config-02.json`；其中 Temp 数据/运行路径应换成自己的绝对路径，不能复制该机器的缓存位置当默认值。

先通过核心创建并发布可查询版本。TUI 使用 `/knowledge use <库 UUID>` 选择，`/knowledge ask <问题>` 提问，`/knowledge off` 清除选择。普通文本不会因选择库而隐式变成知识库请求。非交互使用 `codeplus -p "问题" --knowledge-library <库 UUID>`；`--output-format stream-json` 的最终 result 带真实 status/stop_reason，正文是已校验引用产物。Windows 管道建议 Python `-X utf8` 或一致的 UTF-8 环境，避免采集端和输出端编码不同。

回答模型复用当前 CodePlus provider，不修改用户配置。R12 硬输入预算目前只验证官方 DeepSeek 端点的 `deepseek-chat`、`deepseek-reasoner` 文本模式，实际响应身份为 `deepseek-flash`（V4.1）。固定官方 tokenizer 文件由 `answer_tokenizer` 指定并校验哈希；文件不随发行包或 Git 提交。模型、端点、模板、tokenizer 或参数不受支持时明确失败，不换模型、不猜估算值。三协议宿主适配的 cap/terminal/usage 分别有受控 SDK 测试，不能据此宣称三个服务商都具备生产计量能力。

目前开发配置必须显式 fixed、rerank=false；R17 支持 dense、bm25、hybrid 三路，运行中固定路线，工具仍只接收 query。BM25 不连接 Embedding worker，候选排名与融合诊断不进入模型正文，详见 [检索调用与追踪](retrieval.md)。最终 auto 权限继续由 R19 实现。模型可多轮调用 `knowledge_search`/`knowledge_open`，其他工具、MCP、子 Agent、团队、外部通知入口不可进入该 run。可执行或异步 hooks 不支持；同步 prompt hooks 进入同一完整计量。Remote 明确返回 feature_not_available。活跃知识库任务由自己的循环管理 compact，UI 在取消收束前仍保持 busy；普通手动 compact 不并发修改其上下文。

每次实际 HTTP 请求先对完整最终序列化输入留出保守上界及输出硬上限。未知用量保留预留，已知违约保留真实超限事实并停止。开始时保留 finalize 和一次原 Agent citation repair 的输入与输出预算；软探索耗尽可收尾，硬截止/取消不补发模型。source-return 成本、已受理 search/open 和实际 LLM token 分开登记，不重复计数。无依据、工具失败、截断、修正仍失败等返回明确非 completed 状态；无效答案和思考不先流式显示。

引用只来自实际传输正文与可信偏移 sidecar 的交集。spill、pair repair、三协议字段展开和 compact 不从文本猜回来源。摘要不能新增引用资格。引用不匹配只允许同一 Agent 修正一次，错误给具体偏移/码点/长度；不放宽精确引用标准。

取消后先停止受理、撤销权限、结算已受理请求，再收束实际协程、线程和 GPU 请求句柄。grace 内未完成则终止 run 并保留 cleanup_pending 与 revision pin，真实 worker_finished 或确认进程结束后才能释放。已持久化终态后才到达的消费者取消仍向调用者传播，但不重写已经提交的终态；返回记录与数据库一致。完整崩溃恢复由 R15 继续实现，当前诊断不得擅自释放无法证明完成的读者。
