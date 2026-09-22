# R13 Leader 独立验收

状态：ACCEPTED，全部必要代码、独立运行、独立清理及 Leader 清理复核已通过；共享目录元数据观察按实际证据保留。执行者 `/root/r13_mutations` 已明确 STOP_WRITE，Leader `01a0c91e-27b2-7aa2-a146-37daf8601a73` 持有唯一写权限。未暂存、提交或推送。R14–R26 和总体验收仍未完成。

## 冻结与实际补丁

唯一目录 `D:/CodePlus`，分支 `codex/rag`，前置提交 `44b861a235233f7072fda267e7a75321431c4e88`。Leader 独立核对执行者 70 份交付文件、130 份源码/测试/打包输入和 6553 项保护输入；10 份既有 RAG 生产文件的差异均逐文件匹配授权范围，143 份宿主源码、R12 五份后记和既有 6 份 SQL 未变，index 为空。保护输入唯一差异是本项授权的独立 README。完整核查见 [R13-leader-freeze.json](R13-leader-freeze.json)。

交付清单 SHA256 为 `23b47ded5137137dcd3eb5020b92fdeaddad496d3c80197779dff9a42b418c84`，冻结输入指纹为 `365df50e99520acfc88eb1b6d8512d37f0ac18f326610fc0f154233ab05aa292`。Leader 已读全部生产补丁、新增普通修改协调器与 migration 7、正式行为测试、真实驱动、升级驱动和使用说明。

重点核对完整候选及全批终结、整个文档编码成功边界、失败更新保留旧成员、向量全行/float32 摘要校验、空版本、取消与库级故障不部分发布、正常无变化终态及释放、显式移动来源身份延迟提交、失败项重试的配置/原件核对和后续发布/ABA 防覆盖。早期发现的目录重试扩大范围、后段编码失败、来源变更与索引变更组合、直接无变化完成守卫、独立模块导入和门闩原子写入均已在最终代码与正式用例中关闭。

## Leader 运行证据

所有临时环境、脚本和数据属于 `C:/Users/18221/AppData/Local/Temp/codeplus-r13-leader-20260922`，使用自有 uv cache 与 copy 模式。共享 CUDA 参考环境以独立字节复制，复制/安装前后 22380 项源目录元数据一致；没有更改共享依赖。实际导入为 Leader 的 site-packages，模型权重只读。

| 检查 | 独立结果与证据 |
| --- | --- |
| 重新构建、安装、依赖及 GPU | PASS；[环境记录](R13-leader-setup.json)。host wheel `1ac2f7c8ca498bfe749f1458b990557f8cc98024f5b8509a1f30b5f1c187ebe5`，RAG wheel `d8967e612116849884497423763af4c29792d4c0948a19641146b47beb9ba8da`，sdist `3d6ec5424a710f50031efeaa5bd3fdbbca794567d7194c2adc91e1cbb1694425`，均与执行者最终包一致；两环境安装的全部 RAG 源码/SQL/JSON 与冻结输入一致，pip check 通过 |
| 独立服务 | PASS；专属 Compose `codeplus-r13-leader-20260922`，19537/9098，3 个健康容器；[启动与资源记录](R13-leader-infra.json) |
| 宿主回归 | PASS：715 passed / 3 skipped；[XML](R13-leader-host.xml)、[命令](R13-leader-host-command.json)、[运行前后安装路径](R13-leader-host-modules.json) |
| 核心完整回归及两路干净安装 | PASS：587 passed / 29 skipped，1163.59 秒，含全部 31 个 R13 用例；[XML](R13-leader-core.xml)、[命令](R13-leader-core-command.json)、[运行前后模块](R13-leader-core-modules.json)、[两路安装](R13-leader-core-installations.json)。direct/rebuilt wheel 与上列哈希相同 |
| 独立全语料处理 | PASS：609 文件、4041 Chunk、0 失败，386.09 秒；[完整报告](R13-leader-core-corpus.json)。原件、解析、结构、码点覆盖与完整模型输入 token 检查通过 |
| 真实 GPU/Milvus 双进程 | PASS；[真实报告](R13-leader-real.json)、[命令](R13-leader-real-command.json)及[原始结果与实际数据库交叉核对](R13-leader-runtime-verification.json) |
| 真实 v6→v7 升级 | PASS；[报告](R13-leader-upgrade.json)、[命令](R13-leader-upgrade-command.json)。由旧提交实际构建/安装 v6 包并创建归档和解析检查点，删除外部源文件后换装新 wheel；全部原行摘要、检查点、版本、解析文本与旧 6 份 migration 指纹保持，integrity/FK 通过 |
| 独立清理及 Leader 复核 | PASS；[独立清理](R13-cleanup.md) 与 [Leader 清理复核](R13-leader-postcleanup.json)。代码未变，无需重跑已通过的运行验收；精确本地提交另记 |

独立真实运行的写进程 PID 为 26212，读进程 PID 为 66384。读进程于本地 `22:06:33.381738` 只固定 V1；写进程在 `22:06:46.612772` 发布 V2，读进程于 `22:06:46.621484` 观察门闩后才创建检索/来源对象并首次搜索和打开旧文档。进程出生标识、解释器及安装路径均保存于报告。Leader 另以 SQLite 只读连接核对检索返回的完整文档/版本集合：V1、V2 各 4 成员，更新项版本不同、失败更新与未变项版本相同，显式删除项仅在 V1，新增项仅在 V2。

