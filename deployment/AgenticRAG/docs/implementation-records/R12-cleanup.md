# R12 独立清理记录

结论：精确清理完成，`PASS_WITH_METADATA_OBSERVATION_STOP_WRITE`，保留未解释的共享目录元数据观察，交回 Leader 复核。不宣称共享目录全部元数据一致。此记录不替代 Leader 最终验收或本地提交；R12 未由清理会话标为 COMMITTED。

执行者：`/root/r12_independent_cleanup`。授权依据为 Leader 在功能/代码独立审核通过后的任务卡与 task-plan 第 6 步、checklist G09。清理期间唯一写入者为本会话；没有生产或正式测试修改，没有 stage/commit/push。

基线：`codex/rag` / `5dbe979a330a6b157adfba7ad3ea8299fecf73a9`，清理前后 index 均空。冻结范围、逐文件 SHA、实际命令/退出码、资源标签及前后检查详见 [机器记录](R12-cleanup.json)，输入为 [Leader 清理前冻结](R12-leader-precleanup.json)。

## 实际删除范围

| 资源 | 结果 |
| --- | --- |
| `C:/Users/18221/AppData/Local/Temp/codeplus-r12-executor-20260922` | 删除前 203729 文件、8260464396 字节；整个专属临时根已删除 |
| `C:/Users/18221/AppData/Local/Temp/codeplus-r12-leader-20260922` | 删除前 66967 文件、4475965020 字节；整个专属临时根已删除 |
| `C:/Users/18221/AppData/Local/Temp/pytest-of-18221/pytest-17`、`pytest-18`、`pytest-19` | 仅清单中的三项已删除，直接子项与归属清单一致 |
| 同一 pytest 父目录的 `pytest-current` | 确认链接目标为 `pytest-19` 后，只解除链接 |
| Compose `codeplus-r12-acceptance` | 核对 compose SHA、3 容器 ID/标签/挂载、3 卷标签、1 网络及连接后，`docker compose --project-name codeplus-r12-acceptance --file C:/Users/18221/AppData/Local/Temp/codeplus-r12-executor-20260922/compose.yaml down --volumes`，退出 0 |
| Compose `codeplus-r12-leader-20260922` | 同样核对后，`docker compose --project-name codeplus-r12-leader-20260922 --file C:/Users/18221/AppData/Local/Temp/codeplus-r12-leader-20260922/compose.yaml down --volumes`，退出 0 |
| 清理会话自有 `C:/Users/18221/AppData/Local/Temp/codeplus-r12-independent-cleanup-20260922` | 仅临时 `cleanup.ps1`，最后分别删除脚本与空目录；复查均不存在 |

所有删除均在 PowerShell 7 内执行，未跨 shell 组合路径。递归前校验规范绝对路径与精确授权根，并检查各级祖先无 reparse。3800 个内部 reparse 全部逐一核实目标在各自根内并先解除链接。发现只读/隐藏 Git 测试文件后，额外逐一执行 `fsutil hardlink list` 核查 291 个特殊文件，所有硬链接都位于各自授权根，随后才对该根使用 `Remove-Item -LiteralPath <exact-root> -Recurse -Force`；没有更改共享缓存文件属性。没有结束任何进程；8 个已知 worker 的 PID/birth 均已失效，删除前后未发现活动自有进程。

## 保留与复核

