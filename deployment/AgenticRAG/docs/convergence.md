# AgenticRAG 本轮收敛记录

清理任务：`01a0c9c1-0a2a-7eb3-9e79-a007a6a9d1e4`。唯一集成写入者为功能 Leader；清理仅在独立 `c-woker/agentic-rag-convergence` 工作树执行，不推送。

本轮状态与功能 R00–R26 分开。修复、自测、独立审查、接入提交分别记录，不能以某一步替代后续步骤。

| 问题 | 已复核事实 / 处理边界 | 验证与接入 |
| --- | --- | --- |
| BS-01 | 来源额度依赖英文异常文案，片段上限落入非法 partial/budget；source_budget 还混有计量故障。第一批改为明确原因，新增 context_limit，共用终态原因定义；不放宽 partial/budget | 第一批已在R14提交上接入，Leader实际安装独立回归core658/host715、新独立清理及Leader复核通过；实际本地SHA见postcommit记录 |
| BS-02 | R13 候选多次重读旧成员；完整发布所需向量复制必须保留 | R14 冻结后重验；W1 优先，不写正在实现的恢复/发布代码 |
| BS-03 | 单条输入/处理检查点读取触发全批清单与条目验证 | R14 冻结后确定窄读取和批次验证边界；W1 优先 |
| BS-04 | 已复用 quality 补丁：来源调用准入/异常归并；归档同一次读取完成哈希验证后返回，不跨调用缓存 | 第一批归档/审计已接入并通过Leader回归、独立清理及复核；重复read_ref的操作内复用属于第二批 |
| BS-05 | 完成请求载荷被 client 强持有；累计 session 上限属于现行防重契约，不能仅改活跃计数 | Leader 已批准连接绑定、活跃句柄及安全轮换设计；独立 CPU 实施后在 W2（R15 提交后、R16 前）验收 |
| BS-06 | 首层推理 hook 仅诊断消费但已列为观察契约；Milvus load/release 需按 R14 最终路径重验 | Leader 已批准显式可选诊断开关，保留配置/快照指纹；真实 GPU 验收待 W2，不宣称性能收益 |
| BS-07 | publication 对具体 Milvus 类型及验证实现耦合 | R14 冻结后重验；保留真实内容验证与端点/版本绑定；W1 优先 |
| BS-08 | SQL 迁移执行流程重复；既有 SQL 字节/哈希/顺序须保持 | R14 新迁移稳定后收敛；W1 优先 |
| 材料 | 3 份逐字节副本；R09 fixture 有正式消费者；7 份历史语料处理报告各有独立证据 | Leader 已逐项核验 raw bytes/消费者并批准独立材料补丁；W1 末适配最新 SHA，尚未删除 |

## 第一批候选

- 基线：R13 `a4fede1c3f0a09202710e53debdbac3307c0911d`。
- 复用补丁：quality 的 SHA256 `3e42f222a7014b7d172f53c06e7f201944e7fdfdfe45b0788ac4b3c949081054`，五个原路径在 R12→R13 未变化。
- 对外记录字段、ErrorInfo 格式、工具签名、归档格式、SQL、运行 pin 协议保持。Run 原因新增 context_limit，旧合法记录可读；旧开发包不承诺读取新原因。
- 来源窗口软停止允许剩余额度内收尾；有合格引用才 partial，无证据 incomplete/no_evidence。计量故障或未知 source_budget 错误为 failed/explicit_error，不收尾。已有请求门 context_limit 仍 hard=True、拒绝本次请求且不再收尾，终态明确为 incomplete/context_limit；这是经 Leader 确认的可见状态补全。
- `Record.model_copy` 原本已在 SQL 更新前校验终态；无需修改 `storage/runs.py`。正式回归检查非法组合不写库，随后合法结束仍可成功。
- 归档 read 从“verify 完整读 + read_bytes 完整读”收为一次打开/逐块哈希/同批字节返回；每次调用仍重验，空、二进制、多块、损坏及读取失败均保留验证。
- 准入事务、失败/拒绝计数、call_id、原异常/cause、候选提交前 owner/run/预算复验保持。

