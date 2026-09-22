# R14 独立清理

结果：PASS_WITH_METADATA_OBSERVATION。R14 的专属临时资源已删除；独立清理会话停止写入，交回 Leader 做最终复核与精确本地提交。没有修改生产、测试、既有报告或验收阈值，没有 stage、commit、push，也没有重跑产品测试。

## 清理范围

- 删除两个精确临时根：`C:/Users/18221/AppData/Local/Temp/codeplus-r14-executor-20260922`、`C:/Users/18221/AppData/Local/Temp/codeplus-r14-leader-20260922`。包含 143792 个普通文件、27105 个目录、1863 个重解析节点；普通文件逻辑大小共 9515393455 字节。
- 删除两个冻结 Compose 项目的 6 个容器、6 个卷、2 个网络。删除前按完整 ID、创建信息、project 标签和挂载重新核对；34 条检查/停止/删除命令全部 exit0。19538、9099、19539、9100 均无监听。
- 删除唯一仓库临时残留 `R14-real-third.json.pending`：583729 字节，SHA256 `48e922c602f6f582f82941c493335d2a1db970815ca099d31d04e4566dfbc486`。对应正式 JSON、日志、命令和其他全部失败证据保留。
- 删除本清理会话自己的 `C:/Users/18221/AppData/Local/Temp/codeplus-r14-cleanup-20260923` 及全部辅助文件；完整文件 SHA、链接计数与删除结果见 `R14-cleanup-finalizer.json`。

临时根实际路径及祖先均无重解析。143792 个普通文件的 inode/link 计数与两根内完整枚举一致，2 个多链接组均全部在内部；1863 个重解析目标也均在内部，先删除链接节点后再原生 pwsh `Remove-Item -LiteralPath` 删除根。144 个特殊属性文件不存在未知外部硬链接。新鲜进程审计未发现本项存活 worker，未终止任何进程；审计自身启动链单独排除。

## 保护复核

前后逐项重算均无差异：102 个执行者交付文件、143 个审阅文件、206 个生产文件、6553 个保护输入、7 个旧 SQL、7 个 spec、6 个 R13 后记。411 个 ignored 项中 408 个文件哈希一致，3 个原有工作树目录均实际存在。

HEAD 保持 `a4fede1c3f0a09202710e53debdbac3307c0911d`，分支 `codex/rag`，index 为空。继承 MultiHop 输入与 Git 状态、compose 状态、根 README/README_EN、其他任务及 `bf43/CodePlus` 收敛工作树保持保护。外来 2 个容器、5 个卷、4 个网络的完整选定身份元数据前后相同；挂载数组按内容规范化比较，不依赖排列顺序或假定 bind mount 有 Name。

## 共享元数据观察与失败记录

共享 before/after 使用同一脚本逐项 `lstat`，不记录 atime，不跟随重解析，不改共享属性，也不创建缺失目录。两份完整 gzip 各记录 172661 个现存节点和 1 个既有缺失 pytest-2 标记。

严格全等审计 `R14-cleanup-after.json` 保留 **FAIL**：6139 个 `C:/Users/18221/AppData/Local/uv/cache` 普通目录仅 `size` 字段出现变化，0 新增、0 删除，其他字段全部相同。独立重新读取两份 gzip 确認这些条目全为非重解析目录；所有共享普通文件、模型、core、CUDA 和 worker 协调目录的元数据均相同。逐项差异在原 after 报告中，重新分类见 `R14-cleanup-shared-review.json`。原因未知，没有为消除差异而修改共享资源，也不声称模型或缓存内容经过全量 SHA 校验。R13 的既有同类观察与本次 6139 项测量分别保留。

此外，最初一次只读 schema 定位因 PowerShell foreach 直接接管道产生解析错误，未执行文件操作，改为先赋值再管道后继续；见 `R14-cleanup-readonly-error.json`。没有其他清理删除失败或重试。既有执行者早期失败和 Leader crosscheck 首轮失败均由冻结哈希确认保留。

完整清单、计数、报告 SHA 见 `R14-cleanup.json`。本记录仅交付 R14 清理，不代表后续 W1、R15 或 R00–R26 整体目标已经完成。
