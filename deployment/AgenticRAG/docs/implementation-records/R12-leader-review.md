# R12 Leader 独立验收

状态：**ACCEPTED，待精确本地提交**。执行者与独立清理会话均已 STOP_WRITE，Leader 接回唯一写入权并完成清理复核。R13–R26 及总体验收仍未完成。

## 冻结与范围

唯一目录 `D:/CodePlus`，分支 `codex/rag`，HEAD `5dbe979a330a6b157adfba7ad3ea8299fecf73a9`，接管时 index 空。Leader 独立逐字节核对 108 项交付文件、R12 基线输入、R00 的 6553 项保护输入及历史 SQL 1–5，未发现未授权漂移；`permission_dialog.py` 保持原哈希。见 [冻结报告](R12-leader-freeze.json)。

执行者交接文件哈希：

- R12.md：`a33e90afe1ad5a79e2dbc8f367ea93673d6014f3a95dfbea61778b1707a3d5c5`
- R12-delivery-manifest.json：`f3bd9f55baf2a89a4fa47bf744211309b43ad73874eac3f3605257029b0b0f42`
- R12-protection.json：`9f231e713297097cd34bcab25cc824cc148bc2388a6ee71d6194e1f0e9d2f985`
- R12-resources.json：`dabfe8bac609821692a3bbfdb27a7f481e7f44ebec5a8ec98038651c9d00387a`

Leader 另按用户追加流程更新 task-plan 的独立清理/新 Leader 交接步骤，以及 checklist 的 G09 和 R12 REVIEW 状态。这是流程文档变更，不改变已确认产品需求，也不回写此前任务的历史事实。

## 独立代码检查

Leader 已阅读实际宿主接点、适配策略、账本/计量、来源 sidecar、迁移与正式测试。`/root/r11_sources` 的独立只读评审不执行测试、不持有写权；其发现由 Leader 核实后退回执行者，在最终候选中关闭。

重点关闭项包括：最终 SDK HTTP 字节与来源映射的交集；错误/未知协议终态不得新增 Evidence；合法 length 只证明输入送达；实际费用与证据资格分开结算；未知用量保留预留；权限 future 与实际工具/线程/GPU 句柄的取消收束；compact 不添资格；严格 JSON shape、多 citation/多 span 与 marker 的完整预检；无效草稿零发布、保存前完成关联校验；最多一次同 Agent 修正。

真实安装失败进一步暴露的收尾缺陷也已修复并读实际补丁：按完整 tool-use/result 对逐个裁剪，避免删除同一 assistant 的整批原文；日志从真正配对/序列化映射得出；完全容纳不了原文时不发修正请求。同步引用校验和渲染之后再次检查总 deadline，再决定发布。受控测试的通过不代替下文 Leader 自己的真实链路。

## Leader 运行证据

Leader 自有临时根 `C:/Users/18221/AppData/Local/Temp/codeplus-r12-leader-20260922`，使用独立 CPU 和 CUDA 环境，`UV_LINK_MODE=copy`；无 PYTHONPATH 注入。临时验证脚本在独立清理阶段删除，正式 JSON/XML/TXT 报告保留。

