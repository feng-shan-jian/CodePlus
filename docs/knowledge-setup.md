# Knowledge 本机环境与运行说明

更新日期：2026-09-21。先按以下步骤使用；后面的 K01–S7 是历史实测记录，其中“尚未实现”等表述仅代表该阶段当时状态。S1–S7 首版及混合检索 H01–H06 已通过 leader review，验收与清理限制见 [混合检索计划](knowledge-hybrid-retrieval-plan.md)。

## 最短使用流程

### 1. 安装、配置与自动准备

在项目自己的 Windows 环境执行 `uv sync --locked --extra knowledge`。后续使用 `uv run` 时也带 `--extra knowledge`，避免默认同步移除可选依赖；或直接使用 `.venv\Scripts\python.exe`。Windows 与 WSL 不共用虚拟环境。

编辑**已有完整** `.codeplus/config.yaml`，保留已配置的 `providers`，添加或修改下面的块。它只是配置片段，不能单独保存成 `config.local.yaml`：现有加载器要求每个 YAML 层都有非空 `providers`。

```yaml
knowledge:
  enabled: true
  milvus_uri: http://127.0.0.1:19530
  managed_local: true
  data_dir: .codeplus/knowledge
  retrieval_mode: auto
  retrieval_candidates: 50
  rrf_k: 60
  top_k: 5
```

相对 `data_dir` 在启动时解析。多个启动目录要共用库时，改为同一绝对路径，例如 `D:/CodePlus/.codeplus/knowledge`。默认 Embedding 是本地 Qwen3-Embedding-0.6B，固定 revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`、CPU、1024 维；首次准备可能下载模型，后续复用 HF 缓存。回答使用原有 provider。已建库绑定模型与分块配置，修改这些参数须建新库重新导入。

`retrieval_mode` 默认 `auto`：新混合库用 hybrid，已知旧向量库用 dense。可显式设置 `dense`（向量）、`bm25`（正文关键词）或 `hybrid`（两路 RRF）；旧库显式使用 bm25/hybrid 会报错，升级见 [旧库兼容与重建](#旧库兼容与重建)。**H05 的真实 workbin-v1 回归集建议显式设为 `dense`**，默认 hybrid 未全面改善该集；没有改动现有用户配置。

hybrid 每路候选数为 `max(retrieval_candidates, 本次 top_k)`，融合后返回 Top-K；候选数必须是 1–16384 的整数，`rrf_k` 必须是有限正数。dense/bm25 仅取该路 Top-K，不使用候选数或 RRF 参数。修改检索参数后重启应用即可生效，无需重建库。TUI、CLI、Remote 和 Agent 共用配置，工具参数及会话绑定不新增模式字段。纯 BM25 的 search 不编码查询，现有 prepare 仍会准备模型。

结果中的 `score_type` 为 `cosine_similarity`、`bm25` 或 `rrf`，RRF 是排名融合分数，不是相似度或概率；`retrieval` 显示请求/实际模式、实际候选数、RRF 参数和两路数量。一路正常空返回可融合，任一路异常会使 hybrid 查询失败，不静默降级。

Windows 需先安装 Ubuntu-24.04 WSL2 内的 Docker Engine 和 Compose。CodePlus 使用这个发行版，不调用 Windows Docker Desktop；应用会保持自己的隐藏 WSL 会话，无需额外打开终端。Linux/macOS 使用本机 Docker。然后运行：

```powershell
uv sync --locked --extra knowledge
uv run --extra knowledge codeplus
```

输入 `/knowledge`、`create`、`use` 或恢复知识库会话时，应用异步准备环境，按实际步骤显示“正在连接服务”“正在启动服务”“正在加载模型”“已就绪”；已有健康服务直接复用。失败后按提示修复，再执行 `/knowledge prepare`；独立命令为 `python -m codeplus.knowledge prepare`。`retry` 仍只用于恢复已登记的资料写入。缺依赖时重新安装 knowledge extra；首次模型下载需要网络和磁盘空间，离线使用须已缓存固定 revision。

`managed_local` 默认 `false`，外部服务（包括自行部署在 localhost 的 Milvus）保持此值。只有明确设为 `true` 且 URI 为 `http://127.0.0.1:19530`，才允许操作随包提供的固定 `codeplus-knowledge` Compose 项目；启动前核对已有容器服务标签、镜像和端口，使用 `up --no-recreate --wait` 复用健康检查。资源定位与启动目录无关，wheel/sdist 均携带 Compose。多实例串行准备共享项目，退出仅释放自己的连接和 WSL 管道，不执行 stop/down，也不关闭其他实例或外部服务。普通聊天、disabled 模式及本地 status/source 不启动服务或加载模型；程序不修改配置或 providers。

### 2. 导入、问答与报告

在 TUI 输入 `/knowledge` 打开操作候选，用上下键选择、Tab 补全、Enter 执行。创建时输入名字；选择库时显示名字，执行后才切换当前绑定。Esc 返回或取消选择，不会停用已选知识库；停用需明确执行 `off`。熟悉后仍可直接输入完整命令，原有 ID 接口保留：

```text
/knowledge create "个人资料"
/knowledge import "C:\资料\说明.md"
/knowledge import "C:\资料\报告.pdf"
/knowledge import "C:\资料\手册.docx"
/knowledge status
/knowledge sources
```

也可 `import "C:\资料目录"` 递归导入 `.md`、`.pdf`、`.docx`；Windows 反斜杠和路径中的空格会保留。TUI 状态栏显示已处理、成功、未变化和失败数，结束后汇总失败原因与恢复命令，不逐文件刷聊天。退出候选不会取消已启动的导入；导入结束前仍保留原有切库、切会话和提问保护。随后直接提问：“根据资料比较各方案，列出依据和缺少的信息，并给出引用。”生成文件时明确要求：“把比较结果用 WriteFile 保存为 comparison.md，每个结论附原文引用。”写文件沿用原权限；按提示授权，或启动时显式加 `--mode acceptEdits` 允许编辑。

TUI 与 Remote 把每条回答的引用显示为 `[1]`、`[2]`，附文件名和页码、行号或段落。点击短引用或来源条目，即可在应用内阅读引用原文、位置、完整 ID、版本及“当前版本／历史版本／已删除”状态。TUI 支持 Tab 聚焦回答、Enter 打开，来源列表用方向键和 Enter 选择，Esc 或关闭按钮返回原焦点；Remote 链接与按钮支持 Tab、Enter，预览用 Esc 关闭。来源原文按纯文本显示。

短号仅属于这一条回答；会话正文、CLI 和报告始终保留完整 `K:<kb_id>:<chunk_id>`。仍可执行 `/knowledge open K:<kb_id>:<chunk_id>`（也可含方括号）读取保存的原文、行/页/段落位置与原件路径。长片段返回 `next_offset` 时，用 `/knowledge open K:<kb_id>:<chunk_id> <next_offset>` 继续。报告追加来源、generation 和库 revision；程序校验引用存在且本轮已提供，结论是否受到原文支持仍需核对。

恢复会话时，画面回放原始消息，包括压缩、切库及 `/knowledge off` 之前的回答；模型仍只恢复最后上下文边界后的消息。点击旧引用按链接中的库与 chunk 查询原版本，不改变当前绑定、不注入本轮证据，也不需要加载嵌入模型。元数据或来源不可用时，预览显示原因。

### 3. 更新、删除、恢复与退出

```text
/knowledge use <kb_id>
/knowledge import "C:\资料\说明.md"
/knowledge sources
/knowledge reimport <doc_id>
/knowledge remove <doc_id>
/knowledge status
/knowledge retry
/knowledge off
```

`sources` 在 TUI 显示文件名、状态和更新时间，选择资料后可重新导入或移除，实际执行仍使用内部 `doc_id`。`reimport` 从该资料的原始源路径读取；源文件已移动时使用 `import` 指定现有路径。成功更新或移除刷新更新时间，失败及内容未变化不会刷新；旧数据库升级后缺失的时间显示为未记录。

同一路径内容改变后再次 import 即更新；相同内容不重复编码。删除后新检索不再返回该文档，更新后只检索新代；旧回答/报告的引用仍能通过 open 核对旧代。失败按实际阶段恢复：

- 文件读取、解析或写入登记前失败：修好文件后重新 `import` 原路径。TUI 在当前进程中按库保留未解决的失败路径候选；重导入一份成功后，其余失败仍可选择。这类提示不保存为持久文档记录，重启后可依据原汇总重新导入。
- 已登记的待恢复写入：修复服务/文件问题后执行 `retry`，重放原有材料；READY 且无待处理操作时不改库。被待恢复库阻止的其他文件，恢复后仍需重新导入。
- 环境连接或模型准备失败：按原因修复后执行 `prepare`。它与恢复已登记写入的 `retry` 不同。

off 关闭当前会话知识模式并清空当前回答上下文，历史记录和引用保留；不会删除库。彻底禁用时将配置设为 `knowledge.enabled: false` 并重新启动，普通编码模式不要求 Milvus 或模型依赖。

### 旧库兼容与重建

安装 `uv sync --locked --extra knowledge` 后，create 新库使用 SentenceSplitter（512 tokens / 64 tokens 重叠预算，包含特殊 token 的最终片段不得超限）及 dense + BM25 索引。旧库不会自动升级：

| 库的保存配置 | auto 实际检索 | 原有写入与恢复 |
| --- | --- | --- |
| 新 `dense-bm25-v1` 索引 | hybrid | 正常导入、更新、删除、retry |
| SentenceSplitter、无 indexing 的旧向量库 | dense | 仍可导入、更新、删除、retry，不因缺少 BM25 禁写 |
| `structure-offsets-v1` 旧分块库 | dense | 禁止新 import/update/reimport；仍可 remove、retry 已登记材料及读取历史引用 |

分块或 BM25 升级共用一次 create/import/use 流程，在 TUI 或 Remote 输入：

```text
/knowledge create "资料库 混合检索"
/knowledge import "C:\资料目录"
/knowledge sources
/knowledge status
/knowledge use <新库ID>
```

create 会立即选中新库；导入全部原件、核对数量、状态与引用后，其他会话用 `use <新库ID>` 切换。独立 CLI 对应 `python -m codeplus.knowledge create "资料库 混合检索"` 和逐文件 `python -m codeplus.knowledge import <新库ID> <原件路径>`；问答 CLI 使用 `codeplus -p '问题' --knowledge <新库ID>`。保留旧库，旧报告中的 `K:<旧库ID>:<chunk_id>` 继续指向旧代；不要手改旧 profile、覆盖原件或删除旧库目录。原来源路径丢失时，可从旧库 status 的 `original_path` 取保存原件导入新库。

评测重建使用冻结数据集 corpus 原件和独立新库，将新 ID 通过 `benchmark --kb-id <新库ID>` 传入；无需修改现有 binding.json、问题、参考答案、解析文本或历史结果。

### 分块升级实测（2026-09-20，历史 dense 库）

本次实现者实测（2026-09-20，leader 已验收通过）：Python 3.14.3 安装及 `uv lock --check` 通过；知识库相关回归 47 passed / 4 skipped。独立新库用真实 Qwen/Milvus 导入冻结 31 份资料，789 → 742 个片段，含特殊 token 的最大长度 509；导入耗时 989.18 秒，环境准备另计 57.68 秒。原基线未记录可比导入耗时，不作加速结论。

原 benchmark 全部 32 题零查询错误；单轮找齐题数 20/25 → 20/25，指定证据覆盖 27/34 → 28/34；追问均为 1/3。仅 Q28 从 0/2 改善到 1/2，无证据覆盖退步题；Q28 仍未找齐全部依据。4 道无答案题不自动判拒答通过，这些指标不是回答正确率。原库真实检索及基线 112 个不同引用可用，新结果的 108 个不同引用经应用 preview 入口核对；原库状态、32 个原库文件与 76 个既有评测文件核对未变。详细对照与逐文件耗时位于本机 `.codeplus/benchmarks/workbin-v1/runs/sentence_upgrade_20260920T111625Z/`，原 benchmark 新报告位于 `runs/20260920T113421Z_eec171/`。未执行回答模型或桌面点击验收，4 个独立 opt-in 测试的 skipped 不计为通过。

Leader 独立复核：上述 47/4 回归与锁文件检查结果一致；两份报告重算、实现源码指纹及 108 个保全文件指纹均一致。核对全部 742 个新片段的真实 token 长度与来源范围，另验 9 组真实 tokenizer 边界样例，并对新旧库各执行一次真实查询与来源读取；均通过。本轮未重复完整导入、回答模型或桌面点击验收。

Leader 临时核验脚本已删除；系统临时目录 `C:\Users\18221\AppData\Local\Temp\codeplus-leader-sentence-review-20260920` 的清理被自动审批拒绝（未提供具体原因），暂保留，不纳入提交。

### 4. 非交互 CLI 与 Remote

在含上述完整配置的项目目录执行，将 `$kbId` 换成 create 返回的 ID：

```powershell
$kbId = '<kb_id>'
uv run --extra knowledge codeplus -p '根据资料回答问题并引用来源' --knowledge $kbId
uv run --extra knowledge codeplus -p '根据资料回答问题并引用来源' --knowledge $kbId --output-format stream-json
uv run --extra knowledge codeplus --remote
```

`--knowledge` 必须配合 `-p`，省略它即普通问答。text 输出最终正文，stream-json 在 stdout 逐行输出 JSON，诊断走 stderr；检索/回答失败非零退出。非交互知识模式无法弹出权限询问，保存报告可显式使用 `--mode acceptEdits`，其他原权限约束仍生效。

