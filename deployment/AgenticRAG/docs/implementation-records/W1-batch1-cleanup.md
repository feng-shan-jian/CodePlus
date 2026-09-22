# W1 第一批独立清理

状态：PASS。独立会话 /root/w1_batch1_cleanup 在独占写权限下完成清理与复核；HEAD 为 175f8f54ed9e24736a232f6b989d145f7f217dca，分支 codex/rag，index 为空。没有运行产品测试、修改源码/正式测试/既有报告/checklist，也没有 stage、commit 或 push。

已用 PowerShell 7 原生 Remove-Item -LiteralPath 删除精确自有根 C:/Users/18221/AppData/Local/Temp/codeplus-w1-batch1-leader-20260923。清理前扫描全部 9321 个普通目录、34585 个普通文件和 884 个 reparse 节点，普通文件逻辑大小 990329552 bytes。全部 reparse 最终目标落在该根内；1 组硬链接的全部成员也在根内。72 个只读/隐藏/系统属性文件属于该根，无外部链接或未识别节点。记录实际 PID、出生时间、解释器/命令行，未发现非审计本批活进程，未终止进程。

清理前后 43 个当前交付文件、315 个测试冻结文件、206 个生产文件、6553 个受保护条目、143 个前阶段交付文件、6 个 R14 提交后记录、7 个历史 SQL 和 7 个规范文档均与最新 precleanup 冻结值相同。411 个 ignored 条目中，408 个文件 SHA 相同，3 个目录确实仍存在。所有继承 eval/compose/root README 和正式失败证据保持；新增文件全部使用本清理前缀。

本批没有 GPU/Docker 资源。全部外来 2 个容器、5 个 volume、4 个 network 的身份与定义字段前后相同，没有服务、端口或 Docker 变更。

共享 models/core/CUDA/uv cache/worker 协调根前后均扫描 172661 个现存节点；原先缺失的 pytest-2 仍缺失。lstat 不跟随 reparse，不包含 atime，也不宣称共享文件全量内容 SHA。严格 after 对比为 PASS；随后独立重新读取两份完整 gzip，逐路径和全部字段核对，新增、缺失、变化均为 0，无需 metadata 差异豁免。

- before：W1-batch1-cleanup-shared-before.jsonl.gz，172661 行，4593398 bytes，SHA256 0f414c289f3d27183cce3724140dbeb5df871d78244e76d5488a9595b2f9ac62。
- after：W1-batch1-cleanup-shared-after.jsonl.gz，172661 行，4593398 bytes，SHA256 0f414c289f3d27183cce3724140dbeb5df871d78244e76d5488a9595b2f9ac62。
- 全部自有节点与链接证据分别见 W1-batch1-cleanup-owned-before.jsonl.gz、W1-batch1-cleanup-owned-links.jsonl.gz；摘要清单登记全部正式附件 raw SHA 和大小。

主自有根最终状态：已删除。辅助根最终状态：已删除；最终器从已加载脚本完成精确删除并写出独立证据。

两个既有自动审批拒绝范围未触碰：R14 finalizer 根 4 个文件、18611 bytes 的内容与文件元数据不变，用户重试授权仍待回答；bf43 的十个被拒绝旧测试目录及仍在用环境/cache/tokenizer 全部不在授权范围，未重试删除，其资源报告 SHA 保持 5969b0c12f8c7a53882999466650fd8ad3ff4c4c635798b88471c8662f99fc6f。不把本批清理完成表述为这些历史目录已清理。

清理辅助 review.py 的首次组装调用发生 JavaScript SyntaxError，未执行任何嵌套工具/文件操作；已独立保留 W1-batch1-cleanup-tool-error.json 并修正。全部实际清理与审计执行均通过，未隐去失败。

完整 R00–R26 任务继续。独立清理交回后仍由 Leader 复核并精确本地提交。
