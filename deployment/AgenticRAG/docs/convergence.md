# AgenticRAG 本轮收敛记录

清理任务：`01a0c9c1-0a2a-7eb3-9e79-a007a6a9d1e4`。唯一集成写入者为功能 Leader；清理仅在独立 `c-woker/agentic-rag-convergence` 工作树执行，不推送。

本轮状态与功能 R00–R26 分开。修复、自测、独立审查、接入提交分别记录，不能以某一步替代后续步骤。

| 问题 | 已复核事实 / 处理边界 | 验证与接入 |
| --- | --- | --- |
| BS-01 | 来源额度依赖英文异常文案，片段上限落入非法 partial/budget；source_budget 还混有计量故障。第一批改为明确原因，新增 context_limit，共用终态原因定义；不放宽 partial/budget | 第一批已在R14提交上接入，Leader实际安装独立回归core658/host715、新独立清理及Leader复核通过；实际本地SHA见postcommit记录 |
| BS-02 | 普通构建内部候选绑定完整 owner 和输入/配置身份，中间步骤复用并核对内容；最终完整归档/索引验证保持。混合旧成员准备 5→2、无变化 2→1，完整发布向量复制保留 | 归档I/O收敛已接入并通过Leader独立验收；内部重复seal另有4路径精简候选待下一批，BS02尚未结项 |
| BS-03 | N 项批处理在单次操作内完整认证一次输入集合，逐项窄查当前行、每次实读归档；公开读取及恢复各自重新认证 | 已接入并通过Leader独立CPU/真实恢复验收；具体提交与清理边界见末尾记录 |
| BS-04 | 来源调用准入/异常归并、归档单次读取哈希及返回；搜索在单次调用内按库/版/文档/文档版复用归档来源，各命中仍校验 section/chunk/span/text/hash，签发及提交仍检查当前 run/pin | 第一批归档/审计已本地提交；第二批搜索复用已接入并通过Leader实际安装和真实服务验收 |
| BS-05 | 完成请求载荷被 client 强持有；累计 session 上限属于现行防重契约，不能仅改活跃计数 | Leader 已批准连接绑定、活跃句柄及安全轮换设计；独立 CPU 实施后在 W2（R15 提交后、R16 前）验收 |
| BS-06 | 首层推理 hook 仅诊断消费但已列为观察契约；Milvus load/release 需按 R14 最终路径重验 | Leader 已批准显式可选诊断开关，保留配置/快照指纹；真实 GPU 验收待 W2，不宣称性能收益 |
| BS-07 | 生产适配器负责类型、catalog、storage 绑定与正式验证算法选择；publication 保留归档、成员和事务门禁，测试通过实际构造器注入 SDK 传输 | 已接入并通过Leader实际安装、SDK/Milvus与升级验收；仍保留具体适配器类型绑定 |
| BS-08 | v2–v7 共用有序事务执行；每次打开仍逐项核对旧迁移哈希，v1 和 v8 FK 重建保留显式边界，所有 SQL 字节不变 | 已接入；Leader迁移回滚、真实v7到v8升级通过，8份SQL实际字节保持 |
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

## W1 后续候选进展

第二批基线为 Leader 第一批实际提交 `8c764644c57e59c9aa9ae2337f61efc3447d88ee`，父提交为 R14 `175f8f54ed9e24736a232f6b989d145f7f217dca`。独立树按 76 个自有状态的 raw 备份安全快进；13 个已接入文件的 Git blob 与冻结第一补丁一致，62 个不重叠自有状态逐字节保持，搜索后续实现及 Leader 验收/清理记录均已合并，index 为空。本批固定 25 个 W1 文件；W2 实现与材料整理另行交付。

- BS08：`test_input_migration.py test_processing_checkpoints.py test_storage.py`——**58 passed，8.34 秒**。事务中实际执行 v4 SQL 和迁移回执后注入 BaseException，确认已完成的 v2/v3 保持、v4 全部回滚，随后重开升级 v8 并核对历史数据、FK 和 integrity；逐项损坏 v2–v7 已应用哈希时均拒绝重开。
- BS07：`test_publication.py test_mutations.py test_recovery.py test_recovery_ownership.py`——**79 passed，120.25 秒**。首次收集因独立环境未安装 PyMilvus 失败；异步安装尚未完成时重试也同样失败。等待 copy 安装 PyMilvus 3.0.2 完成后原断言通过，没有改成跳过。测试执行真实适配器构造和正式全量验证算法，但 SDK/服务传输为受控替身，不计真实 Milvus 验收。
- BS03：`test_processing_checkpoints.py test_mutations.py test_recovery.py`——**77 passed，106.76 秒**；`test_input_read_scope.py`——**10 passed，5.20 秒**。覆盖新处理和已有检查点线性读取、当前行变更、同一操作再次读取真实归档、下一次公开操作完整认证，以及 inspect 后损坏在 continue 打开 runtime 前被拒绝。独立静态复审通过，未将只读 scope 当作 owner 授权凭证。
- BS02：`test_mutations.py test_recovery.py`——**58 passed，85.26 秒**；`test_build_preparation.py` 初始契约集——**11 passed，16.36 秒**。混合与无变化扫描次数、跨库/版本/owner、退出失效、候选行/配置/向量突变、最终归档重读和公开边界完整重验均有正式断言；其后补充测试及独立复审结果见下条。
- BS02/03 补充：编码首次/已有完整检查点的线性读取，以及基础 artifact/expected rows 内容突变——**4 passed / 21 deselected，6.89 秒**。BS02 独立静态复审通过，无阻塞问题。
- BS04：`test_source_read_reuse.py test_sources.py test_source_identity.py test_evidence_citations.py`——**87 passed，80.49 秒**。三章节真实归档来源每次 search 只执行一次 read_ref、四次归档打开；下一次 search 和公开 issue_source 重新读取。逐命中拒绝错误 section/chunk/text/库/版本，损坏后下一次 search/open/issue 失败，签发及提交前撤销 run 不能写入候选。后续补充 document 与既有 section 错配测试。
- BS04 两项身份补充：**2 passed / 12 deselected，1.37 秒**；独立静态复审通过，无阻塞问题。
- 五项合并回归：`test_build_preparation.py test_input_read_scope.py test_source_read_reuse.py test_input_migration.py test_storage.py test_processing_checkpoints.py test_mutations.py test_publication.py test_recovery.py test_recovery_ownership.py test_sources.py test_source_identity.py test_evidence_citations.py`——**251 passed，203.64 秒**，无跳过。三份真实旧发行包构建/独立安装/删源后由当前 schema8 重开：`test_worker_history.py`——**3 passed，14.78 秒**。
- 最新宿主联动：设置已核验的固定 `R12_ANSWER_TOKENIZER`，执行 `test_codeplus_integration.py test_run_budget.py`——**138 passed，243.81 秒**，无跳过。

