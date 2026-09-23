# R20 Leader 退修：报告输出格式与真实验收边界

2026-09-23，基线仍为 41802ffe9061856dc8d2ea1e4ef0fdbdad1d143a。本项尚未接受、未提交。Leader 独立构建的宿主/RAG wheel 与首次交付一致；221 项相关测试通过，普通宿主 139 通过/1 Windows 条件跳过，直接 wheel 与 sdist 清洁安装通过。首次交付 manifest SHA256 为 aef75d366a4ca2150e5a028fc8a5bf9d2cc4df498432783b938c4fc0df187afe。

独立真实报告经 CodePlusApp 的 Textual 命令分发执行，再以实际 CLI 续研。报告路径、权限、文件回读、普通会话恢复和版本绑定均已运行；但真实输出尚有以下未解决问题，不能以驱动 PASS 或 completed 状态替代正文验收。

| 运行 | 真实结果与发现 |
| --- | --- |
| 17223b73-46c8-4943-ac66-eaee8721538a | TUI report completed/saved，28097 tokens。先 invalid_json，再进入 citation_repair；修正窗口将12个候选正文裁为3个，移除已送达的 atlas.md。最终误称 Atlas 2024 blue certificate/ocean registry 无法核实。 |
| 79b35cb1-9039-498e-a277-022d1ed5c888 | 实际 CLI 同版续研 completed/saved，38930 tokens。同类格式错误与资料遗漏复现；另有 Beacc、2014/2024 和可见 Unicode 转义等报告质量反例。 |
| 78f44521-cabe-451e-8871-fa9e5bd6924e | 更新发布后 incomplete/citation_invalid，63488 tokens；本轮未确认的引用和尾换行遗漏被正确拒绝，没有报告文件。不能记作正常更新报告通过。 |
| 9e435d90-aaf4-4e03-82a5-e61fe4123cfa | 显式开启新 CLI 续研，承接失败父运行，completed/saved，50570 tokens。但仍先 invalid_json，修正后丢掉更新的 Atlas 正文，未正确得出新夜间资格已认证。 |
| f6b5b2cb-99ce-4e17-b56d-1884b420e4e2 | 删除后的实际状态 partial/search_limit，24201 tokens，10 search/0 open；只有当前 Beacon 引用、Atlas 未核实，历史资料没有回灌。正式驱动 delete 分支仍输出 PASS，因其缺少完成状态/成功 open 的必要断言；Leader 独立 audit 已正确报失败。正文还错误声称执行了 open。 |

原始证据在 C:/Users/18221/AppData/Local/Temp/codeplus-r20-leader-20260923 的 real-report.json、real-cli.json、real-update.json、real-update-continue.json、real-delete.json、quality-observations.json 和 live-audit.json。五轮实际累计205286 tokens；任何后续重试或配置实验都须保留原记录。首次验收总脚本 accept.json 为 FAIL，不能覆盖成通过。

## 精确退修范围

1. 先采集实际交给 assess_output 的公开草稿文本，确认 invalid_json 的直接形态；不记录隐藏思考，不额外生成答案。检查 R20 新增 REPORT 提示是否把“结构化 Markdown 报告”误变成顶层输出格式，与现有 JSON 契约发生歧义。基于证据做最小提示修正，明确报告正文位于既有 markdown 字段，引用和公开进度仍用既有 JSON 结构。
2. 不增加 Agent、翻译模型、规划器、额外格式修复模型、自动后台重试；不放宽引用真实性、精确偏移/换行校验，不改生产预算来掩盖失败。已有 report/continue 参数和持久数据保持。
3. 正式真实驱动必须按实际状态分层。若将删除动作定义为正常 search/open 完成验收，须检查 completed 和成功 open 的送达证据；partial 或零 open 另行报告，不能输出无条件成功。
4. 新实验使用独立数据目录、同一受控提供方和明确相同预算/配置。只做有限有目的验证，保留全部失败；重点看有效 JSON 能否直接通过、Atlas 原始事实与更新后的夜间资格是否正确进入报告、删除后当前证据范围及旧文件/历史引用保全。若要改变实验配置，明确说明变量和依据，不能为拿到通过而反复重跑。
5. 原385/2、24/2及最终cursor2通过、Leader221/139通过均保留。仅针对实际修改运行必要报告/窗口测试；不重复未受影响宿主全量。若改源码，最终 host/RAG 包重新构建核对并安装，源与包匹配；Leader 接手后独立验证最终改动。
6. 较广泛的语义质量/忠实度/时延门槛仍由 R23 完成；本次已见的格式歧义与更新报告可用性先在 R20 解决。不能宣称有限样例代表质量门槛通过。

执行者取得 Leader 明确交权后才写入。保护本退修卡、Leader 首次验收证据、继承数据/根 README/十一份 SQL、原报告与原始日志。范围限定 policy 的必要提示/输出契约、正式真实驱动、直接相关正式测试与 R20 文档/交付清单。全部结束后重新 STOP_WRITE，不 stage/commit/push；清理仍交用户另派。
