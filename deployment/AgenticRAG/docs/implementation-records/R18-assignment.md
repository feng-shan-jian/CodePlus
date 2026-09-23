# R18 分派：逐 Chunk Rerank 与 Context 选择

基线 `1fd01183f9ce914e022d923debc13553b0f71d27`，保存项目 `D:/CodePlus`，分支 `codex/rag`。R17 已经独立验收并精确本地提交36路径；191项相关测试、真实GPU/Milvus与10→11升级通过，三路600条记录及200条RRF独立复核通过。

按用户已授权的逐项执行方式，本项使用新执行者作为唯一写入者，Leader在执行期间只读审阅。交接STOP_WRITE后由Leader独立验收、精确本地提交再继续R19。用户最新要求主线继续功能实现，清理由其另派会话处理，不追加清理或材料归档门槛。业务实现先复用现有业务，保持精简，只增加实际业务所需的判断，不写理论防御，不扩大范围。

## 范围与交付行为

对应现行任务规划/checklist的R18、D26/D44、T05/T06。先读 `docs/implementation-task-plan.md` 的R18、`implementation-checklist.md#r18`、`architecture-and-contracts.md` 的T05/T06和R17实际代码。现行总范围为Windows R00–R24，R25/R26已删除。

1. 在三路召回/RRF之后接入已冻结配置的模型Rerank。每个原始Chunk独立评分，稳定ID去重，按 `rerank_candidates` 截取输入候选；精排前不拼邻块。沿用canonical正文和持久化 `ChunkInput.index_title` 的模型输入约定，不用文件名替代章节标题。返回顺序按完整评分及稳定ID确定，原文和引用位置不漂移。
2. 用实际reranker tokenizer计入query、标题、模板、特殊token及完整正文；复用现有模板与token计数。按实际长度形成模型条数/padded-token受限批次，完整候选ID必须对齐。超长明确失败，不截断输入；任一批次失败整次失败，不使用未精排结果，不把中间候选变成已交付证据。原先确认的正文仍可引用。每批身份、实际输入量、评分和耗时写进同一次检索轨迹，便于复核。
3. 复用现有reader/run pin/模型handle完成回执，保护整个检索与重排区间；不可因请求报错/客户端关闭就提前宣称GPU执行已结束。查询和重排共享现有worker，不新增调度器、另一套生命周期或Agent。
4. Context以当前query的相关性排序为先，去重同位置/重叠正文，保留同文档不同证据及冲突材料。相关性接近时优先互补来源，不强凑文档数。使用明确、可复核的确定性选择规则；规则/参数及其试验性质写入配置或现有追踪，不声称已经完成R23阈值冻结。不同query的原始分数不直接比较。
5. 片段数、实际序列化正文的token上界、宿主完整窗口和多轮累计约束沿原权威路径生效。所选/裁剪/压缩/移出区间可追踪；评分候选、工具返回、实际送达资格和当前可见窗口分开。已返回但未送达、被裁剪或移出窗口的正文仍能重新取得，不能用全部历史source_candidates压掉未见正文。
6. 正常fixed配置允许启用Rerank，原query-only工具及两条现有Agent循环继续工作；false路径、DenseSearch和SourceSession(dense=)兼容。BM25+Rerank只调用重排模型，不执行查询Embedding；纯BM25/rerank=false继续零模型连接。R19的auto动态参数/权限/完整预算、R20报告和R21管理CLI不在本项。

## 已检查的复用接点

- `models/client.py::submit_rerank/rerank` 已有正式能力；`models/tokenization.py::FrozenTokenizer.rerank` 拼完整prefix/body/suffix并实际检查长度；`capabilities.validate_response` 校验请求/profile、完整唯一ID和稳定评分顺序。不要重写模板、推测token或用Embedding计数代替reranker计数。
- `ingestion/encoding.py` 已按真实计数处理条数/padded-token批界；`indexes/manifest.py::rows_for_document` 已定义 `ModelInput(chunk_id,index_title,canonical body)`。挑选最小复用方法，必要接口变化同步调用方/测试/文档，不为了批处理引入通用框架。
- `retrieval/search.py` 现有外层reader、RRF和诊断JSON；`storage/readers.py::query` 已跟踪实际handle、worker身份及wait_finished，可最小扩展为Rerank请求，避免复制整套读者实现。
- `SourceSession._candidate/_body_item/_commit_result` 已有canonical区间、最终render计量、共享窗口和generation；`DeliveryGateway.prepare/retain_prepared_window/settle`、`evidence_windows` 和delivery_receipts已有实际窗口/交付权威。优先复用这些表及R17 JSON trace，只有确实缺少业务落点时才考虑追加schema。
- `_unreturned` 是公开标明的tool-return覆盖/导航元数据，不是当前模型窗口。不要把它当作现有全局正文抑制bug，也不要仅为清理改变其对外意义；新Context去重尤其不能按全部历史候选来判当前可见。
- 回答meter是R12已接受的固定模板UTF-8字节上界，不是精确token估算；报告须区分宿主上界与reranker实际token。R12旧试验tokenizer临时路径已不存在，可在本项自有目录获取固定公开资产。来源/许可见 `R12-budget-design.md` 和现有vendor；DeepSeek-V4.1-Flash revision `dba1be0a40aa45a94ad051997016db3960a90277`，tokenizer SHA256 `c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b`。不要改用户provider配置或悄悄换模型。

