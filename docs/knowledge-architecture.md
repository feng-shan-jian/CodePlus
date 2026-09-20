# CodePlus Knowledge：首版架构

日期：2026-09-20。状态：K01 环境及 S1–S7 已通过 leader 功能与代码质量验收，纳入逐阶段本地提交，支持三种格式的导入、检索、更新、删除、恢复，TUI、非交互 CLI、Remote 的知识问答、会话绑定和引用报告，以及独立冻结检索实验。组合验收与用户文档已完成。实施顺序见 [最小任务清单](knowledge-tasks.md)，使用步骤、实测证据及遗留项见 [环境说明](knowledge-setup.md)。

本版取代此前的整库快照发布方案。目标是本地个人知识库，以及可独立运行的检索评测。

## 1. 已确认的选择

- 向量数据库：Milvus Standalone，Docker Compose 部署。
- 本机运行位置：CodePlus/Embedding 使用 Windows Python；Milvus 使用 Ubuntu-24.04 WSL2 内已有的 Docker Engine，S1 已实际部署并验证。
- Embedding：本地运行；Qwen3-Embedding-0.6B 的固定 revision 与 CPU 运行参数已在 S1 验证。
- 回答：复用现有 CodePlus 模型配置。
- 文档：先 Markdown，再文本型 PDF、DOCX。
- 输出：带可定位来源的回答和 Markdown 报告。
- 日常更新：按文档更新；独立实验使用明确冻结的数据副本。

## 2. 两条核心流程

```text
导入：原件 → 解析 → 分块 → 本地 Embedding → Milvus
提问：查询 Embedding → Milvus 召回 → 读取原文 → 现有 Agent 回答
```

CodePlus 内增加一个 knowledge 模块。命令、Agent 工具和评测程序调用同一套服务函数。Milvus 单独运行；首版不另建 RAG HTTP 服务。

Milvus 保存向量、检索文本和片段标识。SQLite 保存知识库、文档及片段的登记信息。本地文件保存导入原件和必要的重试材料。SQLite 不承担向量检索。

## 3. 代码与数据范围

按实现需要逐步创建模块：

```text
codeplus/knowledge/
  __init__.py
  __main__.py      独立管理/检索命令
  models.py        配置与数据结构
  metadata.py      SQLite 登记信息
  documents.py     原件复制、格式解析、结构分块与来源范围
  embedding.py     本地模型
  milvus_store.py  官方 SDK 适配
  service.py       导入、检索、更新和状态保护
  citations.py     原文读取与引用校验
  evaluate.py      固定语料、原文证据召回、Milvus 索引/BM25/RRF 实验
```

先实现具体函数，不预先创建多后端继承框架。后续增加替代模型或数据库时再抽取实际需要的接口。

数据固定在启动时解析的 `<project_root>/.codeplus/knowledge/`，进入 worktree 或改变工作目录不隐式改用另一套数据。现有 Git 忽略规则覆盖该目录。

首期 SQLite 只需三类表；配置和实验结果按文件保存：

| 表 | 关键字段 | 用途 |
| --- | --- | --- |
| knowledge_bases | id、name、collection_name、profile_hash、revision、state、error、pending_operation | 当前集合、模型/分块约定、是否允许查询；未完成创建的明确目标 |
| documents | id、kb_id、source_uri、content_hash、generation_id、original_path、state、pending_operation、pending_path、error、removed、updated_at | 原件身份、当前版本、待执行操作、删除标记及最后成功提交时间 |
| chunks | id、doc_id、generation_id、ordinal、text、source_spans、original_path | 原文片段、来源位置与该代原件的精确路径 |

`generation_id` 根据原件哈希、解析器和分块配置确定。相同字节的不同来源保留各自文档身份。旧片段与原件保留供历史引用读取，但旧向量在文档更新完成后移除。

S3 schema 2 在现有三张表加列，从 S2 documents 回填 chunks.original_path；更新不覆盖历史 chunks，恢复旧内容时继续使用该代已保存的原件路径。PDF/DOCX 解析器标识只参与各自文档代次计算，新增格式不改变已有 Markdown 库的 profile_hash。

命令交互采用 schema 3：仅增加可空的 `documents.updated_at`，在成功提交导入、更新或移除时与 revision 同事务写入 UTC 时间；历史未知时间不回填猜测值。`list_libraries()` 直接查询现有 SQLite 表，提供库名、状态和未移除的文档数，不连接 Milvus、不加载模型，也不建立额外管理服务。

`SearchResult` 保存 query、kb_id、revision、检索配置以及 hits。每个 hit 包含 chunk_id、doc_id、generation_id、原文、来源和带类型的分数。引用 ID 对应已保存的片段，不能由模型编造文件路径。