| 检查 | 当前真实结果 |
| --- | --- |
| 冻结输入/交付核查 | PASS，108 文件、6553 保护输入、5 份历史 SQL；index 空 |
| 独立 CPU 环境 | PASS，实际 import 为 Leader 的 site-packages，无 Torch 导入；[安装记录](R12-leader-setup.json) |
| 两路重新构建/安装、真实 v5 升级 | PASS；[完整命令](R12-leader-packaging.json)。独立生成的 host wheel SHA `791f378dcab5a6f56572a5f96dbeb654a3e67ab8f33337029f86881bb89b8c0f`、RAG wheel SHA `252f5f15218d1a904f28fb7bf13e46f8be6a8bace92ea31febb25dc46f18eeb8`，直接和 sdist 路线均与 final5 完全相等 |
| 宿主首次回归 | 714 passed / 1 failed / 3 skipped；Leader 误用外部 cwd，既有 ReadFile 用例找不到其要求的仓库文件；见 [原失败](R12-leader-host.xml)，没有修改生产代码 |
| 宿主正确 cwd 重验 | 715 passed / 3 skipped；[结果](R12-leader-host-b.xml)、[解释器/模块路径/命令](R12-leader-host-b.json)。运行前后均为独立安装模块 |
| 核心全套回归 | 556 passed / 29 skipped / 0 failed/error，678.566s；[结果](R12-leader-core.xml)、[环境及模块路径](R12-leader-core.json)。29 个旧独立真实探针未启用，不计通过；运行前后均为 Leader site-packages |
| 独立 Milvus/CUDA | PASS：自有 Compose `codeplus-r12-leader-20260922`，19535/9096，真实 RTX 4070 Laptop、Torch 2.14.0+cu130、独立安装 core wheel；[资源与命令](R12-leader-infra.json) |
| Leader 自建版本 | PASS；[建库记录](R12-leader-real-build.json)，3 文档/3 chunks 经真实 CUDA 编码、Milvus 3.0.1 / client 3.0.2 全行/向量/索引检查并发布固定 revision `20832622-d608-4c93-9c3d-735c96e4a046` |
| Leader 真实 CLI | PASS；[运行](R12-leader-real-cli.json)、[独立输出核验](R12-leader-verify-cli.json)，run `68591c03-78e5-49be-8443-d2b8a7c76a02`，4 search / 2 成功 open / 4 confirmed 请求，15818 tokens；一次 repair 输入上界9198，实际保留5个来源映射；唯一NDJSON result与artifact逐字相同 |
| Leader 真实 TUI | PASS；[运行](R12-leader-real-tui.json)、[核验](R12-leader-verify-tui.json)，sdist重建环境实际Textual/Pilot use→ask→off，run `b962dd81-b0fa-4244-aaa9-1f8d2adcb693`，2 search / 2 open / 3 confirmed 请求，10073 tokens；首次校验通过、实际显示artifact、普通Agent/input恢复 |
| Leader completion 首次 | **FAIL，保留原结果**；[原报告](R12-leader-real-completion.json)，run `e45618ed-2a3d-41af-bf36-e57999c3cd7a`，首次及一次修正后均invalid_json；incomplete/citation_invalid，无artifact/stream_text，pin released。实际repair保留两次open及5个映射，输入9318，输出312未触cap。未保存草稿，不猜具体格式；代码与回执复核未发现确定实现缺陷。另核查saved_citations=0，见清理前诊断报告 |
| Leader completion 独立第二次 | PASS；[运行](R12-leader-real-completion-round2.json)、[核验](R12-leader-verify-completion-round2.json)，run `fb160dbc-6563-4050-b30e-a00c7bf46f26`，4 search / 2 成功 open / 3 confirmed 请求，12379 tokens；首次校验通过，返回值等于artifact。代码、prompt、配置未变；这是另一次独立运行，不改写首轮失败、不宣称模型质量达标 |
| Leader 真实加载中取消 | PASS；[运行](R12-leader-real-cancel.json)、[核验](R12-leader-verify-cancel.json)，run `d8d22eb4-9231-4d24-8634-03cbba095917`，loading且execution_finished=false时取消，CancelledError传播，先cancelled/pin active，真实worker_finished后released；无额外模型请求/正文/artifact |
| 独立清理 | `/root/r12_independent_cleanup` 完成授权临时目录、6容器/6卷/2网络及自身脚本清理；[报告](R12-cleanup.md)保留共享目录元数据观察。Leader 独立复核143审阅文件、200源码、6553保护输入、目录/端口消失及外来资源，见 [复核](R12-leader-postcleanup.json) |

执行者 final5 的真实结果已由 Leader 逐项读取：CLI 输出等于持久化 artifact；TUI 显示 artifact 并恢复 ordinary Agent/input；completion 一次修正保留 4 个实际正文映射、input_upper=11124，未扩大预算；loading 中取消先保 pin，再由真实 worker_finished 释放。它们仍属于执行者证据，独立运行另记。

