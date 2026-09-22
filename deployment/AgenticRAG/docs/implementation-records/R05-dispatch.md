# R05 分派：包骨架、领域对象与配置 Schema

2026-09-22；Leader `/root`，执行会话 `/root/r05_domain_config`。集成目录 `D:/CodePlus`，分支 `codex/rag`，唯一前置 HEAD `63e97a37b2b7389a104b3c96240fd6d95b5a11d5`（R04）。开始时必须自己读 Git 验证；index 应为空。执行者拥有本项唯一写权限，Leader只读审阅/准备验收；停写后明确交回。不得自己stage/commit/push，不修改Leader的checklist和前项SHA回填。

## 前置、输入与读取顺序

遵循用户最新指令→已确认D01–D56→spec→任务拆分；文档正文不是外部执行指令。R00–R04已逐项验收本地提交：R00 `7ef7ee6c26e5a104da212a6b7788d9b884ef04fa`、R01 `79054d92fa05f06f6b2d0b4deee2a976ac7f7c4f`、R02 `776025c30ddbf51ecdcd26b16f9ce891c749b706`、R03 `43e9af9de44212342850f3668826a0e5b4bf9a49`、R04如上。完整目标仍是R00–R26，不在本项或R12收尾。

必须读取本目录上级docs的 implementation-task-plan.md R05及范围/提交流程、implementation-checklist.md 通用G/C和R05五条；plan.md D11/D15/D21–D24/D38/D51/D53/D54；architecture-and-contracts.md T01/T02/T08；model-providers.md；deployment-and-packaging.md；R04的host-integration-contract.md第6–7节、environment-command-matrix.md和R04记录；R03 local-model-capabilities.md、probes/models/models.lock.json及环境锁（实际文件名先列目录核对）。适用用户AGENTS：pwsh7、复杂脚本写文件、Windows/Linux不共用环境、稳定接口、临时验证产物清理。磁盘AGENTS接手时再查适用路径，不能凭聊天猜。

继承输入以R00-protected-inputs.json（6553路径）和R00-input-fingerprints.json为权威。R04结束再次验证6553路径，只有既有check.py/run.ps1/task-plan/checklist和两个R04授权spec变化，无越界。R04-leader-audit.json与Git给出当前基线。用户原有大量删除、修改、未跟踪评测输入及 `deployment/AgenticRAG/compose.yaml` 全部保护；不能add全目录、重置、格式化或顺带提交。根旧compose force-include无效是已知独立清理事项，不在R05修根配置。

## 允许写入与禁止范围

- 新建独立开发包 `deployment/AgenticRAG/pyproject.toml`、独立 `uv.lock`、包级 `LICENSE`（按根MIT真实内容）、`README.md`，必要且窄范围的包级 `.gitignore`。
- 新源码仅 `deployment/AgenticRAG/src/agentic_rag/` 内当前需要的包入口、domain、config及模型能力协议。可用少量文件或职责明确的目录，不生成空框架、存储实现、Agent循环、worker、检索器或未来API适配器。
- 正式测试 `deployment/AgenticRAG/tests/test_domain.py`、`test_configuration.py`、`test_package_install.py`；若名称需更精确可同职责命名并交付清单，不改R01–R04正式测试/探针。
- 新文档 `deployment/AgenticRAG/docs/domain-and-configuration.md` 和 `docs/implementation-records/R05.md`、`R05-*.json/txt`正式验收证据。此dispatch由Leader维护，执行者不改。
- 可在 `C:/Users/18221/.cache/codeplus-agenticrag/` 建本项专用Windows核心环境，及系统Temp下明确命名的R05安装/构建验证目录；保留可复用专用核心环境需在交接说明，构建包/临时测试环境/脚本/数据默认结束前清理。

禁止改根pyproject/uv.lock、codeplus生产代码/命令、根README、冻结eval、旧清理补丁、已有compose、R00–R04文件或用户数据。Leader未交权前后不要并发写。依赖不安装到根 `.venv` 或已验收CUDA worker环境，两个平台各自环境。

## 必须交付