## 4. Milvus 集合规则

一个知识库使用一个稳定集合，并绑定明确的 Embedding、解析和分块配置。更换模型或分块配置时先创建单独实验集合；不能向原集合混写不兼容向量。即使维度相同，也要核对模型 revision 与输入模板。

首期集合字段为：

- `chunk_id`：VARCHAR 主键，稳定生成，关闭 AutoID。
- `doc_id`、`generation_id`：VARCHAR，用于单文档替换与校验。
- `text`：VARCHAR，检索预览；按 UTF-8 字节检查长度。
- `dense`：FLOAT_VECTOR，维度来自锁定的模型配置。

日常集合继续使用 FLAT + COSINE，只支持 dense。S6 在新实验集合创建 text analyzer、BM25 Function 和 sparse 字段，并用同一份冻结向量比较 FLAT/HNSW。**当前没有日常 BM25/hybrid 迁移入口**；实验不修改任何日常集合 schema 或绑定。未来若提供日常混合检索，须另行实现显式重导与绑定切换，不能把本阶段实验视为已经迁移。

## 5. 更新与失败处理

采用简单的“同一知识库串行操作”规则：读取和数据库写入共用跨进程文件锁。锁只覆盖检索和提交过程，不持有它等待大模型回答。UI 使用异步等待或后台线程，避免锁等待卡住界面。

写入顺序：

1. 在本地保存原件、解析结果和向量，形成可重试的文档材料；准备失败不改变当前可查询数据。
2. 获取知识库锁，重新核对知识库配置和文档版本，并确认没有待修复操作。
3. 在 SQLite 记录待执行操作，知识库标记为 UPDATING。
4. 仅删除该 doc_id 的旧向量，写入本次片段；用稳定 ID 完整行 upsert 重试，验证写入可见与片段集合符合预期。
5. SQLite 单事务保存当前文档、片段，清除 pending_operation，增加 revision，恢复 READY。
6. 释放锁。

SQLite 与 Milvus 没有跨库事务。若第 3–5 步中断，查询在取得锁后仍会因状态不是 READY 而拒绝执行，展示“待恢复”；重试依据落盘材料重新执行该文档替换。禁止凭超时、异常消失或旧 PID 自动标记成功。

初版代价是更新提交期间查询等待，更新中断后该知识库暂不可查询。原件与重试材料仍在，可以明确恢复；无需维护整库发布、回滚、租约及集合回收系统。

移除文档走同样的保护流程：删除该文档向量、验证不可检索、记录 removed，保留历史引用所需的原件和片段。查询完成后释放锁；一份报告需要再次检索时核对 revision，发现语料改变则提示重新生成，首版不承诺跨多次检索冻结整库。

