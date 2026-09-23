# R20 Leader 独立验收

结论：**ACCEPTED，待精确本地提交**。验收对象是 R20 报告保存、中文查询与显式续研的功能契约。独立新四轮的“全部 completed”检查仍为 **FAIL**：前三轮完成，删除轮按已确认 D14 以 partial/search_limit 收尾。原检查、原始草稿和报告不改判、不覆盖，不追加同动作重试。基线为 `41802ffe9061856dc8d2ea1e4ef0fdbdad1d143a`，唯一集成目录 `D:/CodePlus`、分支 `codex/rag`。

## 功能与代码审查

报告由现有 Agent 的两条循环执行。整份引用校验通过后，宿主通过原 WriteFile 权限、读取缓存、覆盖和错误路径保存；写能力不进入知识模型工具表。文件结果使用实际回读字节的摘要/大小，Windows 换行与逻辑 Markdown 摘要分开；写成后取消或截止仍保留 saved 事实，整体状态如实保持未完成。

续研复用 runs 父关系与 host_runs JSON，新运行绑定当前发布版、新预算和新证据；历史目标、顺序用户约束及发现仅作待核线索。旧报告路径不可复用，删除来源不能从历史恢复为当前证据，逐轮与累计未知用量保持 null。没有新增 Agent、表或旧 SQL 修改。

最终退修使探索保留原生 tools 且不带 response_format；只有工具禁用的 finalize/citation_repair 使用原生 JSON。SDK 正文、预览和最终发送门完整等值计量，普通调用保持原样。收尾去重仅比较当前可见的同版本、同范围、同正文完整结果，且候选完整请求已经适配原窗口才采用；否则保持原来的淘汰顺序。没有扩大预算、强制 open、解析正文 DSML 或增加模型重试。

## 最终独立验证

直接受影响组 **125 passed / 0 failed / 0 skipped**：报告/分页/上下文33、窗口/JSON43、模式/预算49；JUnit 合计415.96秒，三个组并行运行。全部从最终私有 site-packages 加载，并核对模块字节与源码。前次独立的248项相关结果原样保留，不把重复测试相加。宿主 wheel 与此前独立验收逐字节相同，沿用 **148 passed / 1 Windows 条件 skipped**，未无变化重跑普通宿主。

独立重建 host/RAG wheel、sdist，wheel 与执行者一致；源码、wheel、core/cuda 的143/73份生产资源一致。直接 wheel 与 sdist 重建 wheel 的两个清洁安装、pip check、固定 tokenizer、SQLite 建库及 CPU 不加载 torch 均通过。最终 RAG wheel SHA256 为 `48aba508386ad290fc46e83a558012b4862e20db2b2a577b27aaefb1986258ac`；host 为 `f71acfb81fa33ddb9b5dab9b4316a8a427316afe4457162345a18c8c7b64e91b`。

## 独立真实四轮

私有 GPU/Milvus、原已批准 DeepSeek 配置和最终安装包；每个预定动作仅一次。首轮通过实际 CodePlusApp 的 Textual run_test 分派，CLI 为真实子进程；不宣称操作系统视觉验收。

| 轮次 | run_id | 实际状态 | search / 成功 open | tokens |
| --- | --- | --- | --- | --- |
| TUI 报告 | 83808a4c-b320-44b4-bddb-98ed3f4a74f2 | completed/finished，saved | 2 / 4 | 19380 |
| 实际 -p 同版继续 | 92464844-91b7-4d89-9ba1-0bb13ad74c20 | completed/finished，saved 新文件 | 3 / 6 | 41049 |
| 更新后继续 | 4e9f9911-ee10-4a94-8299-ac30bd6a30ed | completed/finished，saved 新文件 | 6 / 4 | 41605 |
| 删除后继续 | 10436be3-6138-4648-925f-5093f8cd7a52 | partial/search_limit，QA 无保存目标 | 10 / 1 | 22621 |

累计124655 tokens；每轮新证据、父链、三份绑定版本、预算/用量、实际文件字节、pin释放、旧报告和历史引用保全均独立核对。前三份报告的主要中文比较、英文引文、2023排除和更新后的 Atlas 夜间资格正确。前三轮首次公开回答被严格 JSON 门拒绝后，已有一次修正成功；实际 CLI 首稿未采集，不猜测其具体形态。删除轮 finalize 首稿合法，引用正确，只有当前 Beacon 正文，Atlas 三项均明确无法核实。

原 `accept.json` 和 `live-audit.json` 保持 FAIL；audit 仅两条错误为删除轮未满足正常驱动/完整完成。D14 明确要求预算耗尽时返回有依据的部分结果和停止原因，D55 要求保留本轮状态及缺口；这条结果满足上述功能契约，不能称已完整解决研究问题。它与前次退修中“没有实际工具调用、伪称打开来源、丢失仍可用正文”的缺陷不同。执行者最终四轮均 completed 的结果亦由 Leader 用数据库、文件及历史引用独立回读，四轮正常检查 PASS（136215 tokens），未重发模型请求。正常删除与预算停止两个行为均有最终同包真实证据，R20 checklist 不要求每次研究都必须完成。

## 质量边界与证据保全

搜索收敛仍有不足：删除后重复空查询耗尽10次配额。报告还有“初步登记”写成年初、无支持的颜色/权限级别猜测等措辞问题；这些原文及此前 DSML/序言/修正丢失来源反例全部交 R23/R24，未宣布语义质量或任何数值阈值通过。D27 的程序引用校验不替代语义支持与人工抽检。

最终 manifest `05dde75d3ec97fdba135aee376bc2fc1db0c163f12085b0494c5d344818a4c21`、23路径及292份索引在回读前全部核对。Catalog 回读关闭连接时自动移除了 SQLite 共享内存文件和空 WAL，主数据库及其余290份证据字节未变；最初汇总因此报缺少 shm，原失败和修订汇总均保留，未改写执行者 manifest。6537继承路径、根双语 README、11份旧 SQL 保持。Leader 仅更新本验收文档、台账与提交记录。

完整命令、cwd、退出码、源码/包/模块摘要、原失败和本次边界见 [独立验证](R20-leader-validation.json)。R20 已知真实回答用量合计798559 tokens，另有早期1次 transport 失败用量未知；未知不作零。清理由用户另派任务负责，不构成功能提交门槛。没有 push、发布或后续阶段完成声明；R21 管理命令/Remote 继续推进。
