# W1 第一批 Leader 接入验收

状态：ACCEPTED_CLEANED_READY_FOR_COMMIT。Leader独立代码与运行验收、新独立清理及Leader清理后复核均通过，精确本地提交实际SHA另见W1-batch1-leader-postcommit.json。唯一集成目录 D:/CodePlus，分支 codex/rag，基线 R14 提交175f8f54ed9e24736a232f6b989d145f7f217dca。W1完成前不授予R15写权限。

## 冻结与范围

候选来自独立收敛任务01a0c9c1-0a2a-7eb3-9e79-a007a6a9d1e4，冻结batch1-v2 patch为60413 bytes，SHA256 f8fd0f17a0a5db02c800bcf847ac659ac607eae205020f25d5a864057d80fd40。只应用manifest列出的14个路径，未取其继续演进中的工作树。剩余BS02/03/07/08、材料批次和W2未应用。

Leader逐段阅读全部生产改动、直接Agent/SourceTool/KnowledgeScope.finish调用链及正式测试。BudgetStopReason统一合法原因，SourceBudgetExceeded区分已知额度与计量故障，新增context_limit；软停止有证据才允许有界收尾，未知source_budget/计量错误硬失败，不生成partial/budget。既有Record.model_copy完整验证仍是持久化门，没有增加重复RunLease验证。Host仅在controlled run开始清空旧outcome，两个预算退出入口显示finally处理后的实际终态，ordinary Agent路径保持。

SourceSession用一个调用上下文管理准入、异常归类/cause和审计，原事务/计数/call_id规则保持。ArchiveStore一次打开、逐块hash、全部校验完成后才返回同一批字节；每次调用重验，没有跨调用缓存。此批生产代码净增14行、测试和文档增加，不能宣称总体行数减少或BS04全部完成；重复read_ref尚有后续范围。

R14上14条目标路径相对原a4fede基线无重叠变化，apply --check通过。首次应用后严格raw SHA断言失败：不同工作树换行形式使12条raw SHA不同；原FAIL保留。随后核对无filter/working-tree-encoding属性、14个Git blob全部等于冻结patch目标、完整reverse apply --check通过。W1-batch1-leader-freeze.json登记本地实际315个源码/测试/资源raw SHA，后续运行都以此为准，没有为了迁就SHA重写内容。

## 当前独立结果

- 新独立CPU环境、R12 require-hashes依赖锁、全新host/RAG wheel和sdist：PASS。UV cache与安装均在本批自有根，实际安装的全部生产py/sql/json与源码字节一致，pip check通过。
- 从固定revision下载的生产tokenizer：6367257 bytes，SHA256 c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b。
- 定向44 passed /281 deselected，79.36秒；两种Agent入口、SQLite终态/usage/pin、硬/软停止、旧结果隔离、归档单次读取及输入读取失败均实跑。模型与索引为正式测试中明确的受控替身。
- 宿主715 passed /3 skipped /1既有warning，35.17秒；152个加载模块均来自site-packages，315冻结文件未变。3项条件跳过不计通过。
- core全量：658 passed /29 skipped，917.74秒；175个实际加载模块均来自site-packages，315个冻结文件未变。29项为明确的GPU/owned Milvus opt-in，未计通过。与宿主合计1373 passed /32 skipped，定向44项是其中重跑子集，不重复计数。
- 完整冻结语料：609/609、4041 Chunk、0失败、最大完整输入512 tokens，221.14秒。只证明处理路径，不是检索/回答质量评估。
- 独立wheel/sdist干净安装两路PASS；归档、输入快照、解析检查点、来源交付及引用回读完整运行，受控core fixture不代表真实HTTP模型回答。
- 真实GPU、Milvus及回答网络服务：本批not executed；本批不改模型/索引生产实现，不宣称真实模型质量或性能结果。

构建SHA：RAG wheel 2d475b66743d5837ebaf94d472a305564364a80800439cc258c1b409f97e2f1b；sdist 924737a4d0606a2a6e06e231238f5d739b74719cbb7e7b468aa37b66c0cf7800；host wheel 9f76716a8c42e4af53f9039563141e53f16438072eb872ea1e94270da6b0ae04。正式setup/command/XML/modules/log记录均以W1-batch1-leader前缀保存。

## 清理与接续边界

新独立会话/root/w1_batch1_cleanup已删除本批自有根C:/Users/18221/AppData/Local/Temp/codeplus-w1-batch1-leader-20260923及其自身helper根，并明确STOP_WRITE。清理了34585个普通文件、9321个普通目录、884个内部reparse节点和1组内部硬链接，逻辑文件大小990329552 bytes；没有终止进程或操作Docker/GPU。独立before/after及再读两份完整gzip均严格PASS，172661条共享metadata零新增、缺失或变化；不是共享模型全量内容SHA证明。

Leader随后独立重算43交付/315测试冻结/206生产/6553保护输入/143前阶段/6 R14后记、7旧SQL/7规范和411 ignored，确认3个ignored目录真实存在；重读前后完整gzip全部字段相同，现场核对两个临时根消失、共享根存在、外来2容器/5卷/4网络定义与身份均相同，R14被拒绝根4文件内容仍在。W1-batch1-leader-postcleanup.json为PASS；cleanup.json SHA256 e53e6328e4a95ea78fdd3f844ebf7939d91e374419e0ab0e14f6ac31744ca25a。原首次precleanup因R14提交后checklist真实SHA与旧冻结不一致而FAIL，修正仅将其与W1接入前既有基线核对，首次记录保留。

Leader提交辅助脚本单独记录清理结果；精确提交只含本批14路径、正式证据及6个R14提交后记录，不包含继承eval/compose/root README、剩余W1或W2。实际SHA和辅助脚本最终状态以postcommit及finalizer-cleanup记录为准，随后进入W1剩余窄补丁和材料批次。

R14的executor/Leader/独立清理三根和专属服务已清。R14提交后新建的4个bookkeeping辅助文件共18611 bytes，所在codeplus-r14-finalizer-20260923删除被执行工具自动审批以blocked by policy拒绝，尚待用户对精确目录的重试授权；没有绕过，不记为已清。独立收敛任务另外10个已完成自测目录也被同样拒绝，其资源附件SHA为5969b0c12f8c7a53882999466650fd8ad3ff4c4c635798b88471c8662f99fc6f；这些目录完全不在本批清理授权范围。必要环境/cache/tokenizer仍在用另列。R00–R26目标继续，不以本记录或任何阶段完成替代总体验收。