BS04 的复用范围仅为 SourceSession 的一次搜索，首次读取之后同次搜索的其他命中使用该版本的已验证内容；不把这一点解释为持续监视磁盘变化。下一次调用、公开打开、证据发送/确认和引用验证仍按各自契约实读；上述次数不包含 DenseSearch 的独立读取。

以上结果来自本任务独立 editable 环境，当前工作树还含等待 W2 的 worker CPU 修改；这些自测不冒称仅安装 W1 窄补丁的发行验收，后者由 Leader 在第二批冻结后独立执行。CPU 替身、静态复审和读取次数变化分别报告，不替代发行安装、真实服务或性能验收。

交付前的跨工作树 SQL raw SHA 全等比较初轮失败：bf43 的八份 SQL 为 CRLF，集成树为 LF。逐项复核仅换行不同，规范 Git blob 均等于该基线，按生产 read_text 规则读取的迁移内容一致；没有重写 SQL 或把 raw 全等记为通过。SQL 文件未进入本批补丁。

临时资源清理尚未完成。此前 10 个已完成自测目录的批量和缩窄单路径删除均被执行工具自动审批以 blocked by policy 拒绝，没有更具体原因；未执行、未重试、未绕过。最新只读资源附件 agentic-rag-convergence-resource-status-v2.json 共 6,769 bytes，SHA256 4e56e47fffd581a64de17b60410883d4d20abe151843c46cb84638518982c868，关联保留原拒绝证据，列出 22 个已完成测试目录、匹配 Python/uv 进程为零。该附件不冒充递归链接审计或清理完成；独立环境、cache、tokenizer 和同步备份仍供后续使用，临时清单/同步脚本已逐个删除。

## W1 第二批 Leader 实际验收

Leader已将上述25路径及仅README/dev/lock的3路径依赖修订精确接入，最终28路径v2 SHA256 da86985356438b56b96f35e38209e11a79a0e7c2f8d720cff3d11965aa53df28。core705/29skip、host715/3skip、609文件处理、fresh默认uv sync两项原发布回归、wheel/sdist独立core-only安装均通过；真实GPU/Milvus普通变更、13场景恢复、真实v7→最终v2升级和15份数据库/归档、actual dense及停服引用读回通过。原319源码/测试字节在dev修订时保持，322文件冻结验证；实际版本、完整命令、失败和未执行边界见[Leader记录](implementation-records/W1-batch2-leader-review.md)。工程验收、全新独立主资源清理和Leader复核完成，本地提交真实SHA见postcommit记录；最后12文件清理helper目录被自动审批拒删，单独等待用户指示，不宣称全部临时资源已删除。

BS02归档I/O收敛已验，但内部重复全对象seal待后续4路径窄补丁精简，保留真实边界上的owner/CAS和公开/最终完整校验。该追加代码另审另验，不与材料压缩混入；BS02和整个W1尚未结项。BS03、BS04搜索复用、BS07、BS08已接入并通过上述Leader验收。W2、材料和R15未混入。

独立收敛任务最新v3资源报告为8060 bytes，SHA256 5965d8634f0550901debd878391132f5b96c08b1316da1b8d8ee3264c14f9cce：25个完成测试目录，另有2个新单文件被自动审批拒删；此前10目录拒绝及仍供后续工作的环境/缓存/备份保持。不覆盖v1/v2历史报告或宣称递归审计/清理完成。它们与Leader此前R14四文件目录、batch1单文件的待处理拒绝均不在本批独立清理范围内。

本批独立清理实际删除约4.93GB自有根与3容器/3卷/1网络，端口关闭；Leader重核33附件及全部保护哈希通过。shared after与最后predelete的172661节点零差异；最初before仍有5724个uv缓存普通目录size差异，1052个自有目录attrs差异也保留原FAIL与来源未知说明。新helper根12文件63232B的删除被自动审批以blocked by policy拒绝，未执行，未重试；三处Leader相关拒删范围均待各自用户指示。bf43 v4（9042B/SHA03a75a0d423615e822a3be17d0eaedc5ce637f6255988f6d144be3c3311da207）及之后第5个辅助单文件拒绝也不在本批清理范围。代码提交不表示这些残留已清除，详见本批Leader记录及独立清理报告。