## 验证与同条件对照

正式测试覆盖：三路+开/关重排、原始Chunk身份/标题/正文、跨批次重组/重复/缺失/同分、实际token超限、部分批次故障及旧证据、取消与reader保护；Context重叠/同文档互补/冲突材料/预算裁剪；两条正常Agent循环中的实际正文映射、裁剪/压缩/移出后再取得和引用资格。受控网络用例与真实模型/服务证据分别记录，不把mock称为真实回答质量。

用本项私有安装包进行针对性和受影响完整回归，保留原失败和后续复验。实际GPU/Milvus完成固定配置下的Rerank、超限/批次故障和无降级。若已有正式测试足以证明不变路径，不反复重跑无关测试；不为临时验证新增一次性脚本到仓库。

同条件medium：先声明固定Hybrid路线，在本项同一个真实609篇/4041Chunk发布版本上，对相同200题顺序比较rerank=false/true，除开关外固定query、模型、候选数、RRF和输入hash。复用现有query-only runner与独立official/native评分，TopK10及177检索分母不变，所有失败保留。增加实际选择/交付Context的片段、上界量、重复/原文区间覆盖和成本时延对照；将排序指标与最终正文指标分开。不开新评委/不以接口成功代替有效性，不做答案正确率或R23达标声明。结果允许回退，不调gold或正式题。

R17对照参考：Dense/BM25/Hybrid Hits@10 88.70/88.70/93.79%，MAP 0.270704/0.320490/0.314998；区间覆盖246/276/288（共484），完整覆盖38/55/55（共177）。Hybrid的MAP低于BM25，不能预设新重排必然改善。

## 资源、保护与交接

建议专属根 `C:/Users/18221/AppData/Local/Temp/codeplus-r18-executor-20260923`、同名Compose、端口19546/9107，先检查空闲。可常规复制R17 Leader的core/cuda环境，禁止硬链接或重装原环境；Compose复用 `tests/resources/r14-compose.yaml` 变量R14_PROJECT/R14_MILVUS_PORT/R14_HEALTH_PORT；共享模型缓存只读。自己拥有建库/测量数据和运行，其他阶段资源不删除、不改写。

实际schema11旧包为 `C:/Users/18221/AppData/Local/Temp/codeplus-r17-leader-20260923/dist/codeplus_agentic_rag-0.1.0-py3-none-any.whl`，SHA256 `20319d8040fa24b2eaeb2a9f858ac0d03b9f82d1a975c116d46b90d857e0684a`。若需新schema，必须真实旧包数据升级及故障回滚，历史11份SQL原字节保留；优先使用既有JSON追踪避免不必要迁移。

先拍实际基线，保护继承官方609篇/2556题/6084 gold、旧compose删除、未跟踪compose、用户双语README及共享环境。R17真实SHA台账更新和 `R17-leader-postcommit.json` 保持原字节，由Leader随下一正常提交携带。仅改R18所需核心/适配接点、评测、正式测试和文档；若确需宿主变化，先从现有 `codeplus/agent.py`、`run_policy.py` 和已接受R04契约复用，说明具体调用点，不新建宿主流程。

交付精确文件清单、简明R18记录、验证/原始证据索引、资源归属与未执行边界；原始文件留私有根并登记即可。全部写入/自有命令结束后明确STOP_WRITE。不stage/commit/push，不另开worktree，不处理清理分支，不新增并行写入者。Windows用pwsh7，无Bash heredoc，复杂脚本写文件执行。
