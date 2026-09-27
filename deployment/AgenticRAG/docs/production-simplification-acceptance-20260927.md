# RAG精简验收

本次按[执行方案](production-simplification-plan-20260927.md)完成实现、范围review及旧资料清理。development807和独立test300检索实测完成；真实回答质量验收因供应商HTTP402阻塞。机器可读结果见[冻结实测数据](production-simplification-results-20260927.json)。

## 实现与review

- 知识工具加入普通 registry；退出、异常和取消恢复同名工具及启用/发现状态。CLI/TUI/Remote保留普通Agent会话、记忆、权限、文件历史、协作和compact。
- 删除探索/收尾/强制答案JSON/引用修复控制、跨调用预算和主动历史裁切。报告使用普通WriteFile，继续入口读取公开历史后交回普通Agent。
- 来源保留单次返回限制、分页、去重、版本与原文坐标、实际交付。历史citation_id及新confirmed evidence_id均可通过知识命令读取。
- review退回并删除新增配置字段提议、续研报告路径专属拒绝分支、已无生产调用的CitationRegistry/footnote及其旧协议用例。没有新增业务表、检索路线、排序算法或答案审批流程。
- 最终review移除知识绑定强制禁用SDK重试和重定向的残留。沿用父客户端设置，每次实际HTTP请求独立记录；503/307不确认来源，成功流完成后才确认。

当前项目和用户配置未设置`knowledge_development_config`。验收从原SciFact知识库配置出发，经`assemble_configuration`合并[选定参数](retrieval-selected.json)，使用独立配置文件；没有重建索引或把评测库设为用户默认库。旧配置与冻结快照读取保持兼容。

## 验证边界

| 验证 | 结果 |
| --- | --- |
| 清理后AgenticRAG全模块回归（最终SDK修正前） | 728 passed，19 skipped，1 failed；一次Remote用例建库夹具的SQLite锁等待失败 |
| 上述失败定向复核 | 原参数1 passed；Remote整文件13 passed，未修改生产或测试 |
| 最终SDK修正：请求交付、知识宿主、Remote | 42 passed；覆盖三协议503重试及307重定向真实来源回执 |
| 普通Agent/context/commands/permissions/subagent | 276 passed，1 skipped |
| 宿主、命令、Textual、实际WebSocket专项 | 80 passed；与全模块有重叠，不重复累计 |
| 报告写入权限、继续、裁切后重复检索 | 7 passed；与全模块有重叠 |
| SciFact正式评分和隔离测试 | 27 passed |
| wheel与sdist生成wheel，两套干净环境安装 | 1 passed，覆盖两种安装路径 |
| 实际CLI `/knowledge status` | 退出0，读取原已发布revision；编码和索引fingerprint均为CURRENT |
| P01受保护文件 | 开始、清理前和最终复核5,826个SHA256全部匹配 |

Textual、WebSocket、宿主循环测试运行实际入口组件、权限、文件、SQLite及来源映射，模型和索引响应受控；不作为真实模型答案质量。GPU检索另列下节。

全量失败时数据库仍处于fixture发布阶段，`runs/host_runs/delivery_receipts`均为零。该路径使用既有1,500ms SQLite等待上限；原记录缺少持锁者，无法确定争用线程。相关存储、索引和publication fixture没有本次修改。定向未复现，保留全量失败记录，不增加生产重试或放宽断言。

2026-09-27本轮实时回答接口预检返回HTTP402，错误为`Insufficient Balance`。已停止付费请求调度。真实供应商问答、重复检索决策、长会话、报告、继续及答案/来源语义评分为**blocked**，未用受控回答代替质量验收。

## 冻结检索实测

保留5,183篇文档、5,844块和原发布版本。运行侧仅加载query与语料，离线评分读取Gold。development为807题，句子完整证据层为505题；独立test为300题。5/10是计分窗口，实际SourceSession仍使用原单次8块、8,000计量单位上限；正文计量保持原UTF-8字节上界。

运行源码SHA256：`c5bfc4f0bd9c191b90769b532f481f8d284f9e6f04df5b2fc69bd41c8bccb960`。当前实现实际运行完整双路召回、RRF10、24候选精排及0.001过滤；同时记录SourceSession正文选择。两遍运行另用生产search对照首题，避免将离线重排冒充线上调用。

development全量结果如下，召回、重排、正文选择均为0错误：

| 阶段 | 文档Recall@5 | 文档Recall@10 | 完整证据@5（505题） | 完整证据@10（505题） |
| --- | ---: | ---: | ---: | ---: |
| 融合RRF24 | 79.46% | 86.35% | 93.68% | 97.46% |
| 阈值过滤后精排 | 83.14% | 89.38% | 96.67% | 98.35% |
| 单次实际正文 | 80.23% | 80.23% | 94.31% | 94.31% |

