# AgenticRAG

AgenticRAG 为 CodePlus 提供知识库管理、`knowledge_search`、`knowledge_open` 和来源信息。回答、工具循环、compact、文件输出和会话继续由现有 Agent 处理。知识工具加入当前 registry，运行结束后恢复原工具及启用状态。

当前使用独立核心包及 CodePlus 适配层，Windows 宿主与 GPU worker 可以使用不同 Python 环境。知识库管理支持创建、导入、更新、删除、重建、恢复、版本读取和索引回收。解析范围为 Markdown 与纯文本。

## 使用

宿主设置中的 `knowledge_development_config` 指向绝对路径 JSON，包含 `knowledge` 与 `worker`。在 CLI、TUI、Remote 使用相同知识命令：

```text
/knowledge create 我的资料
/knowledge import "D:\资料\手册.md"
/knowledge watch "D:\资料"
/knowledge ask --mode auto 这份资料给出了哪些条件？
/knowledge report --output "D:\Reports\报告.md" 比较资料中的方案并保存报告
/knowledge continue 补查尚未解决的问题
/knowledge sources
/knowledge off
```

CLI 也可使用 `codeplus -p "问题" --knowledge-library <库UUID>`。报告通过普通文件工具保存，遵循宿主权限和覆盖规则。命令、切库及恢复说明见[宿主接入](docs/codeplus-integration.md)。

`watch` 开启文件/目录自动同步，TUI/Remote 运行时通过文件通知与 hash 核对发现更新；CLI 使用 `/knowledge sync` 核对一次。兼容配置下只写变化文档的 chunks，发布成功后新运行使用新版，旧版本数据在旧任务结束后回收。详见[文档更新](docs/ordinary-mutations.md)。

## 检索与来源

选定配置为 512/64 分块、Dense/BM25 各最多 50、RRF `k=10`、融合后最多 24 块进入 Qwen3-Reranker-0.6B。完整精排及响应检查后保留 `score >= 0.001`，再执行单次输出上限与正文构造；不补齐，允许空结果。关闭精排时不将阈值应用到召回分数。配置见[retrieval-selected.json](docs/retrieval-selected.json)，通过 `assemble_configuration` 合并到知识库配置，不重建索引。

fixed 固定单次检索路线，auto 允许 Agent 选择 Dense、BM25、Hybrid 与精排开关。两者均可多次搜索和分页阅读。`context_chunks/context_tokens` 只限制单次返回；没有 RAG 累计搜索/阅读次数、回答预算或收尾预留。

每轮固定已发布版本；归档保留原文坐标、章节和分页信息。真实交付记录与历史引用读取继续保留。worker、租约及版本 pin 在正常、失败和取消路径释放。详细行为见[检索](docs/retrieval.md)、[来源与交付](docs/sources-and-evidence.md)。

## 开发与验证

```powershell
uv sync --project deployment/AgenticRAG --locked
uv build deployment/AgenticRAG --out-dir <绝对产物目录>
```

核心依赖与 CUDA、Milvus、CodePlus 宿主依赖分离。`local-models` 依赖使用本模块 Windows 锁；模型从显式缓存读取。安装与正式测试命令见[环境与命令](docs/environment-command-matrix.md)。

正式评测保留 MultiHop 原题、答案、Gold 与评分规则，以及 SciFact development/test 划分和句子标注。检索评分、实际 Agent 运行和答案评分分别报告，入口见[开发评测](eval/README.md)与[SciFact](../../eval/scifact-eval/README.md)。

## 文档

- [配置与兼容](docs/domain-and-configuration.md)
- [存储与并发](docs/storage-and-concurrency.md)、[输入快照](docs/input-snapshots.md)、[解析与来源映射](docs/parsing-and-source-maps.md)
- [首次发布](docs/first-publication.md)、[普通增删改](docs/ordinary-mutations.md)、[恢复](docs/manual-recovery.md)、[索引生命周期](docs/index-lifetimes.md)
- [模型 worker](docs/local-model-worker.md)、[模型切换](docs/model-switching.md)
- [环境与验证命令](docs/environment-command-matrix.md)
