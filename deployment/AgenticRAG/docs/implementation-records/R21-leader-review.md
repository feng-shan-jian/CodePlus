# R21 Leader 独立验收

结论：**ACCEPTED，待精确本地提交**。现有 /knowledge、TUI、codeplus -p 和 Remote 的库管理、问答/报告、恢复与模型选择已贯通。基线为 00fbbbb82509c61e43b3b01be9c68273498ab716，分支 codex/rag。本项验收功能契约，答案质量与数值门槛继续由 R23/R24 验收。

## 实现与审查

管理适配器编排既有 Catalog、ingestion、recovery、model_switch 和 citations：创建/选择、导入/来源、同文档更新、删除、失败重试、批次恢复/放弃、模型 confirm/retry/keep_original、历史引用回看。新增库/发布版本/来源查询，无 schema 变更。初次 retry 不充当 confirm；重复 confirm 不重试，保留原版沿冻结模型继续使用。没有第二套 Agent 或独立产品界面。

后台线程保留真实 mutation owner、GPU handle 与 backend，重复取消仍等待真实完成；发布先于取消时保留回执和 operation_status。Remote 由发起连接拥有任务，旁观连接断开不取消；权限在取消时拒绝并关闭已有弹窗，command_done 在收束后发出，完成次数沿原 LoopComplete。会话只保存库/最近运行 ID，旧元数据约定保持。

知识 CLI 完成、部分/无证据、等待选择、失败、取消分别返回 0、2、3、1、130。已进入 Agent 的失败携带实际持久化 run_id/status/stop_reason；启动配置错误没有伪造 run，私密输入不进入诊断。正文与退出状态均由实际 main 子进程核对。

## 退修与最终测试

Leader 发现已选 ask/report/continue 的斜杠正文被第二次分派为命令。实际 -p "/knowledge ask /knowledge status" 输出管理 completed、退出0，却没有 run；两条真实 WebSocket HTTP 测试未进入 Agent 而超时。有效失败与两次探针收集错误分别保留。修订去掉 CLI 递归分派，Remote 复用已有 _knowledge 标记，仅改两个生产模块。

最终独立 **71 passed / 0 failed / 0 skipped**：新增实际 main 子进程6项与 localhost WS6项，覆盖三种研究动作和两类斜杠正文；核原问题、run/版本、报告、父关系和会话。既有权限、取消、旁观连接、TUI、普通命令/入口通过。原失败输入另独立复跑：CLI PASS，WS **2 passed**。六类 CLI 探针同时满足 OS exit、全部断言后的 PASS 标记与数据库终态，失败场景没有用 exit=1 掩盖断言失败。

修订前独立命令/模式/会话151、核心生命周期83、报告/接入138，共 **372 passed**。这些数字与最终71是不同候选树结果，不相加。逐模块核对证明 RAG 与其他宿主未变；原普通正文 GPU/Milvus/付费链仍适用，没有无变化重跑。

## 包与真实链路

最终 host wheel：8029589bd17f6a52b70f352b0c92428ef693d629e2ce3786d9345928831b3ccb。
RAG wheel：e53c92bef0fe9523e22cfa93bfca7fc6f3bd4756ee12dd9eff5cb43a51ec8da2。

Leader 独立重建 wheel/sdist、安装 core/cuda、pip check，143份宿主和74份RAG生产资源逐字节相等；测试生产模块全部来自私有 site-packages。RAG wheel 与退修前完全相同，固定回答 tokenizer 未变。

独立私有 GPU/Milvus 和原批准 DeepSeek 完成新建两库、中文路径/部分失败、变更输入拒绝与明确恢复/放弃/重试、问答、报告、reimport、模型确认重建、Remote续研、删除后历史引用与跨库拒绝。

| 真实运行 | run_id | 状态 | search / open | tokens |
| --- | --- | --- | --- | --- |
| CLI 问答 | 4bfc08ca-ea41-46a4-89e3-4fc746ab4ab0 | completed/finished | 2 / 0 | 4329 |
| CLI 报告 | b8e7652d-098d-42a3-a49e-9604ce129486 | completed/finished，saved | 2 / 0 | 4970 |
| 更新后 Remote 续研 | 3c6a4b01-9978-4bdf-9b1b-66a0c5b98c5f | completed/finished | 2 / 0 | 5800 |

Leader三轮15099 tokens，执行者三轮14747 tokens；六轮已知合计29846。blue/harbor 与修订后 red/mountain、引用版本、父关系、模型账本、pin释放和删除后历史保留均独立核对。报告实际文件2580bytes、SHA256 6b6b344b1a00046bfc18f1e66dc190c870aa5a23fb026990f7d29f922c0d36cf，与逻辑 Markdown SHA 分开。审计使用不可变只读 SQLite 和归档字节，未构造 Catalog，数据库及 sidecar 全部字节未变。

## 保留边界与交付

六轮均没有模型 knowledge_open 调用。用户 /knowledge open 历史引用命令另有真实通过证据。Leader Remote 却写“两份来源已在本轮实际打开核对”，报告也略扩写 registry checked；这些原文是 R23 指令遵循/过程真实性/语义支持样本，程序引用有效不能替代质量验收。不强制 open、不改提示、不扩预算、不为取得通过重跑。

原执行者524份证据与修订 manifest 的945份索引已逐项核对；执行期6537继承路径全部冻结；Leader按授权推进其中的阶段台账，其余6536路径、根双语 README、11旧SQL及R20原记录保持。Leader自有运行结束，容器停止、卷保留，清理由用户另派；未push/发布。完整命令、包/模块SHA、失败与适用性见 [独立验证](R21-leader-validation.json)。本次一并提交已核过的R20真实SHA后记；R21实际SHA在提交后另记。
