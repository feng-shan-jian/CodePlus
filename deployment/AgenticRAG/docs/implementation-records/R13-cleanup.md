# R13 独立清理记录

状态：`PASS_WITH_METADATA_OBSERVATION_STOP_WRITE`。两个登记项目、两个临时根和清理辅助根均已删除并通过复核；保留已定位的共享目录元数据观察，交回 Leader 独立复核。本会话不修改 ACCEPTED/COMMITTED 状态，不 stage、commit 或 push。

授权来自 [精确分派](R13-cleanup-dispatch.md)，唯一写入者为 `/root/r13_cleanup`。清理前后均为 `codex/rag` / `44b861a235233f7072fda267e7a75321431c4e88`，暂存区为空。完整命令、退出码和文件摘要见 [机器记录](R13-cleanup.json)、[清理前](R13-cleanup-before.json)、[实际操作](R13-cleanup-actions.json)、[清理后](R13-cleanup-after.json)。

## 已删除的精确范围

| 资源 | 清理前与结果 |
| --- | --- |
| `C:/Users/18221/AppData/Local/Temp/codeplus-r13-executor-20260922` | 84145 个普通文件、5149043360 逻辑字节；根已不存在 |
| `C:/Users/18221/AppData/Local/Temp/codeplus-r13-leader-20260922` | 55535 个普通文件、4270740802 逻辑字节；根已不存在 |
| `codeplus-r13-executor-20260922` 与 `codeplus-r13-leader-20260922` | 逐项核对 ID、名字、project/service 标签、挂载、端口、网络成员后，按精确 ID 停止并删除 6 容器，按精确名字删除 6 卷，按精确 ID 删除 2 网络；所有命令退出 0 |
| 清理辅助根 `C:/Users/18221/AppData/Local/Temp/codeplus-r13-cleanup-20260922` | 全部脚本逐文件核验哈希、属性及唯一硬链接后删除，再删除空目录；根已不存在 |

所有文件删除均由 PowerShell 7 的 `Remove-Item -LiteralPath` 执行；未跨 shell 删除、prune、删除镜像或全局缓存。139680 个普通文件均调用 Windows `os.stat(..., follow_symlinks=False)` 检查可靠硬链接计数，其中 3 组双硬链接使用 Windows `FindFirstFileNameW` / `FindNextFileNameW` 枚举，所有路径位于授权根内。144 个只读/隐藏/系统文件没有外部硬链接。1964 个 reparse 节点逐一确认最终目标在各自根内后先解除链接，再复查无剩余节点并递归删除精确根。各级祖先均检查无 reparse；未执行共享属性改写。

目标进程扫描为空，3 个登记 worker 的 PID/出生标识均不再匹配；没有停止任何 Windows 进程。4 个专属端口 19536/9097/19537/9098 均无监听。外来 SonarQube/Postgres 两容器仍运行，5 个外来卷和 4 个外来/默认网络的已采集稳定字段逐项相同。

## 保留与复核

- 202 个生产文件、107 个审阅文件、6553 个保护输入以及既有 6 个 SQL 全部匹配清理前固定摘要，零差异；全部既有失败与通过记录保留。
- 411 个 Git ignored 条目保持：408 个文件哈希相同，3 个既有 worktree 目录仍存在。`.venv`、node_modules、用户 worktrees 和其他未授权目录不在任何删除命令中。
- `git diff --check` 退出 0；新增 Leader 的 11 份 XML/TXT 分别执行 `git diff --no-index --check -- /dev/null <file>`，均无空白诊断，退出 1 仅表示空文件与报告内容不同。未修改 `.gitattributes`，未改写原始日志。
- 代码与正式测试没有变更，因此没有重复产品测试；本记录不替代 Leader 已完成的功能验收或后续本地提交。

## 共享元数据观察

同一采集脚本 SHA256 `15b05d1bc5d7899933fcf3a989477a153348881d1975adffd8fec0cbc567f339` 以 `C:/Python314/python.exe -I -B <cleanup-root>/audit.py before` 和 `after` 运行。before 为 `2026-09-22T22:35:44.267821+08:00` 至 `2026-09-22T22:36:39.659927+08:00`，after 为 `2026-09-22T22:42:06.573994+08:00` 至 `2026-09-22T22:42:37.178655+08:00`。采集代码、参数、时间、全部差异和分组见 [逐项诊断](R13-cleanup-shared-diagnostic.json)，原始逐项数据保存在 [before gzip](R13-cleanup-shared-before.json.gz) 与 [after gzip](R13-cleanup-shared-after.json.gz)。

172576 个条目的路径集合保持，普通文件元数据差异为 0。模型目录 20 项、共享 core 环境 5833 项、共享 CUDA 环境 22381 项、固定 worker 协调目录 4 项逐项完全相同。`pytest-2` 在本会话首次检查时已不存在，前后均为 absent；没有创建、删除或推断其消失原因。

共享 uv cache 的 144338 项中，**6967 个 directory 条目仅 `size`（Python `os.stat().st_size`）读数变化**：6054 项由 0 变为 4096，437 项由 0 变为 8192，其余 476 项变化分布保存在诊断中。没有条目新增/删除；所有 mtime、attributes、nlink、target 及普通文件 size 保持。举例：

| uv cache 相对目录 | before size | after size |
| --- | --- | --- |
| `archive-v0/-IQtwSbAi1m5VaFJ/httpx` | 0 | 4096 |
| `archive-v0/-IQtwSbAi1m5VaFJ/httpx-0.28.1.dist-info` | 0 | 4096 |
| `archive-v0/-XeiaSxRC1yFFEbF/codeplus` | 0 | 8192 |

保留这一明确观察，**不宣称全部共享元数据相等，不推断读数差异原因，也不以本次观察解释 R12 历史记录**。本检查比较元数据，不包含访问时间，也未重新哈希大型共享模型/缓存文件内容。所有实际删除目标均局限于已验证的自有根及登记 Docker 资源，没有对共享目录执行写入、删除或属性变更命令。

## 保留的清理脚本失败

[首次 FAIL](R13-cleanup-actions-first-fail.json) 发生在任何 stop、rm 或文件删除之前：只读卷使用者检查遇到外来 bind mount 没有 `Name` 字段。原状态、错误与此前 3 条只读 Docker 命令全部保留；检查改为仅对 `Type=volume` 读取 `Name` 后重新执行。最终清理命令均退出 0。此失败未引起产品、测试、外来资源或历史报告修改。

[汇总断言 FAIL](R13-cleanup-finalization-first-fail.json) 发生在资源清理后、正式汇总和辅助根删除前：Docker inspect 将两外来容器相同的完整 mounts 对象按不同顺序返回，直接数组比较不相等。原顺序、所有字段及失败状态全部保留；正式比较仅将完整 mount 对象排序，没有删除字段，排序后两容器所有已采集字段相同。5 个外来卷与 4 个外来/默认网络原始字段直接比较即相同。

[辅助根自清理预检 FAIL](R13-cleanup-self-first-fail.json) 发生在任何辅助文件删除之前：新增报告集合误纳入既有分派 Markdown，并将该文件的 LF→CRLF 提示当成空白错误。保留原异常与只读诊断复现，将既有分派文件排除出新增集合后继续；没有修改分派文件、换行或属性规则。