精排Top5/Top10相对原选定RRF10，807题相关文档命中和505题完整证据命中均为逐题零新增、零丢失。正文实际返回2–5块；相对精排Top5，正文少覆盖30条文档标注、19条完整证据标注。相对Top10，少覆盖99条文档标注、36条完整证据标注。旧对照只提供排名结果，本轮另测正文；本次保持原单次限制，没有据此调参。

首次development运行完成807题召回和重排后，因进程启动时未加载本地CodePlus宿主路径，在正文阶段导入失败。恢复前修正正式runner缺少的两项`RankedSearch.catalog/run_id`绑定，仅重放完整冻结候选的正文选择并真实执行首题production search。新结果位于本地`.state/baseline-20260927/retrieval/production-20260927-recovered/`；原失败目录和completion保持不变，复制输入SHA256一致。恢复后807题正文成功，首题直接生产调用匹配。

原runner SHA256为`c161c5aa71030322c3b3b3a0bb07dde71cf2cdcfb689af7681d7c17d6675d315`，修正后为`17baf413a9b628cb3f1e616d2396a21b6396012569e5bc3e45a90b9e1057b7e0`。生产源码未变；独立test使用修正后runner完整执行一次，召回、重排、正文均为0错误，首题生产search匹配。

| test300阶段 | 文档Recall@5 | 文档Recall@10 |
| --- | ---: | ---: |
| 融合RRF24 | 78.60% | 86.44% |
| 阈值过滤后精排 | 82.94% | 89.49% |
| 单次实际正文 | 79.82% | 79.82% |

test未参与选参；本轮未为test执行句子完整证据评分。development原召回/重排耗时1,067.77s/1,172.01s，恢复正文590.94s；test召回/重排418.27s/414.70s，全流程1,005.28s。

## 清理与提交范围

D01–D09及D12已按原逐文件清单删除5,102个旧文件，D04仍有效的旧快照已迁至正式fixture。D10的146个旧评测文件已删除。D11的67,494个旧文件及本轮产生的临时索引文件已清理；新旧development完成记录、恢复轨迹和test结果保留在本地retrieval及runs目录。

清理前确认无运行中的评测进程、run、active pin或pending reader。仅删除所属SciFact集合（ID `469284418260376836`，5,844行）；另外13个Milvus集合ID、行数均保持不变。旧data、environment、worker目录已删除；共享模型缓存、Docker容器及命名卷保留。

被自动审批拦截删除的验证环境、测试目录和外部旧产物迁入`D:\CodePlus\需删除`。外部审计核对了进程命令行、Docker挂载与注册工作树；共享模型、根环境、用户会话、记忆、文件历史、Docker容器和命名卷不进入清理范围。三份内容不同的历史备份只迁移保留。

外部首次迁移27项成功、45个目录部分中断；后续补迁820,909个真实文件、51.41GB并核对SHA256，7个大小写冲突文件分别保存。全部72个原路径的真实文件残留为0。5,572个符号链接对象仍留在原处：Windows返回1314，缺少跨卷创建符号链接权限；原路径、目标和属性保存在`需删除/external-rag-20260927/metadata/remaining-links.json`，未将元数据保存计作对象迁移。

任务开始已有的`eval/RAG-eval`大批数据重排和其他任务改动保留；本次不把它们整体纳入提交。当前MultiHop评测沿用该本地数据状态，不能把本次局部提交单独称为完整的数据迁移交付。

SciFact提交正式入口、四份上游原始文件、三份句子标注文件和两份数据身份记录；生成的runtime和结果留在本地。历史快照和标注文件关闭Git换行转换，暂存的9份P01输入均与原清单SHA256一致。原清单SHA256为`8e6cab532271148c52d9a80572522aa0a20f12bef5113be2178f796faec0b4f4`，本机逐文件清单与外部资源明细不加入源码提交。

工作期间其他会话已提交compose位置调整`df48699`和min_score实现`47a1045`；本次基于这两项继续精简，不重复提交。普通MultiHop历史文档仅暂存失效链接和当前接入状态修正，其余继承重写保留在工作区。

最终暂存审核剥离了继承的dense_runner会话轮换、候选参数、idle timeout及对应测试，工作区内容保留。8份未改动的补充/续研题目和rubric也保持原本地状态，不新增入Git。继承的计分绑定随D07迁至正式retrieval_binding入口，两个scorer同步保留可变分母，避免删除旧文件后计分入口失效；打包清单同步列入HEAD已有的research/management模块。这两项作为清理和打包验收的必要依赖纳入提交。测试结果对应保留继承改动的工作区，未把它们声称为独立检出的完整验收。
