# R14 独立清理分派

Leader 独立代码与运行验收全部通过：最终冻结 wheel 的 core 622 passed / 29 skipped、宿主 715 passed / 3 skipped、真实 GPU/Milvus 恢复13项和生命周期6项、genuine v7→8及回滚/零重复编码、20个实际数据库/归档/物理向量与停服务历史回放。R14 尚未本地提交，完整 R00–R26 目标继续。

执行会话 /root/r14_recovery 已 STOP_WRITE。新独立会话 /root/r14_cleanup 收到 WRITE_GRANTED 后为唯一写入者；Leader 期间只读，不运行测试、服务或改变文件。清理会话不 stage/commit/push，不改变生产、测试、验收阈值或任务状态。

## 冻结与前置核对

- 当前 HEAD a4fede1c3f0a09202710e53debdbac3307c0911d，分支 codex/rag，index 空。只读 Git 使用 --no-optional-locks。
- 先读 R14-leader-precleanup.json、R14-leader-resources.json、R14-resources.json、R14-files.json、R14-leader-review.md。重新核对冻结源码、102执行者交付文件、Leader证据、6553保护输入、旧SQL、spec、R13后记、411 ignored项。
- checklist 的 R14 ACCEPTED_PENDING_CLEANUP 与6个勾选是 Leader 本次验收后的明确授权变化；以本次 precleanup 冻结值为准。其余spec/R13后记不改。
- 保留执行者所有早期失败及 Leader crosscheck 首轮失败记录；RUNNING 部分快照属于已 exit1 的历史命令，不是活进程或通过结论。

## 精确清理授权

1. 删除 C:/Users/18221/AppData/Local/Temp/codeplus-r14-executor-20260922 和 C:/Users/18221/AppData/Local/Temp/codeplus-r14-leader-20260922 内本项环境、构建产物、辅助脚本、模拟输入、运行DB/归档/worker/log/cache。正式仓库测试及证据保留。
2. 可创建自己的唯一辅助根 C:/Users/18221/AppData/Local/Temp/codeplus-r14-cleanup-20260923；临时辅助文件只放此根，STOP_WRITE前删除自身根。递归删除前核验实际绝对路径、祖先、junction/symlink/reparse与硬链接；不越界。用 pwsh 7 原生 Remove-Item -LiteralPath，不将枚举路径转交cmd/Bash删除。
3. 仅清理登记Compose项目 codeplus-r14-executor-20260922（19538/9099）及 codeplus-r14-leader-20260922（19539/9100），精确6容器、6卷、2网络。逐一核对完整ID、project标签、名字及挂载；使用冻结身份，发现陌生资源保留。禁止prune、全局cache/镜像删除或按前缀批量清理。
4. 若仍有本项活进程，依据实际解释器/命令、PID+birth与冻结资源核对后才终止；不能只看PID或杀launcher。审计器自己的启动链记录后排除，外来进程不动。Leader真实GPU worker已验证自然idle退出，仍需新鲜复查。
5. 删除仓库临时残留 deployment/AgenticRAG/docs/implementation-records/R14-real-third.json.pending（583729 bytes；精确SHA见冻结记录）。删除前再次核对大小/SHA。保留同名正式json/txt/command及全部失败记录。
6. 不修改共享cache/模型/环境属性。若只读/隐藏文件需要改属性，先证明所有hardlink都在上述授权根内；存在未知外部链接就保留并报告。保护 shared core/CUDA/model/uv cache、固定worker协调目录、既有/已缺失pytest-2，不能为比较而创建缺失目录。
7. 保护所有继承MultiHop输入、旧 deployment/knowledge/compose.yaml 删除状态、未跟踪 deployment/AgenticRAG/compose...、根README/README_EN、用户文件和其他worktrees。411 ignored项是408文件+3原有目录，3目录必须验证实际存在，不能只比较null哈希。
8. 用户另行授权的 C:/Users/18221/.codex/worktrees/bf43/CodePlus 收敛任务、其补丁、CPU环境/cache及其他任务资源完全不在清理范围。

## 独立记录与复核

写入白名单仅 docs/implementation-records/R14-cleanup.md、R14-cleanup.json 与同前缀正式附件；本分派与Leader冻结记录保持不变。必要的真实日志尾空格例外只能在该records目录 .gitattributes 加精确文件名，不改原始日志、不加通配规则；先记录理由。

保存可定位的 before/after：文件内容哈希、目录存在、进程身份、Docker完整资源身份、端口、共享逐项元数据。共享模型不需重新读取全量内容，明确元数据口径（无atime、不跟随reparse）及不代表内容全量SHA；固定共享worker目录有本次正常协议活动，不能擅自清空。既有R13的uv目录size-only观察保留，不猜原因。不将外来Mounts数组顺序差异当内容变更；不存在Name字段的bind mount按真实结构处理。

清理后验证所有授权根及.pending不存在、四端口无监听、登记容器/卷/网络消失、外来资源仍在、所有冻结文件不变、index空与HEAD未变。保留任何失败命令/报告和重试依据。完成报告后明确 STOP_WRITE，返回报告SHA、实际结果和边界，把唯一写权限交回Leader。Leader独立复核并精确本地提交后进入W1，不提前启动R15。
