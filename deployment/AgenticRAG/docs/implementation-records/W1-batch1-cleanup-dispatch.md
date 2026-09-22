# W1 第一批独立清理分派

Leader独立验收通过：core658 passed/29 skipped、host715 passed/3 skipped、44项定向（为全量子集，不重复计数）、609/609文件/4041Chunk/0失败和wheel/sdist两路安装。5个生产文件净增14行；只接入冻结batch1-v2的14路径，剩余W1/W2未接入。当前基线175f8f54ed9e24736a232f6b989d145f7f217dca，分支codex/rag，index应为空。

新独立会话 /root/w1_batch1_cleanup 收到 WRITE_GRANTED 后为唯一写入者，Leader期间只读。读取W1-batch1-leader-review.md、baseline.json、freeze.json、precleanup.json及resources.json，按最新precleanup冻结核对；其中docs/convergence.md在运行完成后仅更新Leader验收状态，产品和测试字节保持运行冻结值。

## 唯一清理范围

- C:/Users/18221/AppData/Local/Temp/codeplus-w1-batch1-leader-20260923：本批CPU环境/cache、构建产物、固定tokenizer、副本、pytest三个数据根、所有辅助脚本。没有本批Docker或GPU服务，Docker所有资源均需保留。
- 可创建并最终删除 C:/Users/18221/AppData/Local/Temp/codeplus-w1-batch1-cleanup-20260923，全部临时辅助文件只放此根；复杂脚本写文件，用pwsh7执行。
- 递归删除前核对解析后精确绝对路径与祖先、junction/symlink/reparse及hardlink，证明链接不跨授权根；Windows原生LiteralPath删除，不跨shell，不prune，不按前缀批量删除，不改共享文件或其属性。未知外部链接保留报告。
- 如有本批活进程，用实际解释器/命令、PID+birth证明归属后处理，不仅看PID。仅排除审计自己的启动链/已结束采样子进程，不能误判外来进程。本轮所有测试已退出，不需重跑产品测试。

## 必须保留

共享models/core/CUDA/uv缓存/worker协调目录、原已缺失pytest-2、外来Docker、继承eval/compose/rootREADME、所有其他工作树及用户资料。全部正式测试、报告、首次raw换行SHA断言FAIL和R14历史失败保留。411 ignored条目为408文件+3实际存在目录，不能把目录缺失当null哈希匹配。

特别禁止触碰 C:/Users/18221/AppData/Local/Temp/codeplus-r14-finalizer-20260923：其中4个提交后辅助文件被自动审批拒绝删除，用户对该精确目录的重试授权问题仍待回答；不得通过你或其他工具绕过。bf43收敛任务的10个旧测试目录同样被拒绝，且其环境/cache/tokenizer仍在用，也不在本批范围。其他后续测试目录、patch/manifest全部保留。

## 交付与交回

只写docs/implementation-records/W1-batch1-cleanup.md、W1-batch1-cleanup.json及同前缀正式附件。必要真实原始日志whitespace例外仅允许records/.gitattributes中的精确文件名；不改日志正文、不用通配规则。不改源码/测试/既有报告/checklist，不stage/commit/push。

保存逐项可定位before/after文件SHA、根/ignored目录存在、进程身份、Docker稳定字段和共享metadata（不含atime、不跟随reparse，不声称模型全量内容SHA）。若再次只有uv普通目录size变化，保留严格FAIL，独立读取两份完整gzip逐项分类后可报PASS_WITH_METADATA_OBSERVATION，原因未知；不能改属性消除差异或冒称全量相等。

清理后独立核对所有冻结值、HEAD/空index、唯一自有根和自身helper根消失、外来资源完整保留；原R14被拒绝根仍需保留。自动审批若拒绝本次删除，停止该动作并报告原命令/具体理由，不换工具/代理规避。

完整清理、保留证据且删掉自己helper后明确 STOP_WRITE，给出报告SHA及未完成边界，交回Leader。Leader独立复核、精确本地提交后才进入W1第二批，完整R00–R26目标继续。