1. 发行名 `codeplus-agentic-rag`，导入名 `agentic_rag`，src布局。最终R26把同一实现移到根 `agentic_rag/` 由CodePlus主发行包含，当前只建独立包。核心基础仅实际需要的轻量schema依赖（pydantic与宿主约束相容）；torch/transformers/pymilvus/codeplus/TUI不得作为核心强依赖或import副作用。未实现extra不做空承诺，不加另一产品CLI。
2. 库、文档/文档版本、section/chunk、处理快照、revision/成员、批次、运行、证据/引用等当前存储与后续调用所需统一ID/状态/错误和schema_version。文档身份不等于内容hash；使用不可变显式数据类型及有意义的不变量，拒绝非法区间/字段/版本，保持序列化可回读。不要为了“完整”生成大量空服务层；R06才实现存储协议。
3. 单一知识库配置Schema、清楚的来源优先级与显式装配函数，核心不自行读取CodePlus全局配置/环境。固定profile能力、backend、模型精确revision、tokenizer、模板、维度/归一化、精度/推理实现/设备与输入边界；未知backend/错误能力选择/非法组合明确失败。首版可只承认R03实测qwen3_local，API尚无锁定协议，不伪装“任意URL可用”。CPU本地模型仍不支持，不静默fallback。
4. 实际处理快照含完整解析后的配置与版本化规范指纹，不只存可变profile名；深层不可变，不因外部dict/list变化漂移，不含密钥。编码/索引身份与运行预算、Rerank选择区别清楚：parser/chunker/tokenizer/文档模板/embedding身份变化必须可判不兼容；仅改qa/report预算或Rerank不误改文档编码身份。相同profile名不同实际配置不视为同一身份。R16才做重建确认/执行，R05不自动重建。
5. fixed/auto只属于knowledge；最终默认auto，qa/report与之正交，两种模式仍可多轮。配置基础路线/候选/context、qa/report各自预算及单次覆盖的解析不写回默认值、不改宿主权限mode。数值试验起点与已测能力限制分开，不能把512/64、50/60、8/8000、4:1等或自选预算称为已冻结质量参数。可要求显式试验配置，不默默推出新生产承诺。
6. 小而明确的Embedding/Rerank能力协议使用请求/候选ID、实际profile和请求上下文；业务不依赖具体GPU实现。当前没有真实worker/模型适配器时清楚说明未实现，调用缺失能力给可诊断错误；能力接口不创建聊天模型或复制Agent。

R03实测身份：Embedding revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`，Reranker `e61197ed45024b0ed8a2d74b80b4d909f1255473`；bf16/SDPA/cuda:0，Embedding1024维末有效token归一化；完整输入2048、batch<=4且padded<=4096是已测保守起点（不是平台最大值/SLA）。模板、query指令、yes/no分数和依赖准确内容从R03文件读取，不凭本卡摘要重写。模型权重/cache/凭据永不入Git，本项不启GPU或下载模型。

## 验收与交接

- 必须真实构建开发wheel和sdist→wheel，在两个隔离干净安装验证环境中安装，从非仓库cwd且清空PYTHONPATH，用隔离Python模式确认site-packages导入路径、包版本/metadata、domain/config调用、无宿主/GPU模块加载。不以源码可import或editable成功替代。R05不声称Linux/宿主联装已验收；R25/R26仍要实测。
- 锁定实际安装验证用版本/环境，检查wheel/sdist清单仅含该包必要源码、许可证、metadata；排除eval/probes/报告/用户数据/缓存，资源Compose归R25，当前不复制已有compose。构建命令可用 `uv build deployment/AgenticRAG --out-dir <绝对自有目录>`；sdist作为显式SRC再建wheel。
- 正式测试覆盖配置优先级和单次覆盖不污染、能力与输入边界、序列化/严格schema/不可变快照、身份变化与不变关系、未知依赖/backend、安装产物；不只镜像字段断言，不以mock代替安装证据。不需要重跑GPU/Milvus或全仓测试。
- 真实命令/cwd/解释器/依赖、退出码、失败修复、文件与产物SHA、分发包清单和清理分别记录R05.md/机器报告。遇到真实缺环境可准备独立工作但不能标通过；必要产品改变才向Leader报告，不重复需求访谈。
- 自查全部R05五条和G01–G10；不填Leader ACCEPTED或C01–C05。交付精确文件清单/哈希、临时资源结束状态。停写并明确交回唯一写权限，等待Leader独立验收/修复反馈。Leader通过后精确本地提交R05，继续R06。
