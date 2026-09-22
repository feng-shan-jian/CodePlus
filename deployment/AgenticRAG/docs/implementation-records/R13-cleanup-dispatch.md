# R13 独立清理分派

Leader 必要代码/运行验收全部通过：1302 passed、32 条件 skipped，真实 GPU/Milvus 两进程、两路安装、v6→v7 升级、609 文件/4041 Chunk 通过。当前尚未 ACCEPTED/COMMITTED，完整项目仍继续 R14–R26。

执行会话 `/root/r13_mutations` 与所有只读调查会话均 STOP_WRITE。新清理会话 `/root/r13_cleanup` 在收到明确 WRITE_GRANTED 后成为唯一写入者；Leader 在清理期间只读。清理会话不 stage/commit/push，不改变生产代码、测试或验收门槛。

## 必须先核对

- `R13-leader-precleanup.json`：107 审阅文件、202 生产文件、6553 保护输入及空 index/HEAD。
- `R13-resources.json` 和 `R13-leader-resources.json`：精确目录、脚本、PID/birth、Compose 归属及 Docker 全局清单。
- `R13-leader-review.md`、用户 AGENTS、R12-cleanup.md 中共享元数据/硬链接观察。既有未知原因不作推断，不改写历史失败。
- 当前 HEAD `44b861a235233f7072fda267e7a75321431c4e88`，分支 `codex/rag`；index 应为空。读 Git 使用 `--no-optional-locks`，不要执行 `write-tree` 或索引刷新。

## 精确授权范围

1. 删除 `C:/Users/18221/AppData/Local/Temp/codeplus-r13-executor-20260922` 与 `C:/Users/18221/AppData/Local/Temp/codeplus-r13-leader-20260922`，包含其中本轮环境、构建产物、脚本、模拟资料、运行日志/数据库/worker 目录。正式报告已保存在仓库，不删除。
2. 可以创建并最终删除自己的辅助根 `C:/Users/18221/AppData/Local/Temp/codeplus-r13-cleanup-20260922`；辅助文件只能放在此根。所有递归删除前验证实际绝对路径、祖先和 reparse/link，不跨目录目标；用 pwsh 7 原生 LiteralPath 操作，不转交 cmd/Bash 删除。
3. 只清理两个已登记 Compose project：`codeplus-r13-executor-20260922`（19536/9097）和 `codeplus-r13-leader-20260922`（19537/9098），共 6 个登记容器、6 个卷、2 个网络。操作前核对精确 ID、project 标签、挂载和名字。未知资源保留，不按前缀批量删，不 prune，不清理镜像/全局 cache；外来 SonarQube/Postgres 等保持。
4. 若仍有本轮进程，核对实际解释器/命令、PID+birth 与资源清单后才停止；不能仅杀 venv launcher 或只凭 PID。Leader 捕获时自有活进程为 0，真实 workers 已自然退出；再次核验，不误杀复用 PID。
5. 检查只读/隐藏文件及硬链接。清除属性或强制删除之前证明所有链接位于上述明确授权根；禁止修改共享缓存/共享环境的属性。未知外部链接先保留并记录。
6. `R13-leader-resources.json` 的 protected_shared_roots 全部保留。保护模型、共享 core/CUDA 环境、共享 uv cache、固定 worker 协调锁和既有 pytest-2。需要比较元数据时保存逐项前后信息，差异必须能定位；不只有聚合摘要再猜原因。
7. 仓库 ignored 清单是 411 个条目：408 文件、3 个原有 `.codeplus/worktrees/.../` 目录；全部保留。Leader 首轮审计因只取普通文件产生计数失败，已定位为脚本口径问题，没有内容差异。现有 `.pytest_cache`、`.venv`、node_modules 和用户 worktrees 均不是清理目标。

## 仓库写入白名单与交付

仅允许新增 `docs/implementation-records/R13-cleanup.md`、`R13-cleanup.json`（必要时同前缀的正式附件）。若真实原始报告导致 `git diff --check` 尾空格失败，可在该目录 `.gitattributes` 增加精确到文件名的必要例外；不得改写原始日志或使用通配规则。生产源码、正式测试、README、spec、checklist、全部旧报告、R12后记和未跟踪继承 compose 不改。

删除后重新核对 202 生产文件、107 审阅文件及 6553 保护输入（仅允许上述明确文档差异），确认两个临时根/自身辅助根不存在、4 个端口无监听、登记容器/卷/网络消失、外来资源仍在，记录 before/after、命令、退出码与完整边界。若出现未解释差异保留原 FAIL 记录并按实际命令界限分析，不捏造全局相等。

完整清理并报告后明确 STOP_WRITE，返回报告 SHA 与结果，把写权限交回 Leader。Leader 会独立复核后精确本地提交。不得自行修改 ACCEPTED/COMMITTED 状态或提前启动 R14。