Dense、native BM25 和原文范围与对应运行版一致。V2 编码 2、复用 2，全部无变化/全失败正常结束；随后仅重试一个失败项，再删除全部成员形成真实零行版本。实际数据库仅 4 次发布、无 pending library，3 个 pin 全部 released，所有真实请求句柄已完成；Collection 集与登记 artifact 精确相等。它们是版本、状态与真实服务路径证据，不是回答质量或性能门槛通过结论。

## 失败记录与后续边界

执行者所有早期失败保留，尤其 `R13-core-final.txt` 的 19 failed / 562 passed / 30 skipped 不能当成最终结果。16 项与首次开放范围安装取得的新版宿主 SDK 有关，另 3 项为旧 schema 断言；最终恢复 R12 哈希锁并更新受影响断言。合并 pytest 的 importlib 收集失败、uv 参数错误和保护审计脚本误分类也均保留，见 [R13 执行记录](R13.md)。没有为环境漂移改动宿主生产代码。R25 仍须真实检查最终干净安装的依赖范围，不把本次锁定环境等同于任意未来 SDK 组合兼容。

R13 仅完成普通文档修改生命周期，不提前宣告 R14 完整恢复/放弃、R15 自动 GC 或 R16 模型切换已完成。条件跳过、受控模型/服务替身和真实 GPU/服务运行分别记录。R12 共享缓存/worker 聚合元数据的未解释观察继续保留，本项不改写其历史结论。

Leader 清理前首轮审计还出现一次工具脚本断言失败，原输出保留在 `R13-leader-precleanup.txt`。只读差异诊断确认内容差异为空：执行者的 411 个 Git ignored 条目包含 408 个文件和 3 个既有 worktree 目录（文件 hash 为 null），Leader 最初只枚举普通文件导致数量不一致。修正为同一 Git 条目集合后复核，不删除这些目录，不将目录条目当作本轮临时文件。原诊断见 [ignored 核查](R13-leader-ignored-diagnostic.json)，最终统一口径见清理资源清单。

## 逐项独立结论

| 条目 | Leader 结论 |
| --- | --- |
| R13.1 | PASS：正式混合批次与真实 V1/V2 完整成员核对，成功更新/新增/显式删除生效，失败更新保留原版本，失败新增不可见 |
| R13.2 | PASS：所有文件终结后单次发布；受控取消/服务/发布故障保旧指针，无变化/全失败不新增 revision/Collection/publication 并解除占用 |
| R13.3 | PASS：完整候选、旧 artifact/源归档/成员/行/编码指纹和 float32 摘要认证，真实 V2 编码 2、复用 2；空版本有真实服务探针 |
| R13.4 | PASS：两个独立进程固定不同版本，A 在 V2 发布后首次检索/打开未见旧文档；Dense、原文、native BM25 与 SQLite 只读完整集合交叉核对一致 |
| R13.5 | PASS：完成与未发布汇总、失败项重试、配置/字节核对、后续版本/ABA/目录范围防覆盖；不兼容编码在持久注册前拒绝，严格首次评测建库保持全成功要求 |
| G01–G08、G10 | PASS：实际补丁、新文件、调用兼容、追加迁移、真实安装/模型/服务/双进程、完整命令和数据指纹、失败记录及边界均已核对 |
| G09、C01 | PASS：独立清理及 Leader 实际资源/文件复核通过，最终结论 ACCEPTED |
| C02–C05 | PENDING：即将精确暂存、本地提交并核对实际提交后记录 |

Leader 共 1302 项通过、32 项条件跳过，逐项 skip 原因、31 个 R13 用例、全语料及两路安装核验见 [最终验证汇总](R13-leader-validation.json)。没有跳过 R13 必需项。当前已 ACCEPTED，尚未 COMMITTED，下一步仅精确本地提交和提交后核对。

## 独立清理及最终批准

清理会话 /root/r13_cleanup 已明确 STOP_WRITE，Leader 收回唯一写权限。两个临时根、自身辅助根、登记 6 容器/6 卷/2 网络均消失，四端口无监听，未终止进程；外来容器/卷/网络稳定字段保留。Leader 独立重新核对 202 生产文件、107 审阅文件、6553 保护输入、既有 SQL 和 411 ignored 条目，无差异。清理未修改生产代码、正式测试或原始验收报告，也未修改 .gitattributes。

共享逐项记录共 172576 项，6967 项差异全部为 uv cache 的目录 size，其他字段及普通文件元数据一致；模型、core/CUDA 和 worker 目录逐项元数据一致。两次使用相同脚本及 os.stat 调用，原因未确定，不能宣称全量元数据相等或内容哈希相等。pytest-2 首检即 absent，前后均 absent，不将 R12 历史存在当成本轮基线。三次清理脚本失败均保留：挂载 Name 字段检查发生在任何删除前，完整 mount 数组按对象排序后相等，自清理预检将既有分派文件的换行提示误当空白错误后已纠正，未修改原文件。

清理汇总 SHA256：d088dbb929644bb8acf99c8f204d9713a3f6b2147da6b320ff07d4e15110284f；Leader 复核 SHA256：846d06c4aff224bdf1553524582dff321f17a323df8186975ae0999024012b18。全部 R13 必需项无未完成项，批准本地提交；完整项目继续 R14–R26。
