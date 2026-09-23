# R20 分派：报告、中文问答与继续研究

基线 41802ffe9061856dc8d2ea1e4ef0fdbdad1d143a，唯一保存目录 D:/CodePlus，分支 codex/rag。R19 已由 Leader 独立审查、实际验收和本地提交后才启动本任务。全新执行者唯一写入；Leader 执行期只读，明确 STOP_WRITE 后交权。Windows R00–R24 功能主线，不 push、不发布、不建 worktree，不接管另派清理。先复用已有能力，保持业务实现精简，只补实际需要的判断与契约。

## 范围
先读 implementation-task-plan.md R20、implementation-checklist.md R20、plan.md D07/D08/D13/D45/D46/D55，architecture-and-contracts.md T01/T07 和 host-integration-contract.md。

1. 用同一个真实 CodePlus Agent 完成 report 任务；R19 task_kind=report 与 fixed/auto 正交且已冻结。按用户任务生成结构化 Markdown，覆盖结论、比较、分歧、推断标识、证据与局限；不增加报告 Agent、翻译 LLM、规划器、章节工作流或后台续研。
2. 中文问题保留实体、时间、否定、比较等约束，由既有聊天模型生成/改写英文 query，中文回答，直接引文保留英文原文与可定位引用。核心执行实际 query，全部模型调用与 token/时间使用现有账本。
3. 报告只有通过现有整份引用校验后才能保存。用户/可信入口选择目标路径，待校验草稿不能写成正式报告；复用宿主权限、路径、读取后覆盖及错误返回规则。成功必须有实际文件字节及路径/摘要回读；拒绝、无交互 ask、写失败、取消不能声称已保存。后续研究建立新产物，不能静默覆盖旧报告。
4. 用户明确请求继续才以 parent_run_id 新建本轮；使用新的 QA/report 预算和开始时最新已发布可查询版本。承接原目标、用户约束、已覆盖/待查问题、来源线索及停止原因，历史发现明确是待核线索；不保存隐藏推理。无法确定目标/记录缺失应给可操作缺口，换库按新任务处理，不能暗中关联不同库。
5. 即使同版也重新通过本轮 search/open 得到正文和送达凭证。旧 evidence ID 不继承；新版更新/删除旧来源时，不读历史档案补回当前检索范围。支持修改/待核的历史发现标识，保持旧报告和历史引用可回看。
6. 逐轮与整段研究成本可查，保留各轮状态/版本/预算/停止原因；未知用量保持未知而非0。预算停止也保存可用进度以便用户显式续研。没有有效报告的失败状态不伪装完成。

## 已检查的复用点
- Catalog.start_current_run 已接受 parent_run_id，原子绑定当前发布版，runs 已有父运行关系；现有 host_runs.frozen/artifact/detail JSON 可承接适当进度/保存信息，优先复用，不为每个逻辑对象建新表。
- KnowledgeScope.assess_output 已先校验所有引文和标记，再保存 citations 并 render_markdown，返回不可变 ValidatedArtifact(run_id, markdown, sha256, citation_ids)。目前它没有文件路径/保存状态，需最小贯通两条 Agent 循环、TUI、-p 的真实结果；保持旧 QA 调用兼容。
- HostRunContext 有 work_dir/permission_checker，Agent._execution_scope 暂存原宿主 registry/file_history 后切到受限知识工具。不要把任意 WriteFile 暴露给模型绕过校验。
- 实际 codeplus/tools/write_file.py 负责落盘；只直接调用 execute 不会执行宿主权限，且 FileStateCache 未传入时不会限制现有文件覆盖。权限检查与 ask/deny/future/owner 在 Agent._execute_tool；沿其实际路径复用，不能用 Path.write_text 绕过。必要的最小策略契约扩展同步所有调用方和正式测试。
- CitationRegistry.render_markdown 已产出普通 Markdown 脚注，含文件/版本/原文区间/行号/摘录；open_citation 是历史读取，不赋予当前运行证据资格。不要发明尚不存在的可点击协议。
- TUI _send_knowledge 已用现有 Agent 类的受限运行和独立 ConversationManager，恢复普通会话；目前只记 last_knowledge_outcome，无 artifact 时外部没有 run_id，-p result 也未暴露 run_id。为预算停止后的显式续研补最小运行标识/进度接口，不能依赖被丢弃的对话或重复注入旧工具正文为证据。
- 两条循环均在 codeplus/agent.py；交互 _execute_tool 与无交互 _execute_tool_noninteractive 各有实际权限路径，再调用 _execute_admitted。报告保存须在 scope.finish 关闭 admission 之前完成，文件保存结果与运行最终状态分别如实记录。当前 finish 会覆盖 host_runs.detail，进度记录须明确并入这次持久化，避免事先写入后被覆盖。
- R19 已给 /knowledge ask 与 -p 模式入口。R20 接通报告与明确继续所必需入口，管理命令全量完善/退出码统一留 R21，不另造产品 CLI。

