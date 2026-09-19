# Knowledge 本机环境与运行说明

检查日期：2026-09-19。任务：K01。工作目录：`D:\CodePlus`。

**环境验收通过，后续使用 Ubuntu-24.04 WSL2 内已有的 Docker Engine。** Windows 上的 Docker Desktop 启动失败，不作为本项目当前的容器运行入口。Milvus 尚未部署，本地 Embedding 尚未下载或验证。

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
