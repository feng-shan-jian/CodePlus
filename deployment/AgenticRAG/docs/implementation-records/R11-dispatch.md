# R11 分派卡

R11 原文阅读、交付证据与引用核心。前置R00–R10均COMMITTED，唯一集成基线 codex/rag @ 81bf1461c73d9d640144eb1ed8b9a193d0f4f953。执行会话 /root/r11_sources 已实际创建，初始化只读；Leader登记台账后发送显式WRITE_GRANTED，随后此会话成为本项唯一写入者。仓库D:/CodePlus，独立deployment/AgenticRAG，pwsh7，无stage/commit/push，无主模块/根依赖/旧RAG/数据/spec需求修改。读全部适用AGENTS、taskplan流程/R11/R12/R15/R18/R19、checklist通用/R11、plan D20/D26–D28/D31、architecture/acceptance T05–T07、host-integration-contract交付sidecar/confirmed gate及R08/R10实际证据。
允许独立sources/evidence/citations或同等模块、storage必要追加迁移/接口、retrieval Dense来源接点、正式tests/doc。复用SourceRef/Evidence/Citation/Run/RunLease/ArchiveStore，保持历史SQL和快照身份兼容，不另造Agent/产品入口，不把测试confirmed回调写成真实HTTP交付。Leader和只读评审无并行写/测试/GPU；完成先交权再独立验收。
读取只用固定run revision成员+canonical/ChunkSet/SourceMap归档。default matched section own-span，与Heading parent/subtree导航区分；无标题root、纯标题节点、空文件、长章节分页、其他章节导航和逐步全文有精确CP returned_spans/行号/未读标记。正文不得靠current source path/标题重找。SourceRef 5UUID只是内部DTO，模型-facing source_ref/cursor应服务端签发不透明、绑定run/kb/revision/docversion/section/next CP；任意路径/伪造/跨run或版本/terminal/bounds/section+cursor冲突拒绝，invalid不得当EOF。
候选正文先pending；只有可信confirmed最终交付回执对应实际body span/hash才能激活Evidence。目录/导航/内部rank候选/UI/history/落盘路径/compact摘要/未送达全文不授资格。subset只激活subset；未知候选/不匹配绑定/hash/前四状态prepared/not_sent/rejected/unknown不激活。duplicate回执幂等不扩大范围不重复计费；历史已送达与当前window retained分别处理。不用纯字符串find反推映射或资格。
引用核验evidence来源/run/revision/version/范围+精确直接摘录；保存稳定可回看的脚注/元数据。不存在未交付空隙的连续引用；多span顺序、连接符、省略号不冒充原文。错位置重复文本、NFC变换、篡改/未知ID明确失败；无需在线额外语义LLM。source删除/改写和Milvus不可用时仍可凭归档回看；archive缺失/损坏明确失败；历史引用不能给新run授权。实际R15 GC竞争及R12 Agent修正文流未实现不宣称通过。
搜索/open共用次数/片段数/token约束，受理失败/重试计数；正文+必要metadata/citation wrapper计量；跨调用不重置。Token meter需由host回答模型注入真实或有保证上界，不用R09 embedding tokenizer/字符除4冒充。R11测试可用显式受控meter验证算法；R12 completepayload HTTP硬门另实证。Context8/8000只当前实验配置，官方eval TopK10独立；不要提前冻结R23阈值。
独立验收矩阵按R11-readonly准备：层级A/B/C/D；TXT/前言/纯标题/空；BOMCRLFemoji分解重音中文重复文，强制多页覆盖；所有scope/cursor拒绝；固定旧revision阅读；pending100:200仅120:145confirmed；目录/compact无资格；各回执状态+duplicate；exactquote字符变更/NFC/同文错位置；0:4+8:12跨gap2:10拒绝；source删除与archive损坏；搜索→usage登记→open/第二搜索同绑定有效。
交付R11.md含G/C和5条专属证据、所有命令cwd/env退出码/hash、正式tests/报告与精确filemanifest、临时清理；新迁移真实旧R10安装产生DB升级验证；core/cleanwheel+sdist安装保持optional deps，不伪造Agent实证。必要实际R10已发布数据读取可用独占fixture；若需GPU/Milvus先说明序列安排。结束明确停写交权，Leader独立验收后精确本地提交。

前置R10实际验收：Leader326核心+11真实Milvus/多进程+4资源盘点增量；609篇→4041chunks完整生产GPU构建，200查询0错误，真实重启同run/原文一致；185精确文件本地提交，无push。已清理执行者及Leader临时数据和专属服务；没有可复用的旧609数据库，正式fixtures/模型缓存/独立环境保留。最终运行代码与测试参考 docs/implementation-records/R10-leader-code-final.json；不要直接假设早期candidate文件仍覆盖最终资源YAML末尾空行。

环境：core C:/Users/18221/.cache/codeplus-agenticrag/venv-win-core（Python3.14.3、editable、无Torch）；CUDA同父venv-win-cuda（已安装R10 wheel b6cf8f415dbac495ec46811c9b75ea2fba08556fa25431fe472ebd23520e3909，45包文件核对）；models同父缓存。Windows pwsh7.6.5；SQL1–4历史文件不得改字节，必要新增migration5并验证真实旧R10 wheel创建的数据，不将R08/R09历史测试删掉。变更包内容必须重新安装并核对实际worker实现摘要，不能用旧驻留包冒充当前代码。

运行取证边界进一步强调：confirmed来自可信适配层能力，模型不能靠传回JSON或evidence_id自签receipt；purpose=compact不能增加本次答复的证据资格，旧run/history只作线索，新run registry必须从空开始。同一run历史已confirmed证据可在compact之后继续核验，当前窗口留存单独记。按host-integration-contract的source codepoint、JSON node body codepoint、raw HTTP body hash分别保存，不能混用或用find反推。

R10两个独立建库分数/排序并非完全一致（已记录R10-leader-repeatability.json），本任务不顺带优化检索质量。默认auto、模型版本和确认策略保持spec；core测试的token meter须明确是受控fixture。真实chat模型与provider上界问题留R12以真实环境验收，不假称R11完成网络交付。普通产品入口、root依赖、宿主Agent均不在本项写入白名单。

先读取实际Git/工作区、所有适用AGENTS和以上spec。允许常规实现选择自主记录，不重复需求访谈。收到写权后先简要回报模块/持久化与调用关系方案、风险及预期真实证据，再在该明确任务范围推进；如需改已确认需求或宿主接点，停止相关扩展向Leader报告。执行者不得自行stage/commit/push或勾Leader ACCEPTED。结束交精确文件及hash、实际命令/环境/退出码/失败修复记录、R11.md与正式报告，清理自有临时产物后明确STOP_WRITE。Leader当前台账/R10真实SHA后记/R11分派与只读矩阵属于Leader登记材料，不由执行者覆写。
