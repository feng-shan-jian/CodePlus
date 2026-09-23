# R21 Leader 退修：问题正文不得再次分派为命令

基线仍为 00fbbbb82509c61e43b3b01be9c68273498ab716。Leader 独立安装的原最终包 host 916d1551dbf20147cf685ead1e7914a53ec6a11bfc1153b27612921d318be8b7、RAG e53c92bef0fe9523e22cfa93bfca7fc6f3bd4756ee12dd9eff5cb43a51ec8da2。原执行者 manifest SHA 0f9590c6815c4252c6cb0db37872ef5b4a066693d936ab6dae6399eefe74dfe4 已完整复制到 Leader 私有根 executor-manifest.json；不得覆盖原失败证据。

## 必修问题

用户已经用 ask/report/continue 选定研究动作后，后续正文是数据。当前 CLI 的 _run_prompt 递归会再次解析以 /knowledge 开头的问题；Remote 的 _run_knowledge 再次调用 _handle_user_message，也会把以 / 开头的问题交回普通命令分派。

独立实际 main 探针：-p "/knowledge ask /knowledge status" --knowledge-library <已发布库 UUID>，结果 action=status/status=completed，main 原退出 0，但没有任何 run。期望问答的断言失败（进程1，validation仍CHECKING），记录在 Leader 根 slash-cli.log、slash-cli-data/validation.json、requests.json、slash-reproduction.json。它还可能将用户正在讨论的命令误执行为管理动作，必须修复。

独立真实 localhost WebSocket 探针将既有 HTTP 等待测试的问题仅替换为 /knowledge ask /knowledge status：两个 HTTP cancel/disconnect 用例均没有进入模型请求并超时，原三项权限场景通过。slash-remote-v3.xml/command/modules 中为 2 failed / 3 passed / 2 deselected。此前 v1 的 __file__ 收集不一致、v2 跨盘 cwd 收集错误也保留，不能当作产品失败；v3 才是有效复现。

修正只需保持外层命令分派一次，内部知识正文直接进入已有 Agent 运行路径；可移除 CLI 对正文的递归分派或增加明确内部入口语义，Remote 已有 _knowledge 标志可以复用。不要对正文加字符串转义/删前缀/内容黑名单，不改真实用户问题，也不要构建新的命令框架。顶层普通 /knowledge 管理命令仍可用，普通 Remote 斜杠命令不受影响。

补正式回归：CLI 实际 main 和 Remote 对以 /knowledge 或其他 /command 开头的 ask/report/continue 正文产生真正的知识运行，保留原正文，不能调用嵌套管理或 session 命令。受控 transport 清楚标注；保留正常退出码、版本与已修复取消/权限事件行为。

## 既有独立通过证据与复验范围

Leader 私有根 C:/Users/18221/AppData/Local/Temp/codeplus-r21-leader-20260923：
- 最终生产资源源码/wheel/core/cuda 相等，全部 524 份原证据核过；6537继承路径、11SQL与保护文件一致。
- commands-host 151、core-lifecycle 83、report-integration 138：共 372 passed，无 skip。
- 六类 CLI OS 退出码与 PASS 标记/数据库终态通过。
- 新建/部分失败/恢复放弃/显式重试、真实 QA/report、同文档 reimport、模型 retry 等待/confirm GPU重建/重复confirm、Remote续研、删除后历史引用整链 PASS。
- 三真实 run 全 completed：4bfc08ca-ea41-46a4-89e3-4fc746ab4ab0、b8e7652d-098d-42a3-a49e-9604ce129486、3c6a4b01-9978-4bdf-9b1b-66a0c5b98c5f；15099tokens，6search/0open，104962ms。独立只读 SQLite/归档/引用/确认送达范围/文件SHA/父关系/pins/模型账本核对通过，主库和 sidecar 全部字节未变。
- executor 同法独立回读 PASS（14747tokens），原 audit.py 写错 ArtifactSave.bytes 导致 KeyError；修订 audit-v2.py 使用正式 size_bytes 后 PASS，原失败保留。
- Remote原文称“已在本轮实际打开核对”，但实际 0 open；报告把 registry checked 略扩成“证书/登记做过检查”。这属于 R23 语义/过程真实性样本，保留原文，不扩大 R21、不调提示或强制 open。

本次退修仅重建两包并核定变更模块、运行直接相关 CLI/Remote/TUI/命令/入口回归与以上新增回归。未变 RAG/core 生命周期、原真实普通正文链可按字节适用性沿用，不重复无变化GPU/付费答案质量链。如果修正实际扩大了影响，再解释额外验证需要。

同一执行者恢复唯一写入。Leader 自本次 STOP_WRITE 交接后只读；不要改 Leader 根、Leader文档、R20后记/checklist/分派卡。原证据和原 manifest 副本保留，新记录精确区分原树与修订树的包/模块SHA和测试适用性。不stage/commit/push，全部命令结束后再次 STOP_WRITE。