## 验证
正式测试覆盖同 Agent 报告、引用校验失败不落盘、正常保存、权限 allow/deny/ask、既有文件覆盖规则、实际 -p 无交互、写失败、取消/预算截止与真实保存状态。中文实际工具 query 与英文原文引文需可核对，语义规模评测门槛留 R23。
用父子运行验证同版重新取证、新版更新/删除、跨库、缺失进度、预算停止后明确继续、旧证据拒绝、每轮独立预算及累积未知成本、旧报告/历史引用保全；完成状态和实际文件一致。
用私有安装的最终 host+RAG 包运行两条现有 Agent 循环及必要普通宿主回归；改宿主需重建安装 host，不能只装 RAG。正式环境传入固定 R12_ANSWER_TOKENIZER 并核SHA。小型真实 GPU/Milvus + 已批准回答提供方跑正常报告/中文/继续链路，受控权限/故障另行标明。不重复无必要的200题对照，不以合成响应冒充真实 Agent。
优先现有11份 SQL/JSON；若确需新schema，实际旧11→新升级与故障回滚，旧SQL字节不可改。

## 允许修改与当前证据
允许修改独立模块 adapters/codeplus、已有 storage/catalog/runs 与实际必要的 domain/config 接点；优先现有持久JSON，不另起状态系统。允许最小宿主接点 codeplus/run_policy.py、agent.py、app.py、__main__.py、commands/handlers/knowledge.py；若复用现有工具确需更小接点扩展，先向Leader说明具体调用链后判断，不扩展无关宿主行为。允许相应正式测试及独立模块README/契约/使用文档。根双语README、评测冻结语料、旧11份SQL、其他任务已验资源及清理范围保护。
R19 Leader已独立318相关+139宿主通过/1Windows条件skip，两路安装、真实GPU/Milvus与实际auto/report、-p fixed/qa通过；原提供方402在用户充值后解除。继续使用原已批准D:/CodePlus/.codeplus/config.yaml，凭据不能进源码/参数日志。R19最后两条真实run分别6400/8269 tokens；这只是入口小场景，不是R23质量门槛。
用户刚确认“继续，已充值余额”，可以执行本项必要真实回答验收，无需重复请求额度/调用许可。继续保护模型缓存、数据与接口，不为理论防御扩大范围。

## 资源与交付
建议私有根 C:/Users/18221/AppData/Local/Temp/codeplus-r20-executor-20260923，独占端口19550/9111，启动前查空闲。普通复制已完成R19 Leader环境，不硬链接，不改历史环境，共享模型缓存只读。
先冻结真实基线，保护6537继承数据/旧compose删除/root双语README、R19 Leader后记/checklist真实SHA、本分派卡。精确路径交付：功能/正式测试/文档；原始结果留私有根并索引，原失败与必要复验分别记录，登记临时资源归属。清理交用户另派会话，不新增清理/材料复制门槛。结束全部写入和命令后明确STOP_WRITE，不stage/commit/push。
