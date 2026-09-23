# R19 分派：完整检索模式、预算与失败策略

基线 `cef8821a78447e5dde418cb1d68c6ac73c81fdc7`，保存项目 D:/CodePlus，分支 codex/rag。R18 已由 Leader 独立验收并精确本地提交：原始 Chunk 重排、Context 选择与逐题对照完成；8000 试验上界在完整请求处拒绝部分正文，属于本项必须承接的具体预算行为。

按用户授权，使用全新执行者作为唯一写入者。Leader 在执行期间只读；STOP_WRITE 后独立验收、精确本地提交，再继续 R20。当前目标是 Windows R00–R24；不 push、不发布、不另建 worktree、不迁入根包。用户已另派清理会话，功能主线不处理清理或增设清理门槛。业务实现先找可复用路径，保持精简，仅增加实际业务必要的判断，不做理论防御或扩大范围。

## 范围与行为

先读现行 implementation-task-plan.md 的 R19、implementation-checklist.md 的 R19、plan.md 的 D14/D21–D25/D28/D53/D54、architecture-and-contracts.md T06/T07、host-integration-contract.md，以及现有 R12/R17/R18 代码。

1. 知识库功能默认 auto；本次任务显式模式优先于功能配置，再到功能默认值。开始时冻结最终模式及来源，显式传入与默认值相同的模式也应能还原来源。配置改变不影响已运行任务，不改宿主权限模式，也不自动对普通任务启用知识库。既有调用方式保留，新增参数优先用可选 keyword。
2. fixed 仍是 query-only 搜索工具，可改写 query、多轮搜索和 open，但每次使用绑定的路线及重排要求。auto 仅增加 Dense/BM25/Hybrid 和 Rerank 开关两个选择维度；不向模型开放候选数、融合参数、模型、库、版本和预算。核心与适配层共同遵守运行权威；调用选择局部解析，不改全局 config 或检索服务的共享 route。轨迹记录请求/实际选择及阶段结果。
3. 成功为空、能力不可用和执行失败分开。错误提供阶段、类别、调用关联与当前模式允许的后续动作。下一次重试/改选是 Agent 的新显式工具调用；fixed 不跳过失败路线/重排，auto 可在支持选项内改选；不实现程序自动降级。保留失败与后续调用的可核查关联，所有尝试累计计数。
4. QA/report 复用现有分别预算，与模式正交。开始时解析的预算同时用于 reserve 检查、lease、SourceSession、deadline 和账本；旧调用默认 qa。改写、重试、加载、排队、compact、引用修正的时间/模型请求均累计，不能重新计时或自动增额。
5. 复用完整 HTTP 计量、缓存用量归一、unknown 处理、原子预留/结算、收尾与一次引用修正。探索上限保留有界收尾，最终硬上限/取消不再发新请求；返回已支持结果、缺口和 stop_reason。不把资料不足说成资料必然不存在；不询问追加预算。
6. 承接 R18 完整请求预算实例，沿宿主现有序列化/准备/裁剪和来源 sidecar 处理可避免的超限。保留最后真实 raw-body 门。不可通过任意固定余量、单组加预算、把核心 count(text) 偷换为宿主 JSON 协议、或在 permit 回调中悄悄重写已准备 bytes 来掩盖问题。先确认最小业务接点，必要接口变更同步调用方、契约与正式测试。
7. 核对实际非交互入口的权限行为：codeplus -p 当前构造的是 Agent（__main__.py 的 _run_prompt），其 PermissionRequest 分支无条件 ALLOW。知识库运行遇到 host ask 规则时，缺少交互输入不能自动成为同意。复用已有 PermissionResponse/owner/权限路径做最小功能内修正；不要把 CompletionAgent 的拒绝用例误当作真实 -p 入口已经受保护。

本项不实现 R20 报告保存/继续研究、R21 完整管理 CLI 或 R23 参数/质量门槛冻结；report 在本项仅接通正确预算选择。后续入口的退出码完善留在 R21，当前若为具体权限路径必需则同步最小调用方。

## 已检查的复用接点

