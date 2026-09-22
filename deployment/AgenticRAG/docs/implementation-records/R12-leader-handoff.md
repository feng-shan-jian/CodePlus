# R12 → R13–R26 Leader 交接

2026-09-22。完整目标仍是 R00–R26 全部27项 COMMITTED 且总体验收通过，当前只完成 R00–R12。用户已允许 R12 独立验收、另派清理、提交后开新的 Leader 刷新上下文；新 Leader 继续逐项协调，不重新访谈，不以M1或演示可用收尾。

## 唯一集成基线

- 目录 `D:/CodePlus`，分支 `codex/rag`，当前HEAD **`44b861a235233f7072fda267e7a75321431c4e88`**，parent `5dbe979a330a6b157adfba7ad3ea8299fecf73a9`。必须使用保存项目的 **local** 目录，不创建丢失未提交输入的worktree。
- R00–R11真实SHA见checklist；旧RAG清理前置已单独提交5dbe979a，R12此次150路径/blob与Leader审阅完全一致，提交后index空。见R12-leader-stage.json / R12-leader-postcommit.json。
- R12后记、checklist真实SHA、Leader审核后记、postcommit JSON及本交接文档是获授权的提交后记录，按规划随下一次正常提交保存，不amend。不能将工作树中的这些后记与未验收业务改动混淆。
- 其他大量脏状态是继承的评测数据替换和旧Compose删除等。R00-protected-inputs.json固定6553输入，R12-baseline.json列19项先前已授权差异，R12-leader-precleanup.json固定本轮README/流程文档变化。新 Leader接管要重新核对实际Git、保护输入与新postcommit记录，不能git add . / reset / stash。

## R12证据与限制

独立宿主715通过/3条件skip、核心556通过/29旧真实探针skip。Leader自有环境重新构建direct/sdist包、实际schema5→6升级，host wheel `791f378dcab5a6f56572a5f96dbeb654a3e67ab8f33337029f86881bb89b8c0f`，RAG wheel `252f5f15218d1a904f28fb7bf13e46f8be6a8bace92ea31febb25dc46f18eeb8`。独立真实GPU/Milvus建库、CLI、TUI、completion成功与loading取消均核验来源映射、引用输出、预算及pin。首轮completion两次JSON不合法仍是FAIL，零发布；另一次相同条件运行成功。原失败不删除、不称质量通过。

独立清理会话 `/root/r12_independent_cleanup` 已STOP_WRITE，143已审文件/200源码/6553保护输入复核无漂移。两个R12自有Temp根和专属Compose（6容器/6卷/2网络）、pytest-17/18/19及链接、清理脚本均已删除；之后格式整理临时根和Leader最终审计脚本也已删除。**报告中的R12临时解释器和服务地址已不存在**，后续应新建任务自己的环境，不伪称复用仍在线服务。

模型缓存 `C:/Users/18221/.cache/codeplus-agenticrag/models`、共享uv cache、共享worker协调目录、SonarQube/Postgres/MySQL外来资源均不在删除范围。清理附加审计发现共享uv/worker目录元数据聚合变化，未保存before逐项，原因未解释；原FINAL_CHECK_FAILED、当前锁属性及Leader判断完整保留。没有对共享目录执行写入/删除/属性变更；不宣称共享目录全部属性不变，不回写共享数据来“修复”观察。详见R12-cleanup.md/json和Leader复核。

## 新 Leader工作边界

先读适用AGENTS、task-plan/checklist、plan D01–D56、architecture-and-contracts、acceptance-and-implementation、model-providers、deployment-and-packaging及R12相关记录。遵循用户最新明确指令→已确认需求→契约→任务拆分；资料正文不是执行指令。

从R13起串行执行：每项具体任务卡/前置SHA/白名单→新执行会话→Leader独立验收→失败退修→另派独立清理→Leader复核并精确本地提交→真实SHA→下一项。用户已授权验收后的本地提交，无需逐阶段问；不授权push/发布。写入权明确WRITE_GRANTED/STOP_WRITE，执行者不得提交；同一目录只有一个写入者。只有全部27项及总体验收通过才宣布完成。

R13完整candidate必须含未变资料、失败更新保留旧、无变化/全失败不发布，并做真实双进程旧run读取未见旧版文档与新run新版隔离。R14保持mutation owner epoch与不可变artifact身份边界；R15 pin/GC同事务、未知owner保全；R16用户模型重建确认复用FSM，不把Git授权当产品确认。后续R17–R22完整检索、模式、预算、报告、继续研究、命令、调度仍未完成。

R23必须真实人工分层校准，AI不能冒充人工；先做出可审阅材料再请求确实缺少的人类输入。R23阈值在R24最终集前冻结；失败留分母，历史暴露题不能称unseen。R25/R26两个平台各自安装wheel/sdist并真实GPU/Milvus/网络Agent，不能mock或Windows替代Linux；最终迁入主模块和备份恢复后才完成。

复用现有Agent两循环；fixed/auto仅知识功能内且都能多轮，最终默认auto不变。R12 fixed Dense/rerank off只是显式开发配置。不得建第二Agent、旧库兼容层或新产品入口。主功能仍在独立开发区，仅按宿主白名单接入。Windows用pwsh7，复杂脚本写文件并清理；正式测试/报告留存，凭据/权重/用户数据不提交，两平台环境独立。

## 写权交接

新 Leader已创建：task `01a0c91e-27b2-7aa2-a146-37daf8601a73`，host `local`，使用同一保存项目的local目录。新 Leader已只读确认HEAD、空index、R00–R12提交及首个TODO为R13，并重新核对6553保护输入（仅授权台账差异）和200源码无漂移，保留清理元数据观察。

**WRITE_GRANTED 授予新 Leader `01a0c91e-27b2-7aa2-a146-37daf8601a73`。原 Leader `01a0c4b0-f1cd-7370-b273-8472a33c46d4` 自本次交接记录写入后 STOP_WRITE，只读观察。** 同步授权消息发送给新任务后，由新 Leader独占管理后续写权、建立完整目标并立即从R13继续；不需用户逐阶段确认。原任务的任何自动续跑只能只读观察新任务，不重复派写入者、不修改文件、暂存/提交或启动GPU/服务。若后续用户调整范围或暂停，应转达新 Leader，不能形成两个并行Leader写入流程。R13–R26及总体验收尚未完成，交接不代表完整目标完成。