验证环境：Windows Python 3.14.3，独立 copy 环境，明确 editable 安装当前两个包，`-I` 导入路径已核对为本工作树。没有改共享环境，没有启动 GPU、Docker、Milvus 或联网模型。

首次开放版本安装下，聚焦检查为 32 passed（31.55 秒），较宽回归为 **352 passed / 12 failed / 1 skipped**（233.27 秒）。12 项失败涉及 Anthropic 1.7.0/httpx 2 客户端参数兼容和 OpenAI 3.17.0 流式读取异常包装，未修改生产客户端或放宽断言。随后仅在本任务环境内按既有 `implementation-records/R12-windows-integration-lock.txt` 重新安装：anthropic 0.98.1、openai 2.34.0、mcp 1.27.0、httpx 0.28.1；这不构成开放依赖兼容性修复。

以下最终命令统一由本工作树安装的 `python -I -B -m pytest` 执行，使用任务独有 `--basetemp`，未共用其他任务的安装、缓存或测试数据：

- 受影响回归：`deployment/AgenticRAG/tests/{test_archives,test_sources,test_source_identity,test_evidence_citations,test_input_snapshots,test_processing_checkpoints,test_storage,test_configuration,test_domain,test_codeplus_integration,test_request_delivery,test_run_budget}.py -q --tb=short`（花括号仅表示逐个列出的文件）——**375 passed / 1 skipped**，366.21 秒。唯一跳过项为未设置资源变量的固定生产 tokenizer 用例，已按下一条单独补验。
- 生产计量：设置 `R12_ANSWER_TOKENIZER` 指向固定 revision `dba1be0a40aa45a94ad051997016db3960a90277` 的 DeepSeek-V4.1-Flash tokenizer，SHA256 `c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b`；`deployment/AgenticRAG/tests/test_run_budget.py -k pinned_production_meter -q`——**1 passed / 28 deselected**，2.01 秒。
- 宿主回归：`tests/test_agent.py tests/test_context.py tests/test_context_window.py tests/test_serialization.py tests/test_conversation_pairing.py -q --tb=short`——**127 passed**，4.73 秒。
- 最后聚焦复验：`test_codeplus_integration.py`、`test_run_budget.py` 过滤 `source_limit or hard_request_window or handshake or start_budget_stop or request_context_limit`——**29 passed / 109 deselected**，72.43 秒；覆盖两种 Agent 入口、软停止收尾、硬停止不收尾、计量故障、持久化终态、输出状态、pin 与延迟清理。
- Leader 补充的连续运行验收：将启动停止用例加强为同一 Agent 首次实际完成引用答案并持久化 completed/finished、释放 pin，第二次在 policy.start 建立 scope 前触发 BudgetStop；两个入口只输出当次 incomplete/time_budget，不复用旧 artifact/status，也不发送模型请求。独立复审补充了流式分支没有任何 StreamText 的明确断言。`test_run_budget.py -k start_budget_stop -q --tb=short`——**2 passed / 27 deselected**，6.46 秒；此后未再修改生产代码。

独立只读审查发现的宿主错误文案不一致已修复：预算退出显示实际持久化的终态；启动失败清除上轮结果。复审无阻塞发现，未把静态复审记为执行测试。`git diff --check` 通过。真实 GPU、Milvus、Docker、联网模型服务验收 **not executed**；本地受控 HTTP/索引替身不表示真实模型质量或端到端性能。

接入窗口 W1：R14本地提交175f8f54ed9e24736a232f6b989d145f7f217dca之后、R15获得写权之前。Leader已按冻结batch1-v2精确接入14路径并独立验收，core658/29skip、host715/3skip、44项定向、609文件处理及wheel/sdist两路安装通过；新独立清理和Leader复核也通过，共享172661条元数据严格相等，主临时根及清理helper根已删除。实际本地SHA以postcommit记录为准；实际过程、初次raw换行SHA断言失败及边界见[Leader记录](implementation-records/W1-batch1-leader-review.md)。剩余W1和W2未混入本批。