- config.py 已有 RetrievalConfig.mode=auto、RunOverride、resolve_run、RunConfiguration(task_kind,knowledge,retrieval,budget)。catalog.start_current_run 支持 override/parent_run_id，并在实际发布版内绑定编码配置。不要新增另一套配置解析。
- KnowledgePolicy.start 的 fixed guard、硬编码 qa 预算/启动参数是实际改动点。DevelopmentConfig 与 load_policy/KnowledgePolicy 原有调用保留，可选任务/模式参数贯通。已有 assemble_configuration.origins；DevelopmentConfig 直接解析则需在 with_published_encoding 的 JSON 重建前保留实际输入来源，不能靠最终模式值猜测。来源可优先写入 host_runs.frozen 等现有 JSON；不要随意给 RunConfiguration 加默认序列化字段导致旧运行 fingerprint 校验变化。
- SourceTool 当前 SearchArguments(query) 且 is_concurrency_safe=False；注册时按固定模式冻结 schema。将 auto 选择传到 SourceSession/RetrievalSearch 的当前调用，所有 trace、payload、limits 跟同一个有效选择。原 DenseSearch._limits(limit) 与 SourceSession(dense=) 兼容接口必须保持。
- ErrorInfo 已有 code/stage/message/retryable/request_id/call_id，目前工具只返回 code/stage。补实际可用信息和模式允许动作即可，不制造自动重试框架或宣称重试必然成功。
- ledger.py::ModelControl 已有实际完整 request bytes、预算预留/结算、缓存/未知用量与最终 gate；RunTaskOwner 和 reader/handle 回执负责真实取消完成。KnowledgePolicy 的 initial monotonic clock 开始于 meter/catalog/connect 前，保留这条累计时间线。
- KnowledgeScope.prepare_turn/_trim_finish 已使用 build_chat_completion_messages 构造完整请求并处理配对消息；SourceSpan.crop 和现有宿主裁剪/映射路径可复用。ModelControl.before_send 返回 permit，不具有修改 request bytes 的现有契约。
- R18 对照直接执行 SourceSession + 固定最终请求准备，没有回答模型：第一关闭组 200 条排序成功、70 条完整窗口失败，smoke 有 8019 > 8000；开启重排的正式组 200 条排序成功、68 条 Context 失败，失败请求上界实际为 8018–8243。原记录不得重写。要区分 R18 的 selected/prepared、当前宿主窗口和已确认 evidence。evidence_windows 可在发送前 retain，not_sent/unknown 不自动回滚，也不授予引用资格。

## 验证

正式测试环境显式设置 R12_ANSWER_TOKENIZER 指向已核对的私有固定资产；R18 Leader 因漏传变量曾跳过一项并已单独补测，不能把可执行项当作平台跳过。

复用现有 test_run_budget、test_codeplus_integration、test_request_delivery、R17/R18 正式用例和配置/历史指纹测试，补实际缺口：

- 默认 auto、配置 fixed、显式覆盖（包括同值）、非法值、运行中配置修改、来源读回；普通功能/权限参数不被改写。两条现有 Agent 循环及真实 -p 入口分别验证，不混称。
- fixed 重复搜索/改写/open 不漂移；auto 同一个固定版本里选择三路及开关。schema 拒绝无权数值/模型/库参数，核心 direct API 也不绕过固定权威。选择与 trace 一致。
- 模拟或明确分层故障后，新显式 retry/改选受当前模式约束；合法 empty 与失败分开，失败中间结果没有证据资格；每次调用/时间/用量可查。
- QA/report × fixed/auto 预算正交；缓存完整/部分/未知、失败预留、finish reserve、硬停、单次 correction、真实 cancellation/reader 完成不提前释放。先前已通过的无关路径不反复扩大测试。
- R18 实际超限形状与引号/换行/Unicode正文，在两条实际宿主循环中保留完整最终 gate；可裁剪请求能发送，其实际 source spans 保持 canonical，未送达尾部可再取得，真正装不下时仍明确停止。不得伪造 confirmed。
- 用私有安装的小型实际 GPU/Milvus 场景证明 auto 错误后的显式路线/开关选择与 fixed 原配置重试。使用既有已批准 provider/固定 tokenizer 做必要的实际 Agent 正常模式路径，日志不得暴露凭据或修改用户默认配置。真实调用和受控 HTTP/故障证据分开。
- 数值质量、200 题 fixed/auto 策略效果和 P95 冻结由 R23 负责，本项不重复无必要的大对照。

若改 codeplus 宿主文件，必须重建并安装本地 host wheel 与独立 RAG wheel，核对实际加载的已改 host 模块。当前复制环境的 host 是早先 R12 wheel，只装 RAG 不足以验证入口改动。沿用已验 Windows 依赖，不做顺带升级；wheel/sdist 的必要独立安装与普通宿主回归按真实影响执行。

## 资源与交接

建议私有根 C:/Users/18221/AppData/Local/Temp/codeplus-r19-executor-20260923，独占 Compose 19548/9109，启动前查空闲。常规复制已结束的 R18 Leader core/cuda 环境，不硬链接、不重装旧阶段环境，共享模型缓存只读。固定回答 tokenizer 可从 R18 自有资产复制并核对 SHA c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b。若新增 schema，必须实际旧 schema11 包数据升级与故障回滚；优先复用现有持久 JSON，十一份旧 SQL 原字节不变。

先拍基线，保护继承官方语料/题库/gold、旧 compose 删除/未跟踪 compose、双语 root README、共享环境、R18-leader-postcommit.json 和 checklist 的真实提交更新。只有本项功能所需核心/适配接点、实际必要的宿主入口、正式测试和文档可改；不接管清理分支。Windows 默认 pwsh7，不用 Bash heredoc，复杂脚本写文件执行。

最终交付精确路径清单、简明 R19 实施记录、验证及原失败/复验索引、资源归属与未执行边界。证据留私有根并索引即可，不增加复制/清理验收。全部写入与命令结束后明确 STOP_WRITE。不要 stage/commit/push，也不要创建额外写入者。
