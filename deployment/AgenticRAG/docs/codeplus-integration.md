# CodePlus 知识工具接入

`KnowledgePolicy` 是工具与资源绑定适配层。它将知识工具注册到现有 registry，保留普通工具、记忆、文件历史、协作和权限能力。Agent 使用原有流式回答、工具循环和 compact；适配层只记录来源交付与运行结果，不要求答案 JSON、不校验或修复答案、不操纵对话历史。

## 配置

宿主 `knowledge_development_config` 为绝对路径，JSON 包含 `knowledge`、`worker`，可选 `cleanup_grace_ms`、`search_result_chunks/search_result_upper`。`DevelopmentConfig` 位于 `agentic_rag.config`。旧配置中的回答 tokenizer、输出配额及迭代控制字段只在读取时忽略；旧知识快照中的预算数据仍可读。

回答模型沿用 CodePlus 当前 provider。单次工具正文使用 UTF-8 字节上界计量，计量身份为 `utf8-bytes-v1`。`context_chunks/context_tokens` 与可选 search 单次上限取较小值，不扣除此前调用。主 Agent 的上下文和 compact 规则照常执行。

## CLI、TUI 与 Remote

```text
/knowledge create 我的资料
/knowledge import "D:\资料\手册.md" "D:\资料\记录.txt"
/knowledge status
/knowledge sources
/knowledge ask --mode auto 这两份资料有哪些共同结论？
/knowledge reimport <文档UUID> "D:\资料\修订手册.md"
/knowledge remove <文档UUID>
/knowledge open <历史引用UUID>
/knowledge use <另一库UUID>
/knowledge off
```

`create/use` 选择库；`status` 显示发布版、模型差异及待恢复批次。`sources --revision <版本UUID>` 查看历史成员，`open` 接受当前答案中的 evidence UUID 和旧 saved citation UUID，读取其已确认送达的归档原文。`ask/report/continue` 后的正文只作为用户问题，不再次解析为命令。

```text
codeplus -p "问题" --knowledge-library <库UUID> --knowledge-mode fixed --output-format stream-json
/knowledge report --output "D:\Reports\报告.md" 比较资料并保存报告
/knowledge continue --output "D:\Reports\补充.md" 补查未解决的问题
/knowledge continue --run <运行UUID> 继续补查
codeplus -p "生成报告" --knowledge-library <库UUID> --knowledge-report report.md
codeplus -p "继续补查" --knowledge-library <库UUID> --knowledge-continue <运行UUID>
```

问答、报告与继续均走普通 Agent。报告路径进入用户任务提示，由普通文件工具完成写入，遵循权限与读后覆盖规则；不存在答案审批后的专属保存分支。继续沿用会话或已保存运行线索，新一轮绑定当前发布版，历史来源不会自动变成本轮交付证据。

`fixed` 的 search 只接收 query；`auto` 还接受 `strategy: dense|bm25|hybrid` 与 `rerank: bool`，每次缺省选项使用冻结基础配置。模型不能覆盖库、版本、候选数或单次返回上限。BM25 关闭精排时无需连接模型；失败返回明确错误，由普通 Agent 决定下一步。

失败文件用 `/knowledge retry <批次UUID>`，接受变化输入时追加 `--accept-input-changes`。中断导入用 `/knowledge recover <批次UUID>` 查看后选择 `--choice continue`，或 `/knowledge abandon <批次UUID>`。模型变化使用 `/knowledge model` 查看提案；`confirm/retry/keep_original` 的原确认规则保持。

## 绑定和交付

正常结束、异常或取消后，适配层卸载本轮工具并恢复原同名工具及启用状态。工具退出前先等自身线程与模型请求真实结束，再释放 worker、租约和版本 pin；取消确认不等于底层执行已结束。

工具正文附带原文区间映射，随宿主真实请求传播到 DeliveryGateway。实际请求结果记录在 `host_runs.detail.model_requests`，用量汇总到运行记录；`delivery_receipts` 继续保存真实正文交付状态。此记录不控制答案结束、输出格式或重试。

TUI Ctrl-C 和 Remote 取消沿原任务取消路径执行；管理任务的发布回执、忙状态与恢复规则保持。代码及必要场景由 `test_codeplus_integration.py`、`test_request_delivery.py`、`test_user_commands.py` 验证，受控请求测试与实际供应商执行分别报告。
