# Knowledge 本机环境与运行说明

检查日期：2026-09-19。下方 K01 记录来自 `D:\CodePlus`；S1 在独立 worktree 实现，见文末 S1 记录。

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

**清理阻塞（2026-09-20）**：已核对目标路径后，PowerShell 批量删除本阶段临时目录/文件的命令被自动审批以 `blocked by policy` 拒绝。随后缩小为本 worktree 内三个明确临时脚本的 `Remove-Item -LiteralPath ... -Force`，同样被拒绝，未给出具体原因。未更换删除工具或绕过审批；以下残留仍存在，不能标记已清理：

- 本 worktree `.codeplus/s2_accept.py`、`.codeplus/s2_lockfile.py`、`.codeplus/s2_unavailable.py`。
- 本 worktree `.codeplus/s2-pytest/`、`.codeplus/s2-regression/`、`.codeplus/s2-final-sdk/`，仅包含本阶段正式测试的临时合成数据。
- `C:\Users\18221\AppData\Local\Temp\pytest-of-18221\pytest-11`、`pytest-12`，本阶段早期服务测试目录；`pytest-10` 已由 pytest 自身清理。

工作树内上述残留均受既有 Git 忽略规则覆盖，没有暂存或纳入代码 diff；不是正式产品文件，也不是应长期保留的交付物。功能实现和实测已完成，最终验收仍需保留这一清理未完成边界。