所有成功入口均交叉核对成功open call ID与confirmed请求的实际正文映射，而不只看accepted计数。CLI、TUI、completion各使用新run，但固定同一库版本；所有终态均释放pin。Leader采用执行者已记录的显式开发试验context_chunks=16，其他预算不变，前后配置及唯一差异见 [试验记录](R12-leader-trial-config.json)。这些合成运行用于接缝验收，不是R23/R24质量通过证据。

## 逐项独立结论

| 条目 | Leader 结论与证据 |
| --- | --- |
| R12.1 | PASS；原Agent.run的真实CLI/TUI及原run_to_completion经同一核心多次搜索、补查、open和引用产物；独立包安装与实际模块路径均已核验 |
| R12.2 | PASS；开发配置fixed Dense / rerank=false，只提供search/open；未修改正式默认auto，后续能力未提前宣告 |
| R12.3 | PASS；实际SourceTool→SDK HTTP字节→gateway回执的正式受控覆盖及本次联网映射；3协议、裁剪、spill、compact、pair repair、Unicode受控验证；注入资料未扩大工具权限 |
| R12.4 | PASS；独立核心正式预算套件、最后HTTP deadline、unknown用量与合法终态分类；真实请求cap/usage及来源回执均无violation，收尾预留保持 |
| R12.5 | PASS；正式双循环覆盖首次通过/一次修正成功失败/预算不足/无效零发布；本次真实TUI及第二次completion首次通过，CLI一次修正成功，首次completion一次修正后明确失败，未过滤失败 |
| R12.6 | PASS；独立宿主715通过、核心556通过包含ordinary Agent/权限/两scope隔离；真实TUI恢复和取消完成。32个条件skip保持原界限 |
| G01–G08、G10 | PASS；前置与输入/源码/安装指纹固定，实际diff与风险检查、完整命令/退出码/受控与真实证据、失败处置及范围文档均已核对 |
| G09、C01 | PASS；独立清理与Leader实际复核完成，最终结论ACCEPTED；清理元数据观察保留如下，没有把额外断言改写为通过 |
| C02–C05 | 紧接执行精确暂存与最终补丁检查、本地提交及真实SHA交接；证据写入stage/postcommit记录，不提前声称已提交 |

## 清理复核结论与限制

Leader 读取实际删除命令与路径检查后，独立核对清理交付3份文件SHA、143审阅文件与200源码、6553输入保护、7个授权根/链接已不存在、4端口关闭、外来2容器/5卷/4网络仍在。清理仅增加两份正式报告和records/.gitattributes对一份原始失败日志的精确尾空白豁免，没有生产或测试改动，因此无需重复已通过的产品测试。

清理额外执行的“整个共享目录元数据完全一致”断言未通过：worker协调目录及uv cache条目数不变，但聚合摘要不同。删除前未保存逐项元数据，无法定位差异原因。两个共享锁当前均为原有mtime的0字节单链接，模型缓存与pytest-2摘要一致，所有删除目标均在授权范围，291个特殊文件的硬链接均不跨出授权根。没有共享目录写入/删除/属性命令。基于实际命令边界及保护输入证据，Leader接受G09精确清理完成；**不宣称共享目录全部属性一致，也不将未知原因解释为已证实的内容修改或删除**。原FINAL_CHECK_FAILED及完整观察保留在R12-cleanup.json。

## 剩余提交门

首次149路径暂存检查还发现5份原始失败XML含尾空白、Leader任务卡多一个末尾LF，退出2。再次交给独立清理会话，仅为5份XML追加精确属性例外并删除任务卡最后1字节LF，原报告及200源码不变，旧index149路径/blob不变，见 [格式整理记录](R12-final-format-cleanup.json)。Leader重新核对清理输出SHA后精确重暂存并重跑最终检查；不做reset、不修改原失败证据。该记录也保留Leader一次git write-tree审计命令可能刷新cache-tree的事实；清理实际before/after index字节及语义均相同。

功能、代码与授权清理复核已完成，下一步仅精确暂存和本地提交；不 push。R12 真正提交后记录真实SHA并准备新 Leader 接续 R13–R26，完整目标不变。
