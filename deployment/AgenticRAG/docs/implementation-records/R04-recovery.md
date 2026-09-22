# R04 恢复交接（2026-09-22 07:55 +08:00）

Leader 已按当前外部状态重新核对。本次恢复保持 R00–R26 完整目标；前一轮形成了 R03 提交，属于实际进展，R04 尚未验收。

- HEAD `43e9af9de44212342850f3668826a0e5b4bf9a49`，分支 codex/rag，index空。
- 当前 collaboration.list_agents 仅返回 /root；原 /root/r04_host_contract 句柄已缺失。进程检查没有 R04 测试/脚本仍在执行（检查命令自身除外）。不是凭等待超时重启。
- 原 R04 只有 contract.py 和 test_host_contract.py 草稿，尚无 host-integration-contract.md、environment-command-matrix.md、R04.md、交付清单或正式结果。不得把草稿或前一轮聊天中的设计意见算通过。
- 保留原草稿：contract.py SHA256 `7accb8e88a0ba7c6b42f884a8949b323d9a07fd040be228ea8114c3162d4470e`；test_host_contract.py SHA256 `34f810107f72f1d2e7b2152c6bf9e98eafd1b2e3c054c40b02485fc5a87ab0ee`。
- 状态计数：21个tracked修改、5904个继承删除、625个untracked。独立目录除既有compose外，仅台账/R03 SHA回填和4项R04输入/草稿发生变化。
- 重跑R00保护校验：6553路径检查通过，只有既有授权check.py、run.ps1、checklist、task-plan四项与初始输入不同，零新增越界变化。临时审计脚本codeplus-r04-recovery-audit.py已删除。
- 祖先及独立区磁盘AGENTS本次未发现；用户直接给定AGENTS要求仍适用。

恢复执行会话 /root/r04_host_contract_resume1 接任唯一写入者，范围仍严格依R04-dispatch.md；原会话不再持有写权。Leader持有本记录、分派卡、台账与R03提交回填；执行者不要重写这些文件。完成后停写，供Leader独立复验及本地提交。

## 前一轮已发现、尚需落实的设计边界

1. 本机OpenAI/Anthropic SDK均默认max_retries=2；Async客户端支持with_options(max_retries=0, timeout=...)。RAG不能把一次stream当一次实际请求而漏计内部重试；不要突变共享client，明确HTTP transport/hook及关闭的所有权，不能关闭仍由普通会话共用的连接。
2. 当前Compat实现没有完整保留finish_reason=length/异常EOF，usage尾chunk硬编码end_turn；不能因返回字符串就判completed。结束原因、usage缺失和输出硬上限各自清楚。
3. compact有三轮prompt-too-long重试且泛catch；硬预算/取消信号必须穿透，不能被当摘要失败后继续正常调用。所有compact/重试/修正均记账，未知usage不计零。
4. 冻结RAG实际工具集合。AgentTool/后台team/worktree/MCP/命令等可能另开未计量运行，必须有可实施约束；可以采用本功能必要的run局部工具集合并沿用同一Agent循环，普通任务不变。报告保存不能经通用WriteFile等绕过引用校验；应复用宿主权限而非重写未经校验正文。
5. 草稿confirm_receipts只在Anthropic payload中看到is_error；OpenAI两条serializer会丢此字段。可信来源映射必须带成功资格或引用已验证成功候选registry，不能因错误正文恰好包含原文而授予证据；测试覆盖三协议。run/kb/revision等身份由可信核心绑定，不接受模型参数伪造。不要靠substring搜索定位重复原文。
6. 旧清理历史报告还包括“评测替换未提交”和旧隔离临时清理被审批拒绝。按本次最新精确seam/冻结输入要求衔接，不采纳历史“顺带提交继承评测/compose删除”的建议。不在R04应用清理补丁或处理其临时目录。
7. 草稿已有实际宿主serializer、截断/spill/compact、SDK MockTransport实验；这些只是本项接口实验，不是实际Agent/联网模型验收。先审阅草稿并完善必要边界，再运行与记录真实命令及失败；不用重新写第二套相同实验。

交付仍须完整R04五条checklist和G01–G10，不能以某个小实验通过替代设计、宿主白名单、包边界或环境命令表。R04通过提交后继续R05，不在中间节点停止。