Remote 聊天框使用同一套 `/knowledge` 命令。导入路径是**运行 CodePlus 的服务器路径**，相对路径基于服务端工作目录；当前没有浏览器上传。服务默认监听 `0.0.0.0:18888`。

独立管理入口不调用回答模型，支持 create/import/update/search/status/remove/retry/source；`--config` 必须放在子命令前，且指向完整 YAML。它的 import/update 接受单文件，目录导入使用 TUI/Remote。

```powershell
uv run --extra knowledge python -m codeplus.knowledge --config .codeplus/config.yaml search $kbId '检索问题' --top-k 3
uv run --extra knowledge python -m codeplus.knowledge --config .codeplus/config.yaml update $kbId 'C:\资料\说明.md'
uv run --extra knowledge python -m codeplus.knowledge --help
```

### 5. 冻结评测与三路实验

真实资料的固定回归集使用 `python -m codeplus.knowledge benchmark`，支持原件/引用检查、日常检索复跑和离线重算；题目、版本及单轮/追问/无答案评分口径见[标准回归评测说明](knowledge-benchmark.md)。个人语料保存在本地，不纳入公共测试样例。

在仓库根目录运行；仅安装 wheel 的用户需提供自己的 fixtures 或冻结文件：

```powershell
uv run --extra knowledge python -m codeplus.knowledge evaluate --fixtures tests/fixtures/knowledge --mode all
uv run --extra knowledge python -m codeplus.knowledge evaluate --replay '<上次输出的 frozen.json 绝对路径>' --mode hybrid --ef 16 64
```

首次运行保存原文、标注、profile 和真实文档/问题向量；replay 不加载模型、不读取原 fixtures。输出在当前目录 `.codeplus/knowledge/experiments/<run>/`，含 frozen.json 和 report.json。未传 `--config` 的 evaluate 使用本地默认连接，不要求回答 provider。

三路为 dense、Milvus BM25、RRF hybrid；evaluate 的 `--mode` 选择附加证据检索路线，**所有模式仍保留 dense/FLAT 与 HNSW 的 ANN 对照**。实验另建临时集合，不修改日常库或绑定；日常检索策略的同库比较用 benchmark，旧库升级用前述 create/import/use。配置为 HNSW、SDK 显示 Finished 不足以确认真实执行类型；S6 已用 1066 个片段及对应服务端构建/加载日志核实。完整历史结果和冻结路径见下方 S6，不用小集合指标推断生产性能。

### H05 混合检索实测与交付边界（2026-09-21）

