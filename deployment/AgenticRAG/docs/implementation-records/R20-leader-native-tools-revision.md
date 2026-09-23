# R20 Leader 退修：原生 JSON 与探索工具生成冲突

基线仍为 41802ffe9061856dc8d2ea1e4ef0fdbdad1d143a，D:/CodePlus / codex/rag。当前未接受、未提交。执行者第二次交付 manifest SHA256 为 d54d0ce6041093c94e64233762334ef3b46f5bcc7feb9a6afca8a9314e5e52fb；快照在 Leader 私有 revision-verified/executor-manifest.json。原先的报告保存、研究续接、严格引用和收尾配对去重保留。

Leader 独立重建得到相同 host/RAG wheel；22 authored哈希、240份原始证据、6537继承状态和保护文件核对通过。最终安装包相关回归248 passed（报告/集成138、窗口/送达61、模式/预算49），普通宿主148 passed / 1 Windows条件skip；直接wheel及sdist重建的清洁安装通过。只读diff检查首次临时关闭autocrlf导致CRLF误报，使用仓库真实设置复验通过；源码和Git配置均未为此修改。

独立真实新目录四轮累计26711 tokens，正常整链和独立audit均FAIL。原始结果在 C:/Users/18221/AppData/Local/Temp/codeplus-r20-leader-20260923/revision-verified：

| 轮次 | run_id | 实际结果 |
| --- | --- | --- |
| TUI report | b25cb10c-d95c-434c-b301-f134f8c54d2d | completed/saved，2search/0open，7388 tokens。所有主要事实、审计及2023排除正确，但正文虚称已open。 |
| 实际 CLI continue | a71c5b9f-b6e7-4979-8e1b-5f45bbf2abc7 | incomplete/no_evidence，0search/0open，4207 tokens，无文件。未采集其公开首稿，不能猜测内容。 |
| update continue | a34874e2-dcf9-49b0-b2dd-1c4fabcf48e0 | completed/saved，2search/0open，11307 tokens。资格更新和blue/ocean保留；正文再次虚称open，且一条局限把肯定句称为否定表述。 |
| delete continue | 62e540b1-8272-4d3e-b511-3db0dc61c9db | incomplete/no_evidence，0search/0open，3809 tokens，无文件。公开草稿给出了直接协议冲突证据。 |

delete 的实际请求同时携带 response_format=json_object 和两个知识工具。公开 delta.content 草稿以 JSON markdown 开始，随后出现提供方原生 DSML calls/invoke/parameter 标记，而SDK没有收到真正tool_calls。唯一请求正常stop后，被现有no_evidence门正确拒绝。公开草稿 SHA256 为 b0931fef52002905701ec31dc04646f4817a8b6976263ee24b7f1bb041b4968f；原件 real-delete.json、semantic-review.json 和 live-audit.json 保留。这是实际JSON/工具生成冲突证据，不应继续归因为未经证明的历史提示顺序。短预检曾成功，不能证明当前长研究请求中的组合稳定可用。

## 本轮精确范围

1. 只改变一个运行变量：知识compat的原生json_output仅在工具禁用的finalize/citation_repair启用；探索agent恢复此前已验证的普通工具调用模式，compact和普通宿主保持原样。SDK、真实请求预览和最终发送门必须一致，完整模板仍实际计量。
2. 公开草稿仍严格解析，必要时只使用已有一次citation_repair。保留当前完整配对去重及“候选完整请求已装入窗口才采用”的边界。不能解析DSML字符串来执行工具，不能强制tool_choice/open、放宽引用、增加模型重试、扩大预算或修改已冻结原件。
3. 当前SYSTEM/REPORT提示、继续记录内容与顺序先保持，避免混入第二个实验变量。若后续真实结果仍暴露问题，先报具体公开证据，不做无目标反复提示调整。
4. 更新实际SDK/preview契约测试，明确agent有tools但无response_format，两个收尾purpose无tools且有固定JSON格式，compact/普通调用无格式字段。人工裁剪夹具仅按新真实wire条件调整必要padding或说明，生产预算/引用/分页断言保留。
5. 执行最终直接受影响组：report、cursor/request-window、context、JSON字段及模式/预算。Leader已通过的138集成与148普通宿主无需无变化重复全跑；涉及实际新改动再选对应回归。最终源码、两环境安装包与构建哈希匹配，旧host源码未改时保留其已验证结果。
6. 同一已批准提供方、模型和预算，在一个新独立资料目录验证report→实际CLIcontinue→update→delete，每个预定动作一次。保留每轮公开草稿（现有可观察入口）、实际tool_calls/送达和精确状态，直接读正文；CLI首稿未采集就明确未采集。失败不覆盖，不重跑同一动作以凑通过。此轮目的为验证JSON约束与工具生成的分离，不宣称R23语义质量已通过。
7. 更新R20记录、23路径清单及原始证据索引，修正当前文档中agent启用原生JSON的声明。保护本卡、两次Leader证据、继承输入、根双语README、旧11份SQL和历史报告。结束全部命令后STOP_WRITE，不stage/commit/push。

Leader全部独立验收命令已结束后才交权。同一执行者接续本次有直接证据的最小退修；本轮不开展清理、重做环境或材料复制。