Milvus 的记录更新能力依据 [Upsert 文档](https://milvus.io/docs/upsert-entities.md)。具体 load、索引就绪和一致性行为在锁定版本的集成测试中验证。

## 6. 解析、回答与引用

- Markdown：标题、段落和行号。
- 文本型 PDF：页码和块范围；扫描件、空文本、加密或损坏文件明确提示。
- DOCX：标题、段落、表格行列；页码不作为首版定位承诺。
- 分块：结构优先，超长块再按固定 tokenizer 拆分；初始候选 512 tokens、重叠 64 tokens，均可调。
- Embedding：查询与文档分别按模型输入约定编码；校验维度、有限数值、模型版本和输入是否被截断。
- 答案：首轮读取知识库证据；检索失败、没有命中和证据不足分别表示，不能拿用户记忆当文档依据。
- 引用：程序检查来源是否存在、是否在本轮提供的证据中。是否支持结论另做人工评测。
- 原文读取：使用保存的原件和片段，不重新读取可能已经修改的源文件。

`SearchKnowledge` 与 `ReadDocument` 通过既有 `ToolResult.output` 返回结构化 JSON。模型只能使用当前作用域，集合名称由程序解析。知识库模式保持现有权限规则；写报告仍走正常文件写入授权流程。

会话只新增可选的 knowledge_binding（知识库 ID 与检索配置）。旧会话缺字段时关闭该模式。恢复、切换和 off 同步更新绑定；当前知识库不可用时明确报错。

## 7. 实验范围

日常使用支持更新文档。`python -m codeplus.knowledge evaluate --fixtures <目录>` 通过现有 KnowledgeService 导入临时库，复用 documents 的分块/来源范围、LocalEmbedding 和 MilvusStore。`--fixtures` 与 `--replay <frozen.json>` 必选其一；公开样例位于仓库 `tests/fixtures/knowledge`，不作为 wheel 的运行期依赖。

实验输出写入当前工作目录被忽略的 `.codeplus/knowledge/experiments/<run>/`。frozen.json 保存完整原文、原文范围标注、profile、文档/查询向量和来源映射；report.json 保存稳定的 corpus_sha256、冻结内容校验和、运行配置、各路原始结果、分词、失败和耗时。语料哈希仅依赖排序后的文件名与原文 SHA256；完整快照/行哈希包含本轮实际身份。replay 只使用冻结内容及冻结 profile，无需旧临时路径、当前 fixtures 或 Embedding 加载；当前配置只用于连接等运行参数。

实验集合使用随机 `codeplus_eval_` 名称，与临时导入库均在 finally 内按本轮明确名字删除；来源临时目录正常关闭后清理。日常库隔离在验收中检查，不在生产评测中增加整库扫描或审计层。

实验顺序为：FLAT 参照 → HNSW 对比 → BM25 单路 → RRF 混合 → 可选精排 → 模型/分块对照。

- 数据库性能：同一批保存的向量和查询，比较精确近邻召回、延迟、吞吐和资源。
- 文档检索：固定问题和人工标注，比较是否找到了正确原文。
- 回答质量：检查答案是否受证据支持、引用是否准确。

S6 固定 16 个中英文问题、18 处原文证据范围，包含两题无答案和一题需要五处证据的跨文档问题。Evidence Recall@K 的分母是有答案问题的全部标注范围数；同一来源范围须由前 K 个结果的区间并集完整覆盖，部分覆盖不计。请求失败按零命中保留在固定分母，并单独报告失败率与成功请求召回；无标准答案的召回为 null，不计满分。重复测时不重复扩大证据分母。ANN Recall@K 另以同范围 FLAT 实际返回的 K 内 ID 集合作参照，空参照不可用。

实验的 dense/bm25/hybrid 模式和候选数只属于 evaluate CLI；日常配置、SearchKnowledge、会话绑定不扩展模式。RRF 使用两路一基排名等权求和 `1/(rrf_k+rank)`，保留原始分数/排名，以 chunk ID 打破融合平分。两路有效但某路空返回，正常计算并记录该路 no_hits；任一路异常，hybrid 标记 unavailable，保留各路错误和原始输出。

固定背景目录包含 1040 条短小虚构展品，让真实分块达到当前 Milvus 建索引规模。小集合可能显示 Finished/indexed_rows、非零 index_id 和 HNSW index_name，却跳过 HNSW 构建；报告保留 loaded segment/index 信息，将 ANN 数值明确标为配置集合的邻居重合率，execution_verified=false，不能仅凭公共 API 当作已验证的 HNSW 比较。实际本轮类型须结合服务端对应集合的 build/load 日志确认，验收证据记录在 setup。禁止为实验改变共享 Milvus 全局阈值。微型合成集的 P50/P95 不用于生产性能结论。

BM25 创建条件以 [官方全文检索文档](https://milvus.io/docs/full-text-search.md) 为准。不会把不同数据库自带 analyzer 的差异误称为向量索引性能差异。

## 8. 接入与本次边界

现有 TUI、Remote、非交互 CLI 分别初始化 Agent；`Agent.run` 与 `run_to_completion` 也分别构建上下文。按任务清单逐个接入，复用同一服务与知识库上下文准备函数，不靠只修改 TUI 宣称全入口完成。

首版暂缓：扫描件 OCR、复杂版面、多用户权限、退出后继续运行的导入服务、自动历史清理、整库版本回滚、第二种数据库。

K01–S6 的实现、独立验收和清理遗留详见 [环境说明](knowledge-setup.md)。S7 以真实 Qwen/Milvus/回答 provider 的 Textual Pilot 组合链路验证三格式问答、报告及来源、更新/删除后的新检索和历史引用、off 后普通模式；复用前序恢复、CLI/Remote 和冻结评测证据，不扩大为掉电、生产性能或可视桌面验收。可选 K34 精排、K35 模型/分块对照本轮未实现。

S1 直接复用 `config.py` / `validator.py` 的读取与合并路径；默认关闭，按显式出现的 knowledge 字段覆盖前层。数据目录在 `load_config` 返回前按启动目录解析为绝对路径。模型和 tokenizer 默认使用同一个固定 revision，CPU float32、左侧补齐、末 token pooling 和 L2 归一化；实际维度及上下文约束在首次加载模型时核对，输入超限时报错，不静默截断。只有调用编码入口才导入模型依赖或下载模型。首期仅创建 `knowledge/__init__.py` 和 `embedding.py`，未预建存储、服务或解析器框架。