真实 Qwen/Milvus 功能与六组同库报告已通过 leader review；默认 hybrid 有改善也有退步，本集建议 dense。简要指标、正式新库 ID、切换命令和本地 ignored 报告入口集中在 [标准回归评测说明](knowledge-benchmark.md#h05-真实资料对照2026-09-21)，私人原文与失败片段仅保留在本地报告。

H06 仅调整文档和示例注释，复用 leader 对 H01–H04 最终生产代码的全仓结果：`python -m pytest -q` **814 passed / 10 skipped / 1 warning**（88.89 秒）；唯一 warning 是既有未注册 `pytest.mark.timeout`。H04 knowledge/config 为 **151 passed / 7 skipped**。真实 Milvus/Qwen 与恢复证据单列在 [计划执行记录](knowledge-hybrid-retrieval-plan.md#执行记录leader-维护)，skip 不计通过。H05 临时入口已清理；H01 `.h01-pytest-service` 的 32 个只读测试原件曾被自动审批以 `blocked by policy` 拒绝删除，仍为遗留限制，本阶段未触碰。

### 范围限制

支持 Markdown 行号、文本型 PDF 物理页码、DOCX 正文段落/表格行列。扫描 PDF 无 OCR，旧 `.doc` 不支持；复杂 PDF 版面、Word 页码、页眉页脚/批注/嵌套表格不在承诺范围。退出后后台导入服务、自动历史清理、多用户权限、精排与模型对照未实现，未接入 Unstructured。

BM25 匹配正文，文件名未进入检索文本。H05 追问只测试最后一句的首次检索，未增加查询改写或文档定位能力。未执行本轮回答模型、回答/拒答正确性、桌面 UI、生产规模/吞吐及调参后的热重复，不宣称回答质量或全面召回提升；历史 S7 的小样本回答验收仍只代表当时结果。

## 分阶段实测记录

**K01 环境验收通过，使用 Ubuntu-24.04 WSL2 内已有的 Docker Engine。** Windows 上的 Docker Desktop 启动失败，不作为本项目当前的容器运行入口。下表至 K01 验收边界保留当时状态；Milvus 和本地 Embedding 的后续验证单独记录在文末。

## 运行位置

| 部分 | 采用的位置 | 当前状态 |
| --- | --- | --- |
| CodePlus / Python | Windows，`D:\CodePlus\.venv\Scripts\python.exe` | 解释器可执行 |
| 本地 Embedding | 后续接入上述 Windows Python 环境 | K04 验证模型及依赖兼容性 |
| Docker Engine / Compose | WSL2 的 `Ubuntu-24.04` 发行版 | 已连接真实服务端 |
| Milvus Standalone | 后续在上述 Docker Engine 中部署 | K02 未开始 |
| 容器持久数据 | 后续使用该引擎的 Linux 命名卷；根目录 `/var/lib/docker` | K02 创建并记录卷名 |
| 知识库原件与 SQLite | 后续使用 `D:\CodePlus\.codeplus\knowledge` | 未创建知识库 |

PowerShell 通过 `wsl -d Ubuntu-24.04 -- docker ...` 管理容器。Windows Python 通过 Milvus 的发布端口连接；实际连接与持久性在 K02/K03 验证。读取仓库中的 Compose 文件可以使用 `/mnt/d/CodePlus/...`，容器数据库数据使用 Linux 卷。

Windows 和 WSL 不共用 Python 虚拟环境或 node_modules。无需在 WSL 安装一份 CodePlus Python 环境；后续如果需要 Linux Python 实验环境，应单独建立。

## 实测版本和资源

以下为本次检查结果，不是最低版本要求。

| 检查项 | 实测结果 |
| --- | --- |
| 操作系统 | Windows 11 专业工作站版，64 位，10.0.26200.9445 |
| PowerShell | 7.6.5 Core |
| uv | 0.11.13，Windows 原生 |
| 项目 Python | 3.14.3；venv 为 `D:\CodePlus\.venv`，基础解释器为 `C:\Python314` |
| WSL | 2.6.3.0；默认发行版 Ubuntu-24.04，版本 2 |
| Linux 内核 | `6.6.87.2-microsoft-standard-WSL2`，实际执行 `uname -r` 成功 |
| Ubuntu Docker 客户端 / 服务端 | 29.1.3 / 29.1.3；linux/amd64 |
| Ubuntu Compose | 2.40.3+ds1-0ubuntu1~24.04.1 |
| Docker 服务 | `systemctl is-active docker` 返回 `active`；检查时运行中容器为 0 |
| Docker 存储 | `overlayfs`；`/var/lib/docker` 位于 Linux ext4 文件系统 |
| CPU | Intel Core i9-14900HX，24 核 / 32 逻辑处理器 |
| 主机内存 | 总计 31.63 GiB；20:40 采样空闲约 8.67 GiB |
| Docker 可见资源 | 32 个逻辑处理器，15.43 GiB 内存 |
| 主机磁盘 | C 盘空闲约 322.83 GiB；D 盘约 434.64 GiB；物理磁盘为 NVMe SSD |

Milvus 官方当前列出的 Standalone 内存最低要求为 8 GB、建议为 16 GB，实际需求取决于数据规模，见 [部署前置条件](https://milvus.io/docs/prerequisite-docker.md)。当前环境可进入小数据部署验证；Docker 可见内存不代表主机已为它预留了等量空闲内存，也不能保证与本地模型同时满负荷运行。K02/K04 分别测量实际占用。

WSL 虚拟磁盘显示的逻辑剩余容量可能大于宿主机物理可用空间，不能据此规划数据上限。本次没有做磁盘性能或容器压力测试。

## 复查命令

在 PowerShell 7 中运行：

```powershell
Set-Location D:\CodePlus
uv --version
& .\.venv\Scripts\python.exe -c 'import sys; print(sys.version); print(sys.executable); print(sys.prefix)'
wsl --version
wsl --status
wsl --list --verbose
wsl -d Ubuntu-24.04 -- uname -r
wsl -d Ubuntu-24.04 -- systemctl is-active docker
wsl -d Ubuntu-24.04 -- docker version
wsl -d Ubuntu-24.04 -- docker compose version
wsl -d Ubuntu-24.04 -- docker info --format '{{.ServerVersion}} {{.OSType}} {{.NCPU}} {{.MemTotal}}'
```

上述 WSL Docker 版本、服务状态、Compose 及资源查询均已实际成功。进入 K02 时继续使用明确的发行版前缀，避免误连另一套引擎。

## 已发现的问题与处理结果

### Windows Docker Desktop 仍不可用

本机另有 Docker Desktop 4.87.0.236836、Windows Docker CLI 29.7.2 和 Compose v5.4.0。直接执行 Windows `docker version` 使用 `desktop-linux` context，无法连接 `dockerDesktopLinuxEngine` 管道；不能把客户端版本输出当作服务端可用。

本次启动已有 Desktop 后，日志先后报错：`Docker/run/sailor-ingest.sock` 和 `docker-secrets-engine/engine.sock` 无法访问。旧文件为零字节 reparse point。Docker 项目中有相同类型的 [AF_UNIX socket 启动故障报告](https://github.com/docker/desktop-feedback/issues/531)，但不能据此宣称本机故障已经修好。

处理记录：停止失败的 Desktop，将 `Docker/run` 改名后重试；第二处运行目录改名被 Windows 拒绝。随后退出 Desktop，并将原始 `Docker/run` 还原。未重置、卸载或升级 Docker，未改 Docker 配置、虚拟磁盘、镜像、容器或数据卷。

本次重试产生的目录 `C:\Users\18221\AppData\Local\Docker\run.before-k01-attempt2-20260919` 内有两个零字节 socket：`dockerInference`、`sailor-ingest.sock`。清理该目录以及随后更小范围的逐文件清理均被自动审批拒绝，返回 `blocked by policy`；因此尚未清理。这是任务遗留项，不是后续知识库所需文件。

### WSL 代理提示

启动 Ubuntu 时提示：检测到 localhost 代理配置，但 NAT 模式未将其镜像到 WSL。该提示没有阻止本次本地 Docker 服务端查询。镜像仓库下载尚未验证；K02 拉取镜像时检查网络，如失败再针对实际错误处理代理。

## K01 验收边界

- 已完成：Windows Python/uv、WSL2 实际运行、所选 Docker 服务端、Compose、内存和磁盘检查，以及运行位置确认。
- 未完成：上述重试目录清理；Windows Docker Desktop 故障修复。两者不影响已验证的 Ubuntu Docker Engine 路径。
- 尚未执行：Milvus 部署、Python SDK 兼容性验证、模型下载/推理、跨系统端口连接及 RAG 功能测试。Python 3.14 的后续依赖适配须在 K03/K04 实测，不能从解释器可运行推断。
- 本次没有安装依赖，也没有新增应用代码或正式测试。下一项为 K02，使用这里记录的 Ubuntu Docker Engine 部署 Milvus。

## S1 实现与复现（K02–K05）

实现 worktree：`C:\Users\18221\.codex\worktrees\54e2\CodePlus`。状态：2026-09-19 已通过 leader 独立功能与代码质量验收，纳入 S1 提交后合入主仓 `D:\CodePlus`，再进入 S2。

### 已确定的运行约定

- 按 [Milvus 当前官方 Compose](https://milvus.io/docs/install_standalone-docker-compose.md) 和 [v3.0.1 发布文件](https://github.com/milvus-io/milvus/releases/download/v3.0.1/milvus-standalone-docker-compose.yml) 保留 etcd / MinIO / standalone 三服务，锁定 `milvusdb/milvus:v3.0.1`、`quay.io/coreos/etcd:v3.5.25` 和 `quay.io/minio/minio:RELEASE.2024-12-18T13-15-44Z`。
- 官方 Compose 中的 `minio/minio` 本次实际返回 `pull access denied`。改用 [MinIO 官方说明的 Quay 仓库](https://github.com/minio/minio/blob/master/docs/docker/README.md)，保持原发布版本；该镜像已真实拉取成功。没有添加自动换源或第三方镜像兜底。
- Compose 项目名 `codeplus-knowledge`；命名卷为 `codeplus-knowledge_etcd`、`codeplus-knowledge_minio`、`codeplus-knowledge_milvus`，数据留在 Ubuntu Docker 的 Linux 存储。仅 standalone 发布 `127.0.0.1:19530` 与 `127.0.0.1:9091`，etcd 和 MinIO 不发布主机端口。`stop` / `down` 不加 `-v`，保留数据。
- [PyMilvus 兼容表](https://github.com/milvus-io/pymilvus#compatibility) 要求对应主次版本，本次 `pymilvus==3.0.2`；`torch==2.14.0`、`transformers==5.17.0` 同样锁定，仅安装 `knowledge` extra 时引入。Windows wheel 实测 Torch 显示 `2.14.0+cpu`。
- 独立 Windows 环境为本 worktree 的 `.venv`，使用 `C:\Python314\python.exe` 的 Python 3.14.3。主仓环境仅做 `uv pip install --dry-run` 兼容解析和普通 CLI 检查，未安装、升级或删除其依赖；没有建立或共用 WSL Python 环境。
- [Qwen 模型说明](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) 对应默认模型 `Qwen/Qwen3-Embedding-0.6B`，模型与 tokenizer revision 均为 `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`。缓存使用 `C:\Users\18221\.cache\huggingface\hub`，模型不复制进 Git。
- 输入约定：文档保持原文；查询为 `Instruct: Given a web search query, retrieve relevant passages that answer the query` + 换行 + `Query:` + 查询。CPU / float32、左侧 padding、末 token pooling、1024 维、L2 归一化。默认每批 8 条、上限 8192 tokens，超过上限明确报错。模型/revision 可显式修改，但其他模型未经本阶段验收；后续 K11 必须将实际模型/revision/输入约定绑定到集合，不能只比较维度。

### PowerShell 7 命令

合入后的日常入口使用稳定主目录；本次实测在上文 worktree 执行同一组命令，仅工作目录不同。已有 `.venv` 直接同步，无需重建。

**运行期间保持一个 WSL 会话。** 先在单独终端执行 `wsl -d Ubuntu-24.04`，保留该 Linux shell；再在另一个 PowerShell 终端执行以下命令。首次实测曾在 `up --wait` 健康后自动退出：etcd/MinIO 收到 TERMINATED、Docker daemon 随后重新启动，Windows SDK 因连接拒绝失败，`OOMKilled=false`。这与 [微软说明的 systemd 服务不能保持 WSL 实例存活](https://devblogs.microsoft.com/commandline/systemd-support-is-now-available-in-wsl/) 一致。保持会话是本机运行前提，不以健康检查的一瞬间代替持续可用。未修改全局 WSL、Docker 配置或添加自启服务。

```powershell
Set-Location D:\CodePlus
$windowsCompose = (Resolve-Path deployment/knowledge/compose.yaml).Path.Replace('\', '/')
$compose = (wsl -d Ubuntu-24.04 -- wslpath -a $windowsCompose).Trim()
wsl -d Ubuntu-24.04 -- docker compose -f $compose up -d --wait --wait-timeout 240
wsl -d Ubuntu-24.04 -- docker compose -f $compose ps
Invoke-RestMethod http://127.0.0.1:9091/healthz
wsl -d Ubuntu-24.04 -- docker compose -f $compose stop
wsl -d Ubuntu-24.04 -- docker compose -f $compose up -d --wait --wait-timeout 240

uv sync --locked --extra knowledge
& .\.venv\Scripts\python.exe -m pytest tests/test_mcp.py tests/test_knowledge.py -q

# 只在本机空闲时运行：此用例会 stop/up 上述整个 Compose 项目，保留卷。
$env:CODEPLUS_TEST_MILVUS_URI = 'http://127.0.0.1:19530'
$env:CODEPLUS_TEST_MILVUS_RESTART = '1'
& .\.venv\Scripts\python.exe -m pytest tests/test_knowledge.py::test_milvus_flat_cosine_and_persistence -q -s
Remove-Item Env:CODEPLUS_TEST_MILVUS_URI, Env:CODEPLUS_TEST_MILVUS_RESTART

$env:CODEPLUS_TEST_EMBEDDING = '1'
& .\.venv\Scripts\python.exe -m pytest tests/test_knowledge.py::test_qwen_chinese_embedding -q -s
Remove-Item Env:CODEPLUS_TEST_EMBEDDING
```

第一次编码按固定 revision 下载；后续复用同一缓存。缓存完整后，可在编码测试前设置 `$env:HF_HUB_OFFLINE = '1'` 验证离线加载，完成后删除该环境变量。测试未设置 opt-in 时明确显示 skipped；设置后连接、下载或推理失败直接报失败，不用 mock 或 skip 掩盖。

普通编码模式沿用原安装方式，默认 `knowledge.enabled: false`。开关开启仅允许使用本阶段的 `LocalEmbedding(KnowledgeConfig(...))` 基础接口；S1 尚无 `/knowledge`、导入、问答或报告命令。配置示例见 `.codeplus/config.yaml.example`，数据目录在启动配置加载时确定，不会因后续切换 cwd 漂移。

### 实际执行证据

以下为小样本功能验证，不是检索基准或压力测试。

| 实际检查 | 结果 |
| --- | --- |
| Windows Python 3.14.3 兼容性 | worktree `.venv` 实际安装并导入 pymilvus 3.0.2、torch 2.14.0+cpu、transformers 5.17.0；无需降级 Python |
| Compose / health | 三容器均 healthy；Windows `http://127.0.0.1:9091/healthz` 返回 `OK`，保持 WSL 会话后重启及后续查询稳定 |
| 真实 FLAT + COSINE / 持久性 | `test_milvus_flat_cosine_and_persistence` 1 passed / 64.81 秒；服务 3.0.1、SDK 3.0.2；整个项目 stop/up 前后 ID 均 `[1,2,3]`，分数 `[1.0,0.6000000238418579,0.0]`，行数均为 3 |
| 集合隔离与清理 | 唯一集合 `codeplus_test_b7aec9dc0ad042a5b1f1e9a4a86ba03f` 已由 finally 删除；随后独立 Windows SDK 连接读取 `list_collections()` 返回 `[]` |
| Qwen 首次成功下载及加载 | 473.395 秒（包含网络与一次 read timeout 后续传），编码 0.288 秒，正式测试 1 passed / 473.78 秒；不是纯模型加载耗时 |
| Qwen 离线缓存加载 | 设置 `HF_HUB_OFFLINE=1`，加载 5.888 秒、两段中文和一次查询编码 0.283 秒；1 passed / 6.26 秒 |
| 模型资源 | 用实际 Python 子进程的 Windows `PeakWorkingSet64` 每秒采样：首次成功检查最高 2737.6 MiB，离线检查 2731.7 MiB（约 2.67 GiB）；GPU 未参与 |
| 中文向量与边界 | 报销文档/种植文档对报销查询的 cosine 为 `[0.731235384410723, 0.11854433893122399]`；1024 维、有限值、单位 L2 范数、超限不截断均通过。随后补入真实 768 维错误配置拒绝，同一正式用例离线重跑 1 passed / 7.98 秒 |
| 容器资源快照 | 重启及查询后，standalone 209.6 MiB、MinIO 135 MiB、etcd 16.73 MiB；仅瞬时小样本值 |
| 回归 | 实现对话运行配置/knowledge/worktree/subagent 组：131 passed / 2 skipped。leader 独立运行 knowledge/配置/commands/agent/memory/permissions 组：209 passed / 3 skipped；两组有重叠，不累加计数。跳过项为未开启的外部集成及现有系统符号链接用例 |
| leader 联合验收 | 离线加载真实模型，对两段中文编码并写入真实 Milvus 1024 维 FLAT/COSINE 集合。新问题正确返回相关数据库文档在前、无关食谱在后，分数 `[0.6063879728317261, 0.09747003018856049]`。唯一验收集合和临时脚本已删除 |
| 普通模式 / 锁文件 | 主仓解释器运行本 worktree `python -m codeplus --help` 成功；默认关闭的新进程测试禁止可选 SDK 导入和网络仍通过。`uv lock --check`、Compose config、`git diff --check` 通过；`uv sync --locked --no-extra knowledge --dry-run` 确认普通安装不需要 31 个 knowledge 依赖 |

实测镜像 digest：Milvus `sha256:82630c952e887e30b0b09f396cb1fbd4320dea25b4f7a3ba12bf1e10524c735c`；etcd `sha256:52f17f7e56e4f7239f0320dbfcbcc24721163d7d78ae710b466af3254ccf6366`；MinIO `sha256:1dce27c494a16bae114774f1cec295493f3613142713130c2d22dd5696be6ad3`。卷实际挂载点为 `/var/lib/docker/volumes/<上述卷名>/_data`。

模型权重缓存为 1,191,586,416 字节，SHA256 `0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd`，已用 Windows `Get-FileHash` 复核。snapshot 位于缓存的 `models--Qwen--Qwen3-Embedding-0.6B/snapshots/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`。

下载排查记录：第一次 Xet 探测运行约 430 秒后停止，监测期间可见文件长度为零，但停止后旧 partial 显示 1,024,000,000 字节，不能称为“没有下载”。已确认旧 Python 子进程 7244 停止。第二次仅给测试进程设置系统已有代理 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7897` 和 `HF_HUB_DISABLE_XET=1`，标准 HF 下载经历 read timeout 后在同一进程续传并成功；未修改系统代理或 HF 缓存结构。当前 HF 库使用每进程唯一临时文件，旧 partial 不支持跨进程续传，因此清理了该孤立残留，保留完整缓存。首次 uv 依赖下载也遇到 TLS handshake EOF，标准 `uv sync` 重试成功。

代码质量验收：复用现有配置加载、校验、合并和配置测试；删除初版多余参数检查，保留开关、地址、模型维度及输入上限这些实际边界。模型和 SDK 按需导入，未引入额外服务或抽象框架。功能、回归及上述独立联合验收通过后批准 S1 提交。

### 交接与清理

- 经 leader 明确授权，为后续验收保留隐藏 WSL 会话：`wsl.exe -d Ubuntu-24.04 -- sleep infinity`，本次 Windows PID **80820**，Linux `sleep` PID **2022**，2026-09-19 23:26:19 启动；PID 仅是本次现场证据，后续操作前核对进程命令。它使已有 WSL 服务保持存活，没有添加守护程序或自启任务。
- 三个健康容器及 Linux 命名卷、worktree `.venv` 和完整 HF 缓存保留供 leader / 后续阶段复用。临时监测脚本、探测日志、空测试目录及第一次下载的孤立 partial 已清理；正式测试保留在 `tests/test_knowledge.py`，配置用例复用 `tests/test_mcp.py`。
- 本阶段没有创建个人知识库或导入 `E:\WorkBin` 原件；没有修理 Docker Desktop 或操作 K01 的旧 socket。K01 未清理 socket 仍属于旧遗留项。
- 未执行：GPU 推理、长期稳定性/压力测试、其他模型配置、S2 以后的真实文档导入/检索/问答。没有将小样本排序作为检索质量评测结论。

## S2 Markdown 导入和真实检索（K06–K15）

实现 worktree：`C:\Users\18221\.codex\worktrees\397c\CodePlus`，基线 HEAD 为 S1 提交 `87fad9770d40ad8a1dcc07cb342db9c1ab822c30`。**2026-09-20 已通过 leader 独立功能与代码质量验收，纳入 S2 提交。** 临时文件清理的自动审批阻塞单独记录在末尾。

### 使用入口

同步本工作树自己的 Windows `.venv`：`uv sync --locked --extra knowledge`。本阶段显式声明已存在的 `filelock==4.0.1`、`markdown-it-py==4.0.0`，复用现有依赖版本；未升级 Torch、Transformers、PyMilvus。已验证的默认 profile 仍为 S1 固定 Qwen revision / CPU float32 / 1024 维。分块增加 `chunk_tokens: 512`、`chunk_overlap: 64`。

在现有 CodePlus YAML 中将 `knowledge.enabled` 设为 `true`，为验收指定独立绝对 `knowledge.data_dir`。`--config` 沿用已有配置加载器，因此仍需合法的 `providers` 字段，但以下命令不会调用回答模型。未显式传 `--config` 时，沿用已有多层配置合并规则。

实测使用 `%TEMP%\codeplus-s2-...\knowledge config.yaml` 及其 `acceptance library` 子目录，二者名称均包含空格；完成后已删除。以下 `$configPath` 指向使用者自己的上述 YAML。命令参数与实测 CLI 一致，实际执行目录为本节 worktree：

```powershell
$env:HF_HUB_OFFLINE = '1'  # 本机完整缓存已存在，无需重复下载
$configPath = '.codeplus/config.yaml'
$created = & .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath create 'S2 acceptance'
$kbId = ($created | ConvertFrom-Json).id
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath import $kbId 'E:\WorkBin\Recipe\README.md'
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath import $kbId 'E:\WorkBin\Recipe\docs\recipe-manifest.md'
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath import $kbId 'E:\WorkBin\Recipe\supabase\README.md'
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath status $kbId
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath search $kbId '匿名用户可以读取哪些 recipes 数据？' --top-k 3
Remove-Item Env:HF_HUB_OFFLINE
```

成功退出码 0，业务/配置/模型/SDK 错误为 1，命令参数错误为 2。结果为 JSON，search 返回 kb_id、revision、profile_hash、检索配置及带 cosine_similarity 分数的 hits；每个 hit 包含保存的原文、source_uri、original_path 和来源范围。路径可包含空格；原件按 UTF-8（允许 BOM）读取，不改写源文件。当前仅接受 `.md` / `.markdown`。

### 数据与一致性

- `metadata.sqlite3` 只含 `knowledge_bases`、`documents`、`chunks` 三张表，schema version=1；拒绝未知版本。外键和唯一约束保护库、来源身份与片段登记，最终文档/片段/revision 在同一 SQLite 事务提交。
- 相同来源+相同字节重复导入直接返回 `unchanged: true`，不加载 tokenizer/模型/SDK、不再次编码。相同字节的不同来源保留不同 doc_id；generation_id 绑定内容哈希、解析器、tokenizer 和分块配置，chunk_id 绑定文档、generation 和序号。
- 原件先写临时文件、flush/fsync 后改名并设只读；复制读取前后核对文件身份、大小和 mtime。Windows Python 3.14 的 `stat`/`fstat` ctime 含义不同，不能交叉比较。原件保存在每次导入的唯一目录，避免进程死在 pending 登记前导致后续重导路径冲突。
- Markdown 使用 CommonMark 结构，保存标题路径、段落/代码块类型、原文字符范围和行号。短块优先沿结构合并；仅超长结构按 tokenizer 拆分并加入不超过 overlap 的重叠，不在自然分段间机械补重叠。按字符 offset 切分后重新计算 token 数，避免中文多 token 字符和 emoji 被解码切坏；不静默截断。
- 每库 `filelock.FileLock` 覆盖服务读取、Milvus 检索及结果来源解析、提交；原件/解析/编码准备在锁外，锁内重新核对 profile、状态和来源。独立 CLI 是同步入口；后续 UI 应通过 `asyncio.to_thread` 调用，S2 尚未接 UI。
- 同一 profile 同时写入 SQLite 哈希、profile.json 和集合 description；模型/revision、输入模板、pooling/精度、维度、tokenizer/分块参数改变均不能混写。每库一个稳定 FLAT/COSINE 集合，使用 Strong 一致性及同步 load。
- 提交前落盘完整原件、chunks 和向量；SQLite 登记 pending 并置 UPDATING，SDK 完整行 upsert 后核对该文档的**全部 chunk ID、generation、doc_id 和文本**，成功才写元数据并置 READY。异常保留 NEEDS_REPAIR；硬退出可能保留 UPDATING，两者均拒绝检索，不凭进程消失自动恢复。
- PyMilvus 3.0.2 实测差异：`query_iterator` 直接使用 `expr_params`，而 `delete` 使用 `filter_params`；均参数化传值。search 返回主键名 `chunk_id`。最初真实导入发现 iterator 模板参数未传递，修正后重新完整验收，未增加多版本猜测分支。依据 [过滤模板](https://milvus.io/docs/filtering-templating.md)、[查询迭代器](https://milvus.io/api-reference/pymilvus/v3.0.x/MilvusClient/Vector/query_iterator.md) 和本机 SDK 源码核对。

### 真实结果

Windows Python 3.14.3，PyMilvus 3.0.2 / 服务 3.0.1，Qwen 固定 revision，仅用现有离线缓存。完整验收库 `e61257ac5a63408ba8dc02a478bbc9ee`，集合 `codeplus_kb_e61257ac5a63408ba8dc02a478bbc9ee`；最终 READY、3 documents、23 chunks、revision=3。

| 原件（只读来源） | chunks | SHA256 |
| --- | --- | --- |
| `E:\WorkBin\Recipe\README.md` | 10 | `a4bf37f33a8d189c290444067236c6c0945d93ffd7ea50d0ee2adbbedbfd8064` |
| `E:\WorkBin\Recipe\docs\recipe-manifest.md` | 6 | `63b6b97811bcf191dd88daf8aca45fb31250c4603eed833ac19242b66a2393c1` |
| `E:\WorkBin\Recipe\supabase\README.md` | 7 | `cacac7e0bae65ed2e5225706a3fa0b7c476b1b97d0bf819385bbb121f2e98d7b` |

每次 import 独立 CLI 启动，耗时 12.149 / 11.395 / 11.780 秒（包含各自模型加载）；重复 import 0.258 秒，前后 status 完全相同。另以新服务对象复核重复调用后 `_model is None`、`_store is None`。原件副本与来源文件逐字节一致；来源后来被修改仍使用已保存的原件（正式测试用合成文件验证，没有修改上述样例）。

| 已知答案问题 | 预期来源命中的排名 / cosine | 命中范围 |
| --- | --- | --- |
| 本地启动网站的开发命令和访问端口是什么？ | 1 / 0.695748 | 根 README 7–14 行 |
| 生产环境缺少 Supabase 配置时公开目录会怎样？ | 1 / 0.825055 | 根 README 16–24 行 |
| 公开投稿的仓库根目录建议提供什么 manifest 文件？ | 2 / 0.640978 | recipe-manifest 3–5 行 |
| 管理员审核通过就会自动标记为 Verified 吗？ | 2 / 0.551349 | recipe-manifest 74–76 行 |
| 匿名用户可以读取哪些 recipes 数据？ | 1 / 0.676757 | supabase README 15–22 行 |

五题均在 Top 3 找到预期文档和答案片段；对所有返回 hit 的 source_spans 逐片核对字符子串、原文行范围和保存文本。每次 search CLI 6.262–7.591 秒，包含进程启动及模型加载；这只是五题链路核验，不是正式召回率或性能评测。

### 关键检查、清理与后续边界

正式检查集中在 `tests/test_knowledge_service.py`，复用 S1 的 `tests/test_knowledge.py` 和既有配置/命令测试：

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_knowledge_service.py tests/test_knowledge.py tests/test_mcp.py tests/test_commands.py -q
$env:CODEPLUS_TEST_MILVUS_URI = 'http://127.0.0.1:19530'
& .\.venv\Scripts\python.exe -m pytest tests/test_knowledge_service.py -q
Remove-Item Env:CODEPLUS_TEST_MILVUS_URI
uv lock --check
uv sync --locked --extra knowledge --dry-run
git diff --check
```

- 相关回归：73 passed / 3 skipped；跳过的是未 opt-in 的 S1 两个外部集成及 S2 SDK 用例，不当作通过。S2 真实 SDK 单独 opt-in 最终回归：7 passed / 43.14 秒，覆盖实际创建、两文档写入、检索、完整行重复 upsert、同维不同 profile/错维拒绝、参数化删除与另一文档不受影响，以及 65535 UTF-8 字节边界。
- SQLite 故障注入：SDK 写入失败、核验失败、第二条 chunk 插入由 SQLite trigger 真实拒绝，均保留 pending、NEEDS_REPAIR、revision=0，元数据没有半批 chunks；新服务重开后状态保持。模型准备/查询失败不改变旧库；空库和空 Markdown 明确报错。
- 跨进程：写进程走真实 service 导入，在 pending 后的 SDK 调用内暂停；另一进程的 search 确实等待同一 OS 锁，其他知识库的 status 正常。强制终止写进程后 reader 获锁，仍因 UPDATING 拒绝查询；pending.json 和原件保留。该用例的向量后端/编码为明确测试替身，实际模型+Milvus 路径由上面的独立 CLI 验收覆盖。
- 连接失败独立检查：已导入临时库后，另一个服务对象连接 `http://127.0.0.1:1`，2.042 秒返回 MilvusException；本地 status 仍为 READY / revision=1 且与失败前完全一致，无虚假检索结果。未停止或重启共享容器。
- 测试集合均按本次创建的明确名字删除，并确认自身集合不存在；没有清理他人集合。真实 CLI/连接失败验收库的原件副本、SQLite、向量和临时 YAML 已由各自 TemporaryDirectory 清理。正式 pytest 产生的临时数据和三份临时脚本清理被自动审批拒绝，准确残留见下文。保留本 worktree `.venv`、原有 HF 缓存、健康容器和已授权 WSL 存活会话；未操作主仓或源样例。
- S2 没有 update/remove/retry 命令，来源内容变化会明确拒绝并提示更新属于 S3。SDK 的 delete_document 仅为 K11 的存取能力，未伪装成可用业务删除。S3 必须保留每代原件的精确映射，再实现历史读取/更新；不能在覆盖 documents.original_path 后丢失旧代原件位置。
- 未验证：恢复流程、PDF/DOCX、TUI/Agent/Remote、回答和报告、GPU、其他模型、长时压力/大语料和掉电恢复。硬退出发生在 pending 登记前可能留下未登记准备目录，当前不会阻塞下次导入；本阶段不提供自动孤立材料回收，也不提前实现 S3。

leader 独立功能验收：使用 `E:\WorkBin\Recipe\docs\recipe-manifest.md` 和真实 Qwen/Milvus，独立执行 create/import/reimport/search/status 全部成功，6 chunks / revision=1。问题“审核通过是否就能直接标记为 Verified？”命中 74–76 行，字符范围与保存原件一致；重复 import 指向空 `HF_HOME` 仍成功，未加载模型。源文件 SHA256 前后不变，leader 自建集合、目录及脚本均已清理。该次复跑已包含唯一导入目录的更改。

leader 独立回归命令为 `python -m pytest tests/test_knowledge_service.py tests/test_knowledge.py tests/test_mcp.py tests/test_commands.py tests/test_agent.py tests/test_memory.py tests/test_permissions.py -q`：**215 passed / 4 skipped，10.08 秒**。跳过三项未 opt-in 的外部集成和现有系统符号链接用例；与实现对话的测试有重叠，不累加计数。另用真实 tokenizer 检查 CRLF、长中文、罕见汉字、emoji 和代码块，24 个片段的 token 上限、原文覆盖、字符定位和稳定 ID 全部通过。

代码质量验收通过：继续复用配置和 LocalEmbedding，原件/解析/分块集中在一个具体文档模块，SQLite 和 Milvus 各保留一个实现，CLI 直接调用同一服务；未加入多后端框架或重复校验层。leader 发现的真实 SDK 参数问题已修复并独立复验，通过后按明确文件范围提交并合入主目录。

**S2 当时的清理阻塞（2026-09-20）**：已核对目标路径后，PowerShell 批量删除本阶段临时目录/文件的命令被自动审批以 `blocked by policy` 拒绝。随后缩小为本 worktree 内三个明确临时脚本的 `Remove-Item -LiteralPath ... -Force`，同样被拒绝，未给出具体原因。S2 未更换删除工具或绕过审批；当时记录的残留如下，后续实际变化见 S3 清理更正：

- 本 worktree `.codeplus/s2_accept.py`、`.codeplus/s2_lockfile.py`、`.codeplus/s2_unavailable.py`。
- 本 worktree `.codeplus/s2-pytest/`、`.codeplus/s2-regression/`、`.codeplus/s2-final-sdk/`，仅包含本阶段正式测试的临时合成数据。
- `C:\Users\18221\AppData\Local\Temp\pytest-of-18221\pytest-11`、`pytest-12`，本阶段早期服务测试目录；`pytest-10` 已由 pytest 自身清理。

工作树内上述残留均受既有 Git 忽略规则覆盖，没有暂存或纳入代码 diff；不是正式产品文件，也不是应长期保留的交付物。功能实现和实测已完成，最终验收仍需保留这一清理未完成边界。

## S3 更新、删除、恢复和 PDF/DOCX（K16–K20）

实现 worktree：`C:\Users\18221\.codex\worktrees\68fd\CodePlus`；起点为已验收 S2 提交 `255be654a93afd5cd523b978a4696e7c26c51a22`。**2026-09-20 已通过 leader 独立功能与代码质量验收，纳入 S3 提交。** 清理限制单独列于本阶段末尾。

### 入口与复用

`import` 现在按同一来源路径替换文档，`update` 是同一 CLI 分支的别名。重复导入未变化且未删除的文件返回 `unchanged: true`，不加载模型；重新导入已删除文件可恢复检索。`remove` 使用 status 中的 doc_id，重复移除返回 unchanged，不增加 revision。READY 且无 pending 的 `retry` 也为 unchanged；其他情况只恢复已登记的唯一目标，不扫描原件目录、推断 PID 或自动放行查询。

以下 `$configPath`、`$kbId`、`$docId`、`$chunkId` 分别来自自己的配置和命令结果；本阶段已用含空格配置/原件路径逐个执行这些入口：

```powershell
uv sync --locked --extra knowledge
$env:HF_HUB_OFFLINE = '1'
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath update $kbId 'C:\资料\说明.md'
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath import $kbId 'C:\资料\报告.pdf'
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath import $kbId 'C:\资料\手册.docx'
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath remove $kbId $docId
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath retry $kbId
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath source $kbId $chunkId
& .\.venv\Scripts\python.exe -m codeplus.knowledge --config $configPath status $kbId
Remove-Item Env:HF_HUB_OFFLINE
```

`source` 只读取本库已保存片段、来源范围和该代原件的精确路径，包括已更新/删除文档的历史片段。该读取不加载模型或连接 Milvus，未扩展成 S4 的 Agent 引用工具。`status` 中 removed=1 的文档仍保留登记信息，但当前 chunk_count=0。

生产代码未新增模块或类。原件/解析/分块继续放在 documents.py；import、remove、retry 共用 service 的单一文档提交函数，SQLite 继续用原来的 begin_import/finish_import 事务入口，CLI 直接调用 service。

### 数据迁移和恢复语义

- schema 1→2 是同一 SQLite 事务内的加列与回填，仍只有 knowledge_bases、documents、chunks 三张表。新增 documents.removed、knowledge_bases.pending_operation 和 chunks.original_path；旧 chunks 从当时 documents 的原件路径回填。更新成功后只切换 documents 当前代次，不覆盖历史 chunks 的原件路径；重新导入历史相同字节时保持原 chunk ID 和旧代原件映射。
- Markdown 的 profile.json、profile_hash、generation 规则不变。PDF/DOCX 各自固定的解析器标识只参与对应文档代次计算；格式扩展不要求重建已有 Markdown 集合，也不迁移向量。
- 准备原件、解析、分块、编码及 pending.json 均在锁外；锁内重新检查文档登记是否变化，登记 pending 后删除该 doc_id 全部旧向量，完整行 upsert 并 verify_document，最后 SQLite 单事务登记 chunks、当前代次、removed、revision 和 READY。准备期同一文档被另一个写者修改会明确拒绝本次过时提交。
- 初次导入、更新、删除都使用这条提交路径。retry 读取登记的落盘材料，不重新读用户源文件、不重新编码；只验证保存原件哈希和目标/profile 后重放。正常异常保留 NEEDS_REPAIR，硬退出保留 UPDATING；重开后两者都拒绝检索。pending 未登记前的孤立 attempt 目录不会被当作恢复目标。
- S2 在创建库时 Milvus 失败留下的空库（revision=0、未登记任何文档、非 READY）由迁移明确登记 pending=create。恢复仅补完同一个集合的创建、缺失 dense 索引与 load；先核对既有 profile/维度，不删除重建已有集合。真实 SDK 在集合创建后、create_index 前抛错，确认集合存在且索引为空，再 retry 成功；没有手写状态模拟。
- 迁移后 S2 程序会拒绝 schema 2。需要回退程序版本时，应从升级前的数据备份恢复；本阶段没有设计自动降级或历史垃圾回收。

### 解析与真实来源

新增可选依赖 `pypdf==6.19.0`、`python-docx==1.2.0`，锁定传递依赖 lxml 6.1.3；原有包版本和平台约束保留。依赖按需导入，默认启动不会加载它们。PDF 逐物理页提取文本，SourceSpan.page 从 1 开始；DOCX 按正文段落和表格顺序读取，段落计数包含空段落，表格/行/列从 1 开始，保留 Heading/Title 路径，不提供页码。两者共用既有结构/offset 分块器。依据 [pypdf 提取说明](https://pypdf.readthedocs.io/en/stable/user/extract-text.html) 和 [python-docx 正文顺序 API](https://python-docx.readthedocs.io/en/latest/api/document.html)。

非 Markdown 的 char_start/char_end 是本次解析文本中的字符范围，原件定位依靠 PDF 页码或 DOCX 段落/表格行列；Markdown 仍为保存原文的字符范围和行号。空/全扫描 PDF、加密 PDF、损坏 PDF 和旧 `.doc` 明确失败，不提交元数据或向量。当前没有 OCR、PDF 复杂版面重建、Word 排版页码或页眉页脚/批注/嵌套表格承诺。

真实环境为 Windows Python 3.14.3、Milvus 3.0.1 / SDK 3.0.2、既有固定 revision Qwen/CPU float32/1024 维，使用 HF_HUB_OFFLINE=1 的已有缓存。只读取三个 `E:\WorkBin` 样例，导入前后源文件哈希相同，保存副本与源文件字节一致：

| 样例 | chunks / 本进程导入秒数 | 问题与命中 |
| --- | --- | --- |
| `Recipe\docs\recipe-manifest.md` | 6 / 15.659 | 审核通过能否直接标为 Verified：rank 1、cosine 0.564289，74–76 行 |
| `AIGC研究报告\deloitte-cn-dai-the-impact-and-significance-of-generative-artificial-intelligence-on-enterprises-zh-20230327.pdf` | 35 / 38.624 | 生成式 AI 技术栈三层：rank 1、cosine 0.817878，第 5–6 页 |
| `班级\测试管理\已完成\附件1：Midscene.js安装手册.docx` | 1 / 21.145 | Midscene.js 开源团队：rank 1、cosine 0.750437，答案在正文第 2 段，无页码 |

对应 SHA256 依次为 `63b6b97811bcf191dd88daf8aca45fb31250c4603eed833ac19242b66a2393c1`、`8da5e48ee93f87c95d8afad832a5f5507f7971acbcc40eded1bc7303ff5923df`、`c9d00f537bc122bd654953c37d649ed13e2f185f0e3f81dd7f91eea5ef477961`。合成两页 PDF 的 Zircon 772 / November 答案命中真实第 2 页；合成 DOCX 的 Saffron warranty / 813 dollars 答案命中标题路径及表 1、行 2、列 2，并验证表格前后段落顺序。上述是链路和来源定位证据，不是正式检索质量评测。

真实迁移/恢复库为 `af9e6a0fc9744166827b4a95016d9927`，集合始终为 `codeplus_kb_af9e6a0fc9744166827b4a95016d9927`。首先把 `255be65` 的真实 codeplus 代码通过 git archive 解到本轮临时目录，用其 S2 service 与真实 Qwen/Milvus 创建两个 Markdown 文档，确认 schema=1、revision=2，再用 S3 service 打开原库；profile_hash、旧 chunk ID、原件路径和查询结果保持不变，未修改向量。加入上述三个真实样例和两个合成格式样例后 revision=7。

随后分别在真实 service/SDK 执行点调用子进程 `os._exit(73)`，不执行 finally，也不手写数据库状态。每次重开都保留 UPDATING 并拒绝查询，然后显式 retry：

| 硬退出位置 | 中断时 A 可见向量数 | retry 后 A 向量数 | 成功 revision |
| --- | --- | --- | --- |
| 删完旧向量 | 0 | 4 | 8 |
| 新向量只写一半 | 2 | 4 | 9 |
| 写完并核验、尚未提交 SQLite | 4 | 4 | 10 |
| 删除向量后、尚未提交 removed | 0 | 0 | 11 |

每轮都比对 B 的全部向量行（含 dense）与初始值完全相同；成功后 A 只保留当前代向量或零向量，历史片段仍指向原始字节。retry 的 LocalEmbedding._model 始终为 None，重复 retry 不增加 revision。最后独立 CLI 执行 update 恢复历史相同内容、重复 import、source、remove、重复 remove、retry、status，最终 revision=13 / READY；历史 generation 与 original_path 恢复为 S2 原始值。

### 测试及清理边界

正式测试只扩展既有 `tests/test_knowledge_service.py`（新增一个格式测试函数，原有用例扩展更新/恢复）及 `tests/test_knowledge.py` 的默认禁用依赖检查。相关回归：**74 passed / 3 skipped，最终隔离目录复跑 4.37 秒**；skip 为未 opt-in 的 S1 两个真实集成及 S3 SDK 用例。真实 SDK 单独 opt-in：**8 passed / 208.75 秒**，其中测试编码器为 TinyEmbedding，Milvus/SQLite/子进程退出均为真实实现；真正 Qwen 的三格式和恢复验收另行记录，不把替身计为模型验证。`uv lock --check`、`uv sync --locked --extra knowledge --dry-run` 和 `git diff --check` 均通过。

运行正式测试时使用本轮 TemporaryDirectory 之内的独立 `--basetemp`；例如在 Python 临时目录上下文中调用 `python -m pytest tests/test_knowledge_service.py tests/test_knowledge.py tests/test_mcp.py tests/test_commands.py --basetemp <本轮新建临时目录>/pytest -q`。真实 SDK 只额外设置 `CODEPLUS_TEST_MILVUS_URI=http://127.0.0.1:19530`，不设置 S1 restart/model opt-in，也不重跑无改动的 S1 全量真实检查。

**S3 清理更正**：早期正式 pytest 按初始交接建议使用 `tmp_path_retention_count=0`，但未指定独立 basetemp，pytest 自身的全局保留策略因此影响了旧测试目录。leader 于本阶段只读核对确认 S2 旧 `Temp\pytest-of-18221\pytest-11`、`pytest-12` 已不存在；不能继续把它们列为当前仍在的残留。这不是一次获批的 S2 清理任务。397c worktree 的三个 `s2_*.py` 仍存在，S3 未操作它们或其他旧拒绝路径。收到限制后改为每轮 TemporaryDirectory 内独立 basetemp，仅由该上下文清理本轮目录，不进行旧路径删除补救或绕过审批。

本轮真实验收集合 `codeplus_kb_af9e6a0fc9744166827b4a95016d9927` 已删除并确认不存在；正式 SDK 测试同样在 finally 删除自身随机集合。真实验收的原件副本、S2 代码快照、SQLite、向量材料、临时配置及子进程脚本均随 TemporaryDirectory 清理，并断言本轮目录不存在。保留本 worktree 原生 Windows .venv、已有 HF 缓存、共享服务和已授权 WSL 会话。没有改动真实样例、重启容器或操作 Docker Desktop。

**S3 本轮清理阻塞**：任务结束时尝试用 PowerShell 核对解析后的绝对目标路径，再以 Remove-Item 删除本 worktree `.codeplus/s3_acceptance.py`、`.codeplus/s3_checks.py`。整条命令在执行前被自动审批以 `blocked by policy` 拒绝，没有更具体原因。两个脚本仍保留在 `C:\Users\18221\.codex\worktrees\68fd\CodePlus\.codeplus\`，受既有 Git 忽略规则覆盖，没有暂存或纳入 diff；不是正式交付文件。未缩小目标重试、未改用其他工具删除，也未标为已清理。这与上述 TemporaryDirectory 和自建集合已成功收尾是不同事实。

leader 独立验收：实际运行主目录 S2 代码，在真实 Milvus 和 Qwen 上创建 schema 1 双文档库，再用 S3 代码原地迁移到 schema 2；保持三张表、集合名、旧片段和原件映射。A 更新后新期限可检索，旧引用仍指向旧原件；在真实 Milvus 删除 A 后注入中断，查询被 NEEDS_REPAIR 阻止，新服务 retry 成功且模型未加载。随后删除 A、重复删除不增 revision，B 的全部向量行与 S2 基线完全一致，最终 revision=5。该独立测试集合、目录和脚本已清理。

leader 另在本轮独立 TemporaryDirectory 下运行两份 knowledge 正式测试：8 passed / 3 skipped，2.36 秒；三项跳过为未 opt-in 的外部集成。代码审查取消了原拟新增的代次表，复用 chunks 保存原件映射；并发现、修正及真实验证了建库缺索引的恢复缺口。功能与代码质量均通过，按明确文件范围提交后合入主目录。

未执行 S4 的 TUI/Agent/会话/引用报告，也未测试 GPU、长时压力、大语料、OCR 或系统掉电；真实进程死亡恢复不等同于掉电持久性保证。

## S4 TUI、Agent、会话与引用报告（K21–K27）

实现 worktree：`C:\Users\18221\.codex\worktrees\dc17\CodePlus`，基线 `cfd1a8ea0626629f78e99cabedf517f4e0e07fbd`。**已通过 leader 独立功能与代码质量验收，本地提交 `7861da9`；独立证据见本节末尾。**

### 使用方式与复用范围

先在自己的既有配置启用 `knowledge.enabled: true` 并指定数据目录，仍使用 S1–S3 的模型/profile 和 Milvus 配置。未启用时不会创建服务、加载模型或连接 Milvus。正常 TUI 启动分支只多传入 KnowledgeConfig；没有新增 `-p` 选择参数或 Remote 接线。

```text
/knowledge create "个人资料"
/knowledge use <create 返回的 kb_id>
/knowledge import "C:\资料目录\含空格的文件.md"
/knowledge import "C:\资料目录"
/knowledge status
/knowledge sources
/knowledge remove <sources 返回的 doc_id>
/knowledge retry
/knowledge open K:<kb_id>:<chunk_id>
/knowledge open K:<kb_id>:<chunk_id> <next_offset>
/knowledge off
```

create 自动选中新库；use 使用稳定 ID，避免名称歧义。import 接受单文件或目录，后台逐文件调用现有 import_document，显示 `1/N`、当前文件及完成/未变化/失败；目录扫描也在线程中执行。路径作为整个参数余部处理，保留 Windows 反斜杠及空格。sources/status/remove/retry 直接调用 S3 服务，没有第二份导入或状态机。

只新增三个生产模块：`knowledge/citations.py` 管当前绑定、本轮证据和引用记录；`tools/knowledge.py` 容纳两个既有 Tool 子类；`commands/handlers/knowledge.py` 容纳具体管理命令。S3 service 仅增加读取同代相邻片段的方法。SQLite 仍三张表，不新增版本表、HTTP 服务或专用报告 Agent。

- SearchKnowledge 仅接受 query 和 1–10 的 top_k，拒绝额外参数；当前库由应用绑定。每轮首检一次，最多三次补查。run 与 run_to_completion 共用首检函数，经现有工具注册、启停及权限检查执行；显式 deny 也约束自动首检。内部 MCP/system-reminder 不作为查询问题。
- 原文经标准 ToolUse/ToolResult JSON 提供一次，系统提示只含稳定规则。资料正文、历史回答、会话摘要与项目指令/autoMemory 分开；知识模式不自动提取/整合个人记忆，也不注入异步记忆召回。文档内说明和提示词只作不可信数据。
- ReadDocument 仅接收本轮提供的引用，读取精确代次片段及最多前后各一片。单次正文预算 6000 字符；长文显示 truncated、next_offset，继续读不会把邻块文字冒充为锚点文字。邻块有独立引用。用户 open 可按保存的稳定引用读取历史原文，包括关闭模式、更新、移除或会话压缩之后；这不会把历史引用授权给模型本轮回答。
- SessionMeta 只增加可选 `{kb_id, top_k}`，不保存连接。use/off 同步保存并使用现有 compact_boundary 清空回答上下文；磁盘旧记录仍保留。new/clear 关闭模式；resume 恢复绑定，旧文件缺字段为关闭。恢复缺失/待修复库明确提示，后续提问也拒绝，不能静默退回普通回答。
- 同一 TUI 中库操作与回答互斥，运行中不允许切库/切会话；本地检索正在执行时不取消底层编码线程，完成检索后可正常中断模型回答。导入期间输入仍能编辑；不承诺退出应用后继续导入。
- 每次补查、写报告及最终回答均核对语料 revision；变化则提示重新生成。模型正文在机械引用检查后显示，未提供的引用报错。Markdown 报告仍由原 WriteFile 写入，保留权限、读后覆盖和文件状态检查，并追加引用 ID、原位置、generation、revision。权限/Hook/写入失败经既有工具结果路径记录，不能宣称报告已完成。引用存在性不等于事实支持性。

### 真实模型、服务、provider 与 TUI

本 worktree 使用独立原生 Windows `.venv`，`uv sync --locked --extra knowledge`；使用原有离线 Qwen3-Embedding-0.6B 缓存（固定 revision、CPU float32、1024 维）与 S3 的本机 Milvus。回答只读加载 `D:\CodePlus\.codeplus\config.yaml` 的现有 `deepseek-chat / openai-compat`；先实际请求并收到 `OK`，配置 SHA256 前后相同，未复制/输出密钥。整个阶段一次仅运行一个真实模型验收进程，未重启共享容器或操作 Docker Desktop。

最终临时库 `696b2aee3c0943b5babb5688e71dbe23`，三份合成资料各 1 chunk，revision=3。通过真实 `CodePlusApp.run_test` 的 Textual Pilot 向 ChatInput 输入命令、按 Enter，再走实际 dispatcher、handler、Agent、Qwen、Milvus 和回答 provider；这是 headless TUI 交互，不是可视桌面终端验收，也不是只测 handler。

| 资料 | 原文事实 | 引用位置 | 手工核对结果 |
| --- | --- | --- | --- |
| Atlas policy.md | 上限 73 元；17 个日历日内交票据 | 第 3 行；chunk `617f801f…` | 已知答案及报告均一致 |
| Boreal policy.pdf | 上限 91 元；23 个日历日内交票据 | 物理第 1 页；chunk `62cabcd4…` | 报告一致；没有手机号资料 |
| Cedar policy.docx | 上限 108 元；文件没有规定交票据期限 | 正文第 2 段；chunk `7fd8c389…` | 报告明确“缺少信息”，不生成页码 |

- 实际 create/import 三格式共 70.896 秒（含初始化和模型加载）；导入期间在输入框按键得到 `type`，没有冻结。完整验收 93.288 秒，是小样本单次链路时间，不是性能指标。
- 已知问题经实际 TUI/Agent.run 返回 73 元和 17 个日历日。无依据手机号问题经同一 Agent.run_to_completion 明确“资料未提供”，没有编造号码。检索失败、no_hits、命中但证据不足分别由错误/状态 JSON/回答表现；真实本轮未人为关闭共享 Milvus，连接失败正式回归使用明确替身。
- 默认权限下要求 WriteFile 写报告，真实 provider 调用了工具，原 PermissionChecker 拒绝，目标文件不存在，最终错误为 `Report was not saved with verified citations: Permission denied: non-interactive agent cannot prompt user`。随后验收代码显式切到 acceptEdits，再实际保存三方比较 Markdown；知识模式本身从不修改权限模式。
- 报告金额 73/91/108、期限 17/23/缺少信息与原文逐项一致。三条唯一引用全部由实际 `/knowledge open` 打开，核对其保存原件、解析文本、SourceSpan 字符范围以及行/页/段落，generation 和 revision=3 写入报告。报告结尾对表格事实的复述未重复每个行内引用；其事实支持性由上表人工核对，不声称机械系统做了语义验证。
- Cedar 第 3 段含合成伪指令，模型将它引用为可疑文档内容，没有按其要求改变任务或把暗号当作答案。此处只记录一个样例行为，不当作通用提示注入安全证明。
- 实际 Pilot 完成 create/use/import/status/sources/remove/retry/off/open 及 session new/resume/clear，errors=[]；off 后历史引用仍可打开，普通 provider 请求返回 `OFF_OK`。源样例哈希不变。仅用合成小样本，本阶段未重复导入 `E:\WorkBin` 个人正文。

### 回归、发现与清理

正式测试仅在原 `test_knowledge_service.py` 添加三个关键业务函数，并更新 `test_commands.py` 注册预期。包含实际 SQLite 来源隔离、ReadDocument 截断续读、旧代原文、跨轮引用拒绝、revision 变化、两 Agent 入口、首检权限 deny、MCP 提醒不作为 query、报告写入拒绝/成功、真实 App Pilot 输入/后台导入/首次回答/绑定恢复。普通 Agent、会话、权限和默认关闭回归沿用原测试。

在本轮 TemporaryDirectory 的独立 `--basetemp` 中运行：

```text
python -m pytest tests/test_knowledge_service.py tests/test_agent.py tests/test_commands.py tests/test_memory.py tests/test_permissions.py tests/test_knowledge.py tests/test_mcp.py --basetemp <本轮临时目录>/pytest -q
```

最终 **219 passed / 4 skipped，12.16 秒**。skip 为未 opt-in 的 S1 两项真实集成、S3 SDK 用例及既有系统符号链接用例，不计为通过。`uv lock --check`、`uv sync --locked --extra knowledge --dry-run`、`git diff --check` 通过；未增加依赖或修改锁文件。

首轮真实 TUI 暴露自动检索 ToolUse 先于正文时 streaming_label 已为 None 的问题，修复为收到首段正文时重建文本组件，已在同一个 Pilot 用例覆盖。后续验收脚本的 CRLF/LF 比较及“警告中也不得提及伪指令暗号”两个过强断言已修正：使用原解析文本核对位置，事实/伪指令行为单独人工核对。不能把这些中间失败算成功，也不能把断言修改说成产品修复。

最终集合 `codeplus_kb_696b2aee3c0943b5babb5688e71dbe23` 已删除并查询确认不存在；最终 `codeplus-s4-live-vm5tsn4a`、另外两次正常退出验收目录和各轮独立 pytest 目录均已由自己的 TemporaryDirectory 收尾。第一轮失败库的集合 `codeplus_kb_9c9e70313dde420c845b4a46e74b8410` 也已删除，但第一轮目录清理先遇到未关闭 session 句柄的 WinError32；后续脚本将 session.close 移入 finally，避免重复。

**S4 自动审批拒绝清理的残留**：句柄错误后，对明确的本轮目录 `C:\Users\18221\AppData\Local\Temp\codeplus-s4-live-ha52kl2a` 发出核对绝对路径的 PowerShell Remove-Item，执行前被 `blocked by policy` 拒绝。任务收尾再对已核对的本 worktree `.codeplus/s4_provider.py`、`.codeplus/s4_acceptance.py` 发出精确文件删除，同样在执行前被拒绝，未给出进一步原因。三个目标仍保留；没有更换删除工具或绕过审批，旧 S2/S3 被拒绝路径未操作。两个脚本受 Git 忽略，没有暂存；Temp 残留只含合成资料及该轮会话/登记材料，不是正式交付文件。

保留本 worktree `.venv`、既有 HF 缓存和共享服务。未验证 S5 CLI/Remote、S6 评测、大语料/长时压力、可视桌面终端布局、多用户、GPU/OCR 或对所有提示注入的防护。

leader 独立验收通过：另建真实 Qwen/Milvus 临时库，使用现有 deepseek provider，流式 Agent.run 在含 MCP 内部提醒的会话中正确回答 43 天/267 元并给出可读原文引用；run_to_completion 对未提供的手机号明确说明缺少依据。更新为 52 天/310 元后，旧轮回答被 revision 检查拒绝，旧引用仍能读取 43 天的历史原件。该库、目录和独立脚本已清理，真实 provider 配置哈希未变。相关回归独立复跑 219 passed / 4 skipped（11.34 秒）。审查修正首检权限、MCP 问题识别、证据重复注入和写入拒绝后的完成判断；复用现有工具、会话和写文件路径，功能与代码质量通过后本地提交。

## S5 非交互 CLI 与 Remote（K28–K29）

实现 worktree：`C:\Users\18221\.codex\worktrees\b379\CodePlus`，基线 `7861da95083d9dda681376f40954c061f513a38b`。**已通过 leader 独立功能与代码质量验收，本地提交 `518519a`；独立证据见本节末尾。**

### 使用方式与入口行为

在自己的既有配置中启用 knowledge 并指向已导入的同一数据目录，然后使用 create 返回的稳定 `kb_id`：

```powershell
$kbId = '<create 返回的知识库 ID>'
uv run --extra knowledge codeplus -p "根据资料回答报销上限及期限，并标明来源" --knowledge $kbId
uv run --extra knowledge codeplus -p "根据资料回答报销上限及期限，并标明来源" --knowledge $kbId --output-format stream-json
uv run --extra knowledge codeplus --remote
```

`--knowledge` 只用于 `-p`，缺少 `-p` 时由现有 argparse 报错。不指定时保留普通 `-p` 行为。绑定后直接使用 S4 KnowledgeContext、SearchKnowledge/ReadDocument 和 Agent.run：首检仍发出原契约的 tool_use/tool_result（同一 tool_id），引用仍由既有代码校验。text 只输出最终正文；stream-json 的 stdout 每行均为 JSON，模型/SDK 诊断转到 stderr。知识检索或回答失败返回非零退出码，不发送成功 result；库不存在、配置禁用和服务不可达分别保留实际错误。

知识模式不改变 PermissionChecker：显式 deny 阻止首检，需要 ask 的操作在非交互知识模式下拒绝。普通 `-p` 的既有权限策略未扩展。资源在整个已有 Agent/后续团队处理结束后关闭；同一个 finally 也覆盖 MCP 的原有提前 return 出口。

Remote 的聊天框使用 S4 同一套 `/knowledge create/use/import/status/sources/remove/retry/open/off` 命令。`/knowledge import "C:\资料目录\含空格的文件.md"` 中的路径指向**运行 CodePlus 的服务器本地文件系统**，相对路径以服务端工作目录为基准；界面导入消息也明确说明这一点，没有浏览器上传。

- 每个库命令在原 WebSocket 消息后台任务内执行，handler 继续把耗时库操作交给线程。导入进度、完成及逐文件错误沿用 system 消息，在 command_done 之前发送完毕。
- 回答和命令互斥；忙时新请求收到提示，不排队执行旧提问、切库、导入或会话操作。WebSocket 读循环仍能接收 ping 和权限响应。
- 复用 SessionMeta.knowledge_binding、Session.append 和 compact_boundary；use/off 清空当前回答上下文而保留旧记录及历史原件。session new/clear 创建新会话并关闭知识模式；resume 恢复绑定及已有消息。clear 直接使用既有 handler，不另建会话系统。恢复画面复用现有 HTML 的 replay_user/replay_assistant 消息。
- `open` 可在切库/off 后读取历史来源，不将其加入新库的本轮证据；回答仍通过原 Agent 的首检、ReadDocument 范围与引用校验。

### 真实验证及配置边界

本 worktree 新建独立 Windows `.venv`，执行 `uv sync --locked --extra knowledge`。使用已有 Qwen3-Embedding-0.6B 固定 revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` 的离线缓存（CPU float32、1024 维，HF_HUB_OFFLINE=1）和共享 WSL Milvus；healthz 返回 OK，没有重启容器或操作 Docker Desktop。

回答 provider 只读加载 `D:\CodePlus\.codeplus\config.yaml`。Remote 在内存配置中启用独立临时知识目录，启动真实 `_init_agent` 与 WebSocket `_ws_handler`；通过实际 TCP WebSocket 发送消息，未替换 handler、Agent、Embedding、Milvus 或回答客户端。CLI 每次启动独立 Python 子进程，**仅替换 load_config 返回的内存配置，再执行真实 main/argparse**；未修改或复制真实密钥配置，未宣称磁盘原配置已经启用知识库，配置文件 SHA256 前后相同。

两个合成 Markdown 各 1 chunk、各库 revision=1，完整真实链路单次耗时 **119.254 秒**（包括模型加载及不可达连接超时，不是吞吐指标）：

| 入口/操作 | 实测结果 |
| --- | --- |
| Remote 创建、导入 Magpie 库并 use→提问→open | 回答 186 元、29 个日历日；引用指向保存的 Markdown，事实位于第 3 行 |
| Remote 导入过程中同时发送 off、问题、session new、ping | 三个业务请求被忙提示拒绝，绑定/会话不变；ping 0.001 秒返回，早于导入完成 |
| Remote 缺文件导入及 use missing | 逐文件导入失败可见；坏库命令返回 error，原绑定不变 |
| Remote 回答期间 off/ping | off 被拒绝，ping 正常返回；完成后问答仍引用原库 |
| Remote session new/resume | 恢复原绑定与含引用的历史回答，已有 replay 消息被发送 |
| Remote 切换 Kestrel 库再问答 | 回答 275 元、41 个日历日；本轮引用/证据只含新库 ID，旧库引用仍可 open |
| Remote off→open→resume→普通提问→use→clear | 历史原文可核对；恢复 off 状态不复活旧证据；普通回答 OFF_OK；clear 新建会话且关闭模式 |
| CLI text / stream-json | 都实际回答 186 元、29 天并带可追溯引用；退出 0。JSON 输出共 5 行、逐行 json.loads 成功，加载日志仅在 stderr |
| CLI 未指定 knowledge | 原回答 provider 返回 PLAIN_OK，stdout 无知识工具事件、stderr 为空 |
| CLI missing / ../missing / disabled / 不可达 | 均退出 1，text 错误只到 stderr，JSON 错误 3 行且无 result；不可达使用独立地址 127.0.0.1:1，未停止共享 Milvus |

本阶段不重复导入前序已验的三种格式，不读取 `E:\WorkBin` 真实正文。没有运行桌面浏览器的可视验收；上述是实际 WebSocket 协议链路，不将它表述为浏览器上传或多用户隔离验证。

### 回归与清理

只在现有 `tests/test_knowledge_service.py` 添加两个入口回归函数，复用 TinyEmbedding/MemoryStore 与真实 SQLite：CLI main/argparse 的输出、退出码、权限 deny/ask、报告拒绝；真实 WebSocket 的后台导入、进度/错误、忙期间 ping、会话恢复及证据范围。PROMPT 交接在测试中注册既有 REVIEW_COMMAND 后通过；该命令原本不在 Remote 生产注册表，未借本阶段扩大注册或 /plan 功能。这些替身回归与上面的真实模型验收分别记录。

在本轮 TemporaryDirectory 的独立 `--basetemp` 执行：

```text
python -m pytest tests/test_knowledge_service.py tests/test_agent.py tests/test_commands.py tests/test_memory.py tests/test_permissions.py tests/test_knowledge.py tests/test_mcp.py tests/test_clear.py --basetemp <本轮目录>/pytest -q
```

最终结果 **224 passed / 4 skipped，13.76 秒**。四项 skip 为未 opt-in 的真实集成及已有系统符号链接用例，不计通过。首轮为 221 passed / 4 skipped / 3 failed（15.54 秒），三项失败均来自 `test_clear.py` 的 MockAgent 缺少 work_dir；将阶段 HEAD 通过 git archive 放入独立临时目录重跑，同样 3 failed（0.69 秒）。leader 进一步追溯确认是本系列 S4 将 clear 调用对齐真实 Agent.work_dir 时遗漏同步测试夹具，不能作为本任务无关问题排除。本阶段仅将 MockAgent 的 `_work_dir` 改为 `work_dir` 一行，同步已有契约，未在生产增加 fallback；随后上述完整相关回归全部通过。新入口两项也曾单独执行 **2 passed，2.85 秒**；真实 Remote 使用实际 Agent 的 clear 通过。`uv lock --check`、依赖同步 dry-run 及 `git diff --check` 通过，未增加依赖或修改锁文件。

真实集合 `codeplus_kb_fb23ae0e00104620a33c9ed19b71a147` 与 `codeplus_kb_12904faf85e544e2b6af8caeccdaca9d` 已删除并逐一确认不存在。真实验收目录 `C:\Users\18221\AppData\Local\Temp\codeplus-s5-live-80rdtrp1`、各轮 pytest/baseline TemporaryDirectory、子进程配置注入脚本和外层自清理脚本均已清理；先关闭 session/SDK/文件句柄再收尾。没有产生新的 policy 拒绝残留，未触碰 S2/S3/S4 的已记录残留。保留本 worktree `.venv`、既有模型缓存和共享服务。

未验证 S6 评测、浏览器视觉布局、大语料/长时压力、GPU/OCR、多用户隔离及知识模式下的团队协作全链路；后者仅保留既有调用和正确资源生命周期。本阶段未新增服务层、报告 Agent、校验层或会话系统。

leader 独立验收：新建真实 Qwen/Milvus 单文档库，以独立子进程执行真实 CLI main/argparse，仅在内存注入配置；stream-json 每行均合法，首检工具事件与最终结果齐全，现有 provider 正确回答 Orion 保修 46 个月并引用来源。无效库退出 1，只输出工具失败与 error，无成功 result。自建集合、目录及独立脚本已清理，真实配置哈希不变。七文件回归独立运行 221 passed / 4 skipped（15.91 秒），同步修正后的三个 clear 用例独立运行 3 passed（0.44 秒）。代码审查修正资源提前关闭，并追溯补齐 S4 遗漏的测试替身字段；未新增业务模块，功能与代码质量通过后本地提交。

## S6 固定检索实验（K30–K33）

2026-09-20：实现和真实实验完成，leader 独立功能与代码质量验收通过，纳入本阶段本地提交。仅扩展原有独立 CLI、MilvusStore，增加一个具体 evaluate.py、两项算法测试与一项 opt-in 真实重放测试。日常 service、配置、Agent 权限、集合绑定保持原契约；没有评测框架、日常审计层或迁移系统。K34/K35 未实现。

### 运行与冻结

使用本 worktree 原生 Windows `.venv`，`uv sync --locked --extra knowledge`。仓库样例是 `tests/fixtures/knowledge/`；wheel 不包含 tests，安装后需显式提供自己的样例目录或冻结文件。示例在 PowerShell 中运行：

```powershell
$env:HF_HUB_OFFLINE = '1'
uv run --extra knowledge python -m codeplus.knowledge evaluate --fixtures tests/fixtures/knowledge
uv run --extra knowledge python -m codeplus.knowledge evaluate --replay '<上次输出的 frozen.json 绝对路径>' --mode hybrid --ef 16 64
Remove-Item Env:HF_HUB_OFFLINE
```

`evaluate` 不需要回答模型或 provider YAML；未传 `--config` 时显式采用本地 KnowledgeConfig 默认值。若传已有 `--config <YAML>`，仍复用现有加载器并要求该配置启用 knowledge，但不调用回答模型。`--fixtures` 和 `--replay` 必选其一。实验数据固定写到本次工作目录的 `.codeplus/knowledge/experiments/<UTC时间_随机后缀>/`，受既有 Git 忽略规则覆盖，不写日常 data_dir。

- `frozen.json`：完整 UTF-8 原文、每份原件 SHA256、原文字符范围/quote 标注、profile、真实分块来源映射、文档向量、问题向量及查询编码耗时。冻结来自真实 KnowledgeService 导入、source 读取和 Milvus 行读取，不自制另一套分块/来源映射。保存后不依赖已删除的临时路径。
- `report.json`：稳定 corpus_sha256、标注/profile/向量校验和、SDK/服务/运行版本、实验集合 schema、索引及 loaded segment、两边 readback 校验和、各问题原始 SDK 分数/排名、范围过滤、分词、预热/正式轮、失败、时间与资源。运行异常也写错误报告；正常返回退出码 0，请求失败等为 1，参数错误为 2。
- `corpus_sha256` 仅由排序后的文件名和原文 SHA256 计算，排除每轮随机 KB 派生的身份；frozen/rows 哈希标识实际冻结内容。重放采用冻结 profile，即使当前模型配置不同也不把当前标签写成向量来源，不加载 Embedding，也不重读 fixtures。每次新建实验索引，改变同轮的 ef 只发查询，不重建。

固定集有 16 个中英文问题：14 个有答案问题共 18 处证据，另有两题明确无答案；q14 同时要求五份文档中的事实，K=3 时用于观察漏检。5 份短主题文档加 1 份 1040 条虚构展品的背景目录，实际共 1066 chunks；全部公开合成，无 WorkBin 正文、配置或模型缓存入 Git。局部 `.gitattributes` 固定样例 LF，避免 Windows checkout 改换行后破坏字符标注；不改变用户 Markdown 的读取规则。

### 指标与检索约定

Evidence Recall@K 的固定分母是 18 个标注原文区间，不是 chunk ID、问题数或返回片段数。每个区间按同文件前 K 个结果的 source_spans 并集计算，必须覆盖 `[char_start,char_end)` 全部 Unicode 字符；有缺口或只部分覆盖均为零。允许多个片段共同完整覆盖。失败贡献零并留在固定分母，另报成功请求分母/召回和错误数；两题无 gold 为 null，单独统计有没有返回，绝不记成召回 1。证据汇总使用首个正式轮，每个问题仅计一次；所有重复轮原始结果保留。

ANN Recall@K 单独计算：分母为同一文档范围内 FLAT 实际返回的前 K 个不同 ID 数，分子为 HNSW 前 K 的交集数；不足 K 时不额外扣分，FLAT 空返回或请求失败时参照不可用。它衡量向量近邻，不等同于找全原文。SDK 返回的 FLAT ID 是精确参照；边界同分时未另行扩展全部并列近邻。

默认 K=3，两路各取 6 个候选；`--mode all` 输出 dense、bm25、hybrid，选择单一模式只减少相应证据路输出，所有模式仍运行 FLAT/HNSW 对照。HNSW 构建参数 `M=16, efConstruction=128`；查询 `ef=16,64`。RRF 等权使用一基排名 `1/(60+dense_rank)+1/(60+bm25_rank)`，缺席项贡献零，平分按 chunk ID 排序，保留两路原始排名和分数。一次有效 BM25 空返回为 no_hits，可与 dense 正常融合并记录路状态；任一路异常则 hybrid unavailable，不把单路降级写成成功混合。

两路共用参数化 `doc_id in {doc_ids}`；q06/q08/q13/q16 的文档范围分别约束质量、运维、园艺和差旅，返回来源逐项核对。BM25 使用真实 Milvus `FunctionType.BM25`、`SPARSE_FLOAT_VECTOR`、`SPARSE_INVERTED_INDEX`、`DAAT_MAXSCORE`、k1=1.2、b=0.75。text analyzer 为 Jieba search/hmm=false + lowercase；没有 Python BM25。依据 [官方全文检索](https://milvus.io/docs/full-text-search.md)、[Jieba](https://milvus.io/docs/jieba-tokenizer.md) 和 [HNSW](https://milvus.io/docs/hnsw.md)，实际以本机 SDK 3.0.2 / 服务 3.0.1 验证。

### 本轮真实结果

最终报告目录：`C:\Users\18221\.codex\worktrees\7aef\CodePlus\.codeplus\knowledge\experiments\20260919T173947Z_e1ff51df\`。其中 `frozen.json`、`report.json`、`server-evidence.json` 可复核；摘要如下，报告与向量不提交 Git。

| 冻结项目 | 值 |
| --- | --- |
| 稳定语料 SHA256 | `77f3f5d466bdf759d6a480e0e48917670ea2da74b206015f25068af2fdb5ce46` |
| 标注 SHA256 | `24b7293cc3490dfb2231580ced81f9085abc4335e0519e60d7c182169c6f6e56` |
| 完整冻结 SHA256 | `05e37acd1a23b991df10ffda20d09ebf41c782963d5d8f69914d2a0e762df8fb` |
| 文档向量行 SHA256（两实验库 readback 均相同） | `3b4bca92782a8fb4bf69862ca7807f98d714610feb8e178149b7e20cc2030ce5` |
| 查询向量 SHA256（两库所有 ef 共用） | `3a6251eabd6a32dab99fcccffd5a87b7fa15a03bfc9e2dece2947150139a3752` |
| 模型 / revision | Qwen3-Embedding-0.6B / `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| profile / 分块 | CPU float32、1024 维、L2；512 tokens / overlap 64；原有 structure-offsets-v1 |

预热完整 16 题一轮，正式 3 轮，每路 48 次；单客户端顺序发送 FLAT、BM25、HNSW ef16、ef64。耗时包含 SDK 请求与冻结来源映射，不包含编码/构建/预热；hybrid 是两路顺序耗时加融合。百分位按 `(n-1)*p` 线性插值。

| 检索路 | Evidence Recall@3 | ANN Recall@3 | P50 / P95（ms） |
| --- | --- | --- | --- |
| FLAT dense | 16/18 = 88.89% | 精确参照 | 2.575 / 2.996 |
| Milvus BM25 | 12/18 = 66.67% | 不适用 | 2.154 / 2.600 |
| RRF hybrid | 15/18 = 83.33% | 不适用 | 4.642 / 5.384 |
| HNSW ef16 | 13/18 = 72.22% | 111/144 = 77.08% | 2.078 / 2.426 |
| HNSW ef64 | 15/18 = 83.33% | 129/144 = 89.58% | 2.097 / 2.492 |

正式及预热请求均无异常；无 gold 两题不参与证据召回。BM25 共两题正常空返回：无答案 nonce q15，以及中文问题检索英文原文的 q08；q16 在限定差旅范围仍返回无关材料，不能视为已回答。BM25 还漏掉英文询问中文票据的 q03。q14 五处证据分别覆盖 dense 3/5、BM25 1/5、hybrid 2/5。因此本集 RRF 未提高 dense 召回，未作“混合一定更好”的结论。

服务端 analyzer 输出包含：差旅报销 → `差旅, 报销`；APQP → `apqp`；QMS-204 → `qms, -, 204`；SLA → `sla`；API-429 → `api, -, 429`；NPK-712 → `npk, -, 712`。完整问题分词包含空格和标点 token，按实际输出保留，未把编号说成单一 token。上述缩写/编号的 q04/q05/q06/q09/q10/q13 均检查了真实 BM25 命中。

**HNSW 实际执行证据与小集合陷阱**：最初 25 chunks 在 `describe_index` 上也显示 `indexed_rows=25/total_rows=25/Finished`；loaded segment 同样有非零 index_id 和 HNSW 名称。但服务日志 `task_index.go:276` 明确 `numRows=25/minRowsToBuildIndex=1024/indexDataBelowThreshold=true` 并跳过构建。25 行 ANN=1 不作为 HNSW 结论。

扩展到 1066 个真实 Qwen 向量后，独立两库各单 shard，整批 insert 后 flush 一次，每库一个 1066 行 sealed segment，indexed_rows=total_rows=1066、pending=0。最终 HNSW 集合 `codeplus_eval_71a50bb87b06408d97812a3be9cdd43e_hnsw`，collection ID `469193619236545634`，segment `469193619236545651`，index ID `469193619236605664`。UTC 17:40:00 的服务日志明确加载 1 个索引文件，文件路径以 `/HNSW` 结尾，配置 `index_type=HNSW/M=16/efConstruction=128/COSINE`，随后成功加载该 1066 行 segment。三条原始日志保存在 server-evidence.json；本轮实际 HNSW 已据此核实。两组 ef 查询前后索引描述完全相同，没有重建。

生产报告保留 `execution_verified=false`，表示仅靠 SDK 不能独立保证服务器真实执行类型；这不是上述已核对日志的否定。不能把小集合 Finished、固定 1024 阈值或非零 index_id 编成所有服务器都适用的保证。另一次对同一冻结向量重建 HNSW 的结果与本轮不同，重放保证输入与证据可复核，不承诺近似图重建后排名逐位相同。

首次真实导入/编码/冻结准备耗时 218.827 秒，Windows 进程峰值 working set 2993.81 MiB。最终不加载模型的 replay 全程 18.340 秒，进程峰值 337.28 MiB；两实验库 create/insert/build/load 分别为 6.442 秒、5.526 秒（非独立纯建索引耗时）。验收期间一次 Docker 观察：Milvus 421.1 MiB、MinIO 306.6 MiB、etcd 26.6 MiB；这些是共享服务瞬时值，含缓存且不是实验独占资源。此微型合成数据、固定顺序、热缓存下的 48 次样本只证明实验可运行，不能形成吞吐、生产延迟或模型优劣结论。

### 日常隔离、回归与清理

开始前真实 Milvus 集合列表为空，未声称已经验证某个长期日常库。正式 opt-in 用例另建真实 Qwen 日常 FLAT 库，导入一份合成 Markdown，再禁止 `LocalEmbedding._load`、令当前 model/revision/dimension 不同并指定不存在的 fixtures，成功从冻结文件运行三路与 ANN。前后绑定、READY/revision、完整向量行及原文相同；schema 仍是原日常 FLAT。真实向日常库请求不存在的 sparse 字段返回 SDK 错误，实验记录为 error，hybrid 为 unavailable；没有偷偷修改日常 schema。

相关默认回归：57 passed / 4 skipped（17.18 秒），覆盖 knowledge、service、evaluate 和命令注册；未 opt-in 的真实测试明确跳过。`CODEPLUS_TEST_EVAL_FROZEN=<上述 frozen.json>` 与 `CODEPLUS_TEST_MILVUS_URI=http://127.0.0.1:19530` 显式 opt-in 后，运行 `tests/test_knowledge_evaluate.py` 及原有 `tests/test_knowledge_service.py::test_real_store_document_isolation_and_binding`：4 passed / 240.78 秒，含上述真实重放哨兵和原 SDK 四次进程硬退出后的显式恢复。每轮 pytest 在本轮 TemporaryDirectory 内指定独立 `--basetemp`，不触发全局旧目录保留清理。

本轮自建集合均在 finally 中按名字删除并确认不存在；临时导入库及 pytest TemporaryDirectory 正常收尾。正式冻结、报告及服务证据摘要保留在 Git 忽略目录。没有修改共享容器配置、既有 WSL keepalive、主工作区或任何旧 S2/S3/S4 policy 拒绝路径。**S6 当时不支持日常 BM25/hybrid，未实现显式迁移**；本阶段仅交付实验集合路径，不提前扩展精排、模型/分块对照或大规模迁移系统。当前能力与重建步骤见本文开头。

**S6 本轮清理阻塞**：收尾时提交的 PowerShell 命令计划先保存准备阶段资源摘要，再核对绝对目标均在本 worktree 内，以 Remove-Item 删除以下中间目录和测试缓存；整条命令在执行前被自动审批以 `blocked by policy` 拒绝，未给出进一步原因。因此资源摘要新文件未创建，下列清理未执行；不更换工具、不缩小范围重试，也不触碰旧拒绝路径。

- `C:\Users\18221\.codex\worktrees\7aef\CodePlus\.codeplus\knowledge\experiments\20260919T172643Z_9274f5a8`
- `C:\Users\18221\.codex\worktrees\7aef\CodePlus\.codeplus\knowledge\experiments\20260919T173030Z_45f0aa4b`
- `C:\Users\18221\.codex\worktrees\7aef\CodePlus\.codeplus\knowledge\experiments\20260919T173153Z_445ebc89`
- `C:\Users\18221\.codex\worktrees\7aef\CodePlus\.codeplus\knowledge\experiments\20260919T173410Z_3140d0c3`
- `C:\Users\18221\.codex\worktrees\7aef\CodePlus\.codeplus\knowledge\experiments\20260919T173526Z_5fe896ab`
- `C:\Users\18221\.codex\worktrees\7aef\CodePlus\.pytest_cache`

这些中间副本均为本阶段合成数据，不纳入 Git；首轮 SDK 序列化失败的两个目录只保存了 frozen.json。旧小集合报告不作为真实 HNSW 结论。1066 行首次准备资源值仍可从 `20260919T173526Z_5fe896ab/report.json` 核对；最终可交付结果只使用 `20260919T173947Z_e1ff51df` 下的报告与对应服务日志。

leader S6 独立验收：在新的 TemporaryDirectory 禁止模型加载并修改当前模型配置，从最终冻结文件重建两库。两边 1066 行完整读回哈希一致，按原文字位置集合独立复算 Evidence Recall 和 RRF 排名，复得 dense 16/18、BM25 12/18、hybrid 15/18。另选 ef12/48 的 ANN 为 34/48、37/48，重建近似图不保证复现旧排名。对应 segment 469193619237779068 的 UTC 17:47:51–53 服务日志确认构建、保存 HNSW 文件并加载；所有自建集合、临时目录与独立脚本正常清理。四文件回归 57 passed / 4 skipped（16.95 秒）。逐项审查复用、失败统计、冻结重放和日常隔离后，功能与代码质量通过。

## S7 组合验收与文档收尾（K36）

2026-09-20，worktree `C:\Users\18221\.codex\worktrees\3e14\CodePlus`，基线 `b8ecaa37d4577fa5c28780d2657964f6e587f344`。实现者交付后，leader 已完成最终功能与文档质量审查，纳入本阶段本地提交。仅修改五份已有文档；未改产品接口、存储或配置加载，未新增测试文件/框架。

### 本轮真实组合链路：通过

本 worktree 原生 Windows Python 3.14.3 `.venv` 执行 `uv sync --locked --extra knowledge` 成功。复用固定 revision Qwen/CPU/1024 维的离线 HF 缓存、健康的共享 WSL Milvus 和既有 keepalive。只读加载 `D:\CodePlus\.codeplus\config.yaml` 的现有回答 provider；在内存启用独立临时知识目录，未复制密钥或修改配置。

真实 `CodePlusApp.run_test` / Textual Pilot 向 ChatInput 输入并按 Enter，经实际 dispatcher、handler、Agent、模型和 SDK 执行；仅旁路记录显示消息，不替换业务实现。这是 headless TUI 用户输入链路，不是桌面视觉验收。三份小型合成资料共 3 chunks，资料、输出及 session 均位于本轮 TemporaryDirectory；本轮不再读取 WorkBin 个人文档，真实原件覆盖复用 S3。

| 检查 | 实际结果 |
| --- | --- |
| create → 目录 import → status | MD/PDF/DOCX 各 1 chunk，READY/revision=3；89.114 秒，包含初始化与模型加载 |
| 问答与实际 WriteFile 报告 | Sable 137 元/19 天、Juniper 246 元/31 天、Cobalt 358 元/期限未规定；答案、三行报告逐项与原文一致。显式使用 acceptEdits，沿原文件工具保存 |
| 全部三条引用 open | Markdown 第 3 行、PDF 物理第 1 页、DOCX 正文第 2 段；每条 SourceSpan 与解析原文字符范围一致，保存原件 SHA256 与导入文件一致；报告记录三处来源、generation、revision=3 |
| 同路径更新后重新问答 | Sable 改为 149 元/22 天，当前证据无旧数值，回答使用新引用；旧引用仍读到 137 元/19 天，revision=4 |
| remove 后新一轮检索 | 删除 Juniper 后本轮证据仅剩两份有效文档，已删 doc_id 不出现；旧 PDF 引用仍读到 246 元；既有报告文件哈希不变，revision=5 |
| retry / off / 普通回答 / 历史 open | READY 下 retry 不增加 revision；off 清空回答上下文并禁用两项知识工具，真实 provider 回复 K36_OFF_OK，无知识工具调用/证据；off 后旧 MD 引用仍可打开 |

最终完整运行 127.871 秒，应用错误记录为空；这是一次小样本功能链路时间，不是性能基准。临时库 ID `581a5d9d63f34740ab80d445fcdbb853`；对应集合已删除并确认不存在。先关闭 session、SDK、provider 和日志句柄，再正常退出 TemporaryDirectory；工作目录和外层探测脚本目录均确认不存在。配置 SHA256 前后不变。没有重启共享容器、改动 Windows Docker Desktop、触碰任何旧 policy 拒绝路径，或产生新的清理阻塞。

首轮实际已完成导入/问答/报告，在探测的原件哈希核对处因 source_uri 经 Windows normcase 小写、临时 hashes 用原大小写文件名而发生 `KeyError('sable policy.md')`。这是验收脚本映射错误；改为 URI 解码后 casefold 映射，保留完整哈希和位置断言，第二轮从新临时库完整重跑通过。未修改产品 source_uri。首轮集合 `codeplus_kb_e45b09147ee04b00af136a1ec0073381` 及其临时上下文也正常清理，原配置未变。

### 复用的独立证据与未执行项

- leader 在主目录同一 `b8ecaa3` 基线执行全套 tests：**701 passed / 7 skipped，47.66 秒**；另有一个既有 `test_consolidation.py:243` 未注册 `pytest.mark.timeout` 警告。skip 保留为未执行，不计通过。本轮无产品代码变更，不重复全套。该轮使用独立 TemporaryDirectory basetemp 和 `no:cacheprovider`，正常清理。
- leader `uv build` wheel 成功，核实 knowledge 十个模块均入包，tests 不作为 wheel 运行依赖；临时构建目录已清理。主目录 fixtures 保持 LF，16 题/18 处 quote 范围均匹配；主配置未改，主目录 Git 干净。
- 恢复复用 S3 的真实 SDK 四个进程硬退出点与显式 retry，以及 S6 opt-in 复跑；本轮只检查无待处理操作的 retry。CLI text/stream-json 和真实 WebSocket 用户入口复用 S5 的已验收证据；S7 实际重查普通 CLI、knowledge、evaluate 及各管理子命令的 `--help`，全部退出 0，参数与新增文档一致。`uv lock --check` 通过。
- 冻结三路与真实 HNSW 复用 S6 最终 frozen/report/server-evidence 和 leader 禁止模型加载的独立重放，未重跑 1066 片段编码或修改实验结果。S7 当时日常 BM25/hybrid 迁移、K34 精排、K35 模型/分块对照均未实现；OCR、复杂版面、大规模/长期压力、掉电保证、多用户及可视桌面布局仍未验收。

本轮未新增个人资料、原始实测输出或缓存到 Git。旧阶段的清理限制保留在原记录中，不把本轮正常收尾表述为已处理历史残留。

leader 最终验收：核对第二轮真实命令退出 0 及各阶段原始输出，确认三格式报告、全部来源、更新/删除、历史引用、off 和清理均通过；对照真实命令及配置/权限代码审查中英文使用说明。结合主目录完整测试、wheel 与前序逐阶段独立验证，首版功能和代码质量验收通过。提交只包含本阶段五份文档，合入主目录，不推送远端。用户原配置保持原样，功能默认关闭，启用步骤见本文开头。

### 自动准备环境验收（2026-09-20）

Leader 独立复核：完整回归 705 passed / 7 skipped（44.50 秒，既有 timeout 标记警告）；最终进度文案小修相关回归 17 passed / 1 skipped。真实隔离 Compose 双实例验收 90.63 秒完成：失败修复重试、仅启动一次、双 Qwen 加载、重复进入保活复用、关闭一实例不影响另一实例；独占容器和卷已清理，原共享容器 ID/启动时间不变。另实测本地外部地址与远程不可达地址均不触发本地管理。

原生 TUI 键盘进入、Remote 浏览器准备与真实问答通过；CLI stream-json 每行合法并正确回答合成资料的 17 天期限，普通聊天回归通过，主配置哈希未变。首次全新联网下载与未安装 WSL/Docker 的真实安装过程未现场执行。wheel/sdist 的 Compose 内容和包外目录定位由实现成员验证通过。

### 短引用与历史预览验收（2026-09-20）

Leader 最终完整回归 708 passed / 7 skipped（44.70 秒，仅既有 timeout 标记警告）。真实三格式元数据预览无需模型或受管运行时；更新 Markdown、删除 PDF、切库及 off 后，旧引用仍返回原文与相应版本状态。原生 TUI 实际键盘恢复会话、Tab/Enter 打开、Esc 返回与鼠标点击正文 `[1]` 均通过，滚动可读原文。

Remote 浏览器实际验证 off 后新建会话再恢复、正文链接和来源按钮、Enter/Esc 及焦点返回；真实 provider 对更新后的 Markdown 回答 29 天，旧 PDF 回答仍为 46 个月，两条回答各自的 `[1]` 打开正确来源。测试应用已退出，两份独占测试集合已移除，主配置哈希保持不变。CLI 完整 ID 与报告追溯协议由本次回归和前项真实 CLI 验收覆盖。
