# RAG精简实施任务

工作区：`D:/CodePlus`。截至2026-09-28：下列接入调整、历史清理及冻结检索评测已执行；结果和真实回答验收边界见[验收记录](production-simplification-acceptance-20260927.md)。

## 目标与范围

RAG仅提供`knowledge_search`、`knowledge_open`及来源信息。删除重复的Agent控制，复用Agent现有推理、工具循环、compact、回答、文件输出和会话继续能力。

不新增预算类、答案校验类、业务表、累计候选池、最终选择流程或LangGraph图；不增加第三路召回、额外收尾精排或证据压缩。保留现有索引、解析器和模型实现。

## 关键spec

| 项目 | 要求 |
| --- | --- |
| 检索参数 | 原512/64分块、标题和query指令；Dense/BM25各最多50；RRF `k=10`；融合去重后最多24块精排；Qwen3-Reranker-0.6B；`min_score=0.001` |
| 过滤顺序 | 完整精排及响应校验→保留分数`>=0.001`→单次输出上限→正文构造；不补齐，允许空结果。关闭精排时不对原始召回分数应用该阈值 |
| 返回限制 | 保留单次`context_chunks/context_tokens`、分页、原文裁切和去重；取消跨调用累计正文、块数、search/open配额及finish reserve |
| 工具接入 | 加入现有registry，保留普通工具；不关闭Agent记忆、文件历史或协作能力。退出时卸载本次绑定，恢复原有同名工具及启用状态 |
| 来源与资源 | 保留版本隔离、原文坐标、必要参数/响应检查、历史来源读取和真实交付记录；正常结束、异常、取消均释放worker/租约/pin |
| 兼容 | 保持知识命令、工具参数和返回结构稳定；旧配置/冻结快照可读。确需变更时同步调用方、兼容读取、测试和文档 |

配置使用[retrieval-selected.json](retrieval-selected.json)，经现有`assemble_configuration`合并到实际知识库配置；先从CLI/TUI/Remote设置确认配置路径。保留auto/fixed模式，不重建索引。

既有development对照：807题文档Recall@5/@10为83.14%/89.38%；505道有句子标注题完整证据覆盖为96.67%/98.35%。5/10为评测窗口，不是生产固定返回数；独立test未参与调参。

## 需删除

以下RAG路径相对`deployment/AgenticRAG/src/agentic_rag/`。

| 位置 | 删除内容 |
| --- | --- |
| `adapters/codeplus/policy.py` | 探索/收尾/引用修复状态机；`prepare_turn`、`begin_finalize`、`assess_output`专属控制；强制答案JSON与修复重试 |
| 同文件 | `_exploration_window`、`_trim_exploration`、`_trim_finish`；`_advance_open_window`中修改会话历史的逻辑；报告/续研专属编排 |
| `policy.py`、`adapters/codeplus/ledger.py`、`adapters/codeplus/meter.py` | 回答预算、阶段配额、finish reserve及DeepSeek回答协议硬绑定；保留仍被来源读取使用的被动交付记录 |
| `sources.py`、`config.py` | `_call/_token_allowance/_commit_result/_select_context`中的跨调用配额控制；`RunBudget/Budgets`活动执行依赖，旧字段仅在兼容读取边界处理 |
| `codeplus/agent.py` | RAG替换registry、停用Agent能力的分支；两套运行循环中的RAG专属prepare/assess及答案批准后保存分支 |
| 测试、文档、实验目录 | 只服务于已删除行为的断言、旧协议、第三路及其他结束的优化实验、过期资料；按下方清单执行 |

## 需新增

1. 必要的知识工具装卸接线：复用`SourceTool/SourceSession`和`ToolRegistry`；当前registry没有卸载接口，仅补齐卸载/恢复所需操作。
2. 在现有正式测试中补齐工具共存、绑定恢复、知识库切换、取消释放、多次检索和正常compact场景。已有阈值测试直接复用。

## 需调整（需最小重构）

| 文件/入口 | 工作内容 |
| --- | --- |
| `adapters/codeplus/policy.py` | 提取工具调用、资源绑定和结果映射，删除上述控制；剩余职责集中为薄适配。调用方迁完后删除无引用的旧接口/文件 |
| `sources.py` | 解开单次返回限制和累计配额；保留搜索、分页、来源映射及现有互补排序 |
| `retrieval/search.py`、`config.py` | 复用已完成的阈值实现，落地选定配置，保留trace及旧快照兼容 |
| `adapters/codeplus/ledger.py`、`evidence.py`、`citations.py` | 保留必要来源/交付记录，解除其对答案结束、修复和预算的控制 |
| `adapters/codeplus/management.py`及配置加载入口 | 解除对整个policy配置的依赖，留下存储、worker、检索、单次返回配置读取 |
| `codeplus/agent.py`、`codeplus/tools/__init__.py`、`codeplus/run_policy.py` | 接回普通工具路径，仅补必要装卸；保留核心循环及仍被`ToolResult`使用的`SourceSpan`等类型 |
| `codeplus/__main__.py`、`codeplus/app.py`、`codeplus/remote.py` | 同步CLI/TUI/Remote接入与知识状态判断；原问答、报告、继续入口复用普通Agent能力 |
| 正式测试、评测入口、README和有效运维文档 | 更新调用与说明；保留有效回归场景和数据计分口径 |

## 执行顺序与完成标准

1. **实现并同步调用方**：先移除重复控制、接通三个用户入口，再合并检索配置；保留已有未提交修改，不改其他任务文件。
2. **正式回归**：检索阈值边界、全过滤为空、不补齐；普通工具共存、同名工具恢复、分页、切库、旧快照/来源读取、取消回收；现有compact、流式输出、权限和文件输出正常。更新`test_codeplus_integration.py`、`test_user_commands.py`等，按剩余职责调整`test_run_budget.py/test_request_delivery.py`。
3. **实际入口与质量验收**：复跑807题development，对比融合、精排和最终正文，逐题检查原有文档/完整证据命中；CLI/TUI/Remote验证问答、重复检索、长会话、报告保存、继续和取消；用既有线下评分入口对照答案与来源。方案固定后按原协议运行一次独立test；未执行或HTTP402阻塞单列。
4. **清理**：新路径验收后解除旧文件引用，按D组删除，更新文档链接；D10/D11在新对照完成且旧运行停用后处理。复核P01哈希、包安装及正式评测入口，删除本次临时脚本、环境和日志。

交付：代码改动、更新后的正式测试/文档、实际验收结果、已删与仍待手动清理的路径。未经授权不提交或推送。

## 清理资料入口

[验收与清理记录](production-simplification-acceptance-20260927.md#清理与提交范围)汇总各组结果、输入保护核对及迁移位置。原逐文件`cleanup-inventory-20260927.json`、`cleanup-pending-20260927.md`与`manual-cleanup-20260927/`保留在本地，不纳入源码提交。D组是旧产物，P01为带SHA256的受保护评测输入，P02–P04保留有效内容并按本任务调整。

清理前复核路径、修改时间和归属；链接只处理本身，不跟随删除目标。`eval/scifact-eval/runs/annotations/`属于评测集，不能删除整个`runs/`。共享模型、根`.venv`、用户会话、文件历史、Agent记忆和其他任务工作树不在删除范围。
