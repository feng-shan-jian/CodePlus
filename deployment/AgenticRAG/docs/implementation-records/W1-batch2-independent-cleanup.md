# W1 第二批独立清理

状态：MAIN_RESOURCES_CLEANED_HELPER_BLOCKED_AUTO_APPROVAL。主资源清理通过，带明确元数据观察；独立 helper 根最终删除被自动审批拒绝，仍保留。HEAD 8c764644c57e59c9aa9ae2337f61efc3447d88ee，分支 codex/rag，index 空。仅清理本批临时资源，无产品、测试、契约文档修改，无 stage/commit/push。

本批 leader 根已缺失：C:\Users\18221\AppData\Local\Temp\codeplus-w1-batch2-leader-20260923。实际删除 78139 个普通文件、16021 个目录（含根）、1022 个 reparse 链接，共 4932179248 逻辑字节；2 组硬链接全部在根内，72 个特殊属性普通文件已审计。全部 78139 个普通文件通过零共享独占只读打开检查。所有链接最终目标和硬链接边界在删除前再次核对，未杀任何进程。

专属 Docker project codeplus-w1-batch2-leader-20260923 的 3 个容器、3 个卷、1 个网络已按实际身份和全部引用者校验后删除。端口 19542/9103 均关闭。其他 2 个容器、5 个卷、4 个网络的完整稳定定义前后相同；镜像 ID 集保持，未 prune。完整实际 ID 见 JSON deleted_docker。

74 reviewed、322 tested、206 production、6553 protected、199 previous reviewed、3 postnotes、8 SQL、7 spec 哈希前后均相同。411 ignored 中 408 普通文件哈希保持，3 个真实目录实查仍存在。HEAD/branch/index/本批报告以外的 Git 状态保持。6 个登记 worker 的 PID+birth 身份均不再存活，无自有活进程。

共享六根完整不跟链接 lstat 共 172661 节点。与最初 before 比较的严格结果为 FAIL，5724 条差异；与实际删除前 v3 快照比较为 PASS，0 条差异。全部差异仅是 global uv/cache 既有普通目录的 size，其他字段、所有普通文件和 reparse 节点以及另外五根零差异。原因未知；这不是严格全等，也不是共享文件内容全量哈希证明。Leader 在原严格 FAIL 和全部差异保留后，将该项作为独立元数据观察接纳，未对共享根安装、修改或删除。

原失败均保留：辅助脚本规格相对路径基准错误；owned 1052 个普通目录 attributes 从 0x10000010 到 0x10 的首次严格失败；shared 5724 个目录 size 变化的第二次严格失败。两个删除 gate 失败时都未执行任何 Docker 或文件删除。所有原文件／快照／逐项 delta 保持，v3 明确说明差异后重新验证保护哈希、文件身份、链接、硬链接、进程和 Docker 边界再执行本批原授权动作；未重置属性掩盖变化。

R14 四文件目录、batch1 单个 helper，以及 convergence 工作树及其关联资源（包括已被拒的目录和四个新 helper/index 文件）全部保留，未重试任何被拒操作。它们不属于本批删除目标，不宣称所有临时资源已清除。正式验收和失败记录均留库。

逐项清单、hash、完整 Docker 身份、保护分组以及原失败路径见同名前缀 JSON 与 jsonl.gz。所有共享快照字段为 attributes/birthtime_ns/ctime_ns/dev/ino/mode/mtime_ns/nlink/path/reparse_tag/size，无 atime。没有为清理重跑产品测试。

报告生成 helper 的首次括号 SyntaxError 已保留独立错误记录，修正后报告生成完成；错误调用没有执行任何代码。

最终残留：C:/Users/18221/AppData/Local/Temp/codeplus-w1-batch2-cleanup-20260923，12 个普通文件、1 个根目录、63232 逻辑字节。精确目录删除的 exec_command 在 CreateProcess 被自动审批以 blocked by policy 拒绝，未执行，包括同条命令内的检查、删除和报告更新。未提供更具体理由，未作任何重试。新鲜只读核对所有祖先无 reparse、所有文件 nlink=1、尺寸/SHA与已固定 inventory 一致。没有另建外部 finalizer 脚本。主资源清理证据不受影响，但不能宣称本批临时资源已全部清除；待用户针对这个新目录明确授权重试或决定保留/手工处理。STOP_WRITE。