- 143 个 Leader 已审文件逐字不变，200 个生产源码指纹逐字不变；所有既有失败/通过 JSON/XML/TXT 报告保留。
- R00 的 6553 项输入按既有 19 项授权差异，以及本次预冻结 README 和两个 Leader 流程文档的固定哈希检查；清理前后均零未授权漂移，保护输入聚合哈希相同。
- 生产源码针对断点、调试器及本次临时目录路径扫描无命中；没有顺带重构。代码/测试未变，因此未重复产品测试。
- 自有 6 容器、6 卷、2 网络全部消失，19534/9095/19535/9096 无监听；外来 SonarQube/Postgres 两容器仍运行，5 个外来卷与 4 个外来/默认网络的稳定字段保持一致。
- 模型缓存与 `pytest-2` 的完整树元数据指纹前后相同，诊断后再次核验仍相同。元数据指纹包含路径、类型、长度、修改时间、属性与链接目标，不宣称重新哈希了大型模型权重。共享 uv 缓存和 worker 协调目录保留，但它们的元数据聚合不一致，见下述明确限制。其他临时路径不在任何删除命令中。
- 最终 `git diff --check` 退出 0，HEAD/branch/index 无变化。

## 保留的元数据观察与证据限制

首次最终检查为 **FINAL_CHECK_FAILED**，原失败状态、时间、消息和原 before/after 均保留在 JSON。模型缓存及 pytest-2 的指纹相同；以下两项聚合不同，不能断定差异原因，也不能宣称所有属性相同：

| 路径 | 条目数（前/后） | before SHA256 | after SHA256 |
| --- | --- | --- | --- |
| `C:/Users/18221/AppData/Local/Temp/.codeplus-agenticrag-workers` | 3 / 3 | `c616ab755bffd508084c9c18eb507ca9a161cbddd945cfbc42cd7b60dbcaf807` | `da40694c37665869999fc5b4e01d4c8a152bbcb563e37d1fc2e7c4b075082b06` |
| `C:/Users/18221/AppData/Local/uv/cache` | 144337 / 144337 | `0b813c62d2d0621b2a8fc33e04269af7522f041486a4ac450c31c6e438267a49` | `64afc00bab280a55c4a0822a838e65853237c0ae84f30c984665d49c2583c4ce` |

删除前只保存了聚合与条目数，未保存逐项元数据，因此不能事后定位差异字段。当前共享 `cuda-0` 目录 mtime 为 `2026-09-22T11:58:27.8335160Z`；`lifetime.lock` 与 `startup.lock` 都是 0 字节、Archive 属性、LinkType/Target 为 null，SHA256 均为 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`，各自 `fsutil hardlink list` 仅自身一路径。两文件 mtime 分别为 `2026-09-22T04:10:01.9676028Z` 与 `2026-09-22T04:10:01.9656024Z`，均早于清理；具体当前属性保留在 JSON。这些是当前证据，不当作缺失的 before 逐项证据。

没有对共享目录执行写入、删除或属性变更命令；所有删除目标均在精确授权临时范围内，外部 reparse 已排除，291 个只读/隐藏文件的全部硬链接均在各自授权根。Leader 根据这些明确删除边界、正式代码/用户输入哈希、模型缓存/pytest-2 及外来 Docker 核验，同意按“精确清理完成，保留未解释的元数据观察”收尾。这不把额外的全量元数据断言改为通过，也不把观察解释为已证实的数据删除或内容修改。

## 唯一附加文档变更

Leader 单独授权在 `deployment/AgenticRAG/docs/implementation-records/.gitattributes` 追加精确一行 `R12-leader-host.txt whitespace=-blank-at-eol`。原因是保留首次失败命令日志中的原始尾空白；没有修改原日志或新增通配豁免。该文件不在 143 项冻结表内。

- 修改前 SHA256：`996191b60a193138ff7ac273b6b07f02feb6e4f6002c1d5aef6bbef0c0c3d0cb`
- 修改后 SHA256：`9fc7fb02d284735e1ffe8fca6accf493a4920e32192d67af4a93fc82cdf2a7eb`

清理审计脚本首次因 PowerShell 空数组 `.Count` 处理报错，在任何资源删除前修正为显式数组后重跑通过；原失败时间和消息保留在 JSON。它不是产品测试失败，也没有因此修改生产实现。清理后仅新增本 Markdown、JSON 两份正式报告，并保留上述获单独授权的一行属性变更。
