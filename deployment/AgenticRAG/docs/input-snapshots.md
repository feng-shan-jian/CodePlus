# R07 输入身份与原件检查点

2026-09-22；独立包 0.1.0；路径规则 `local-physical-v1`、请求指纹 `input-request-manifest-v1`、SQLite schema 2。对应 D17/D18/D50/D51、T02/T03、P2 和 A01/A03 输入部分。R08 负责真实解析、编码检查与来源映射，R10/R13 负责索引和发布，R14 负责用户恢复/放弃流程。R07 不创建伪造 parsed/source_map 或完整 DocumentVersion。

## API 与固定清单

```python
from agentic_rag.ingestion import InputSelection, select_inputs, capture_inputs, read_input, verify_inputs

# catalog、kb_id、snapshot 为调用方明确创建的 Catalog、库ID、完整 ProcessingSnapshot。
manifest = select_inputs((InputSelection(path="C:/documents/manual.md"),
                          InputSelection(path="C:/documents/notes")))
owner = catalog.begin_import(kb_id, snapshot, manifest)
try:
    items = capture_inputs(catalog, owner)
    for item in items:
        if item.stage == "captured":
            original_bytes = read_input(catalog, item.batch_id, item.entry.item_id)
    # 后续 R08 消费本批原配置和原件；此处尚无解析/发布。
finally:
    owner.close()  # 未完成批次转 WAITING_RECOVERY，保留占用和检查点。
```

`InputSelection.document_id` 可显式选择本库已有文档，仅适用于单文件。目录按所选根的用户顺序、各目录内文件名 Unicode codepoint 顺序深度优先遍历；不忽略隐藏文件、不跟随链接、没有排除模式或后台同步。空目录保留根选择但无文件项。`.md`/`.txt` 以外后缀、不存在路径、无权枚举、扫描中消失等均写诊断项；os.scandir 中途失败仍保留已看到的子项并增加该目录错误。重叠范围、重复路径和同批多个输入选择同一 document_id 明确失败，不静默去重。

选择阶段只枚举、打开短句柄读取状态，不读取原件内容；不存在 SQL 事务。`begin_import` 取得 R06 库锁，在同一短事务登记基准当前 revision、完整 ProcessingSnapshot、请求清单和匹配身份。此时所有原件仍待采集；清单 hash 是固定请求身份，包含根选择、顺序、路径、观测状态及选择错误，不包含后续 hash/时间/捕获结果。旧 `begin_mutation(kb_id,snapshot,manifest_hash)` 保持可用。

逐项结果是独立 insert-only 检查点：pending → captured 或 failed，终态行禁止更新/删除。批次清单、来源历史和原配置也禁止更新/删除。所有接纳操作核对同 Catalog/store/kb/batch、活跃 OS 句柄、nonce/epoch 和 pending pointer；IO、遍历、两遍源读取及归档核验均在 SQL 事务之外，库级修改租约一直保留。

## 路径与文档身份

输入必须是本机绝对路径；禁止 `..`、UNC/映射网络盘、设备路径、备用流、末尾空格/点和特殊文件。Windows 用实际 GetFinalPathNameByHandleW 规范拼写、统一 file URI；逐父目录检查 case-sensitive 属性，以精确 URI 字符串比较，不对整个路径 casefold/NFC，也不使用 WindowsPath 相等比较。Unicode 组合形式保持文件系统中的原样。8.3 别名解析为其实际名称，与长名同时选入时报告重复；合法 `~` 文件名接受。未知/不可读取的路径属性明确失败。

symlink/junction/reparse 目前明确拒绝，避免跟随路径穿越所选边界。普通硬链接接受，每条实际路径保持独立身份；文件ID仅用来验证采集前后仍是同一对象，不用于身份合并。Windows 大小写别名对应同一现存路径；实际 case-sensitive 目录中的 A/a 可各自导入。Linux 使用本地路径的精确拼写、拒绝符号链接；Linux 真实环境与非 NTFS 文件系统的验收不在本阶段范围。

同库同 source_key 命中既有 document_id；改名/移动默认新增；内容 hash 相同不合并身份。显式更新拒绝跨库身份、新路径属于另一文档及同批重复目标。捕获成功后以 owner 短事务更新文档当前来源并追加 document_sources；历史 InputEntry/RawSnapshot/DocumentVersion 的来源不改。这里“当前来源”表示最近成功接纳原件的导入来源，不代表已经发布；新身份可以在未发布批次中存在。不存在隐式删除、版本发布或当前 revision 切换。

## 完整原件与 Windows 实际一致性

Windows 使用 CreateFileW(GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING)，句柄保持到完整归档及检查点接纳结束；不允许其他普通写/删除/重命名打开。已有写句柄或可写映射会使此打开失败。属性访问不受共享标记限制，因此同时比较句柄对象身份、size、LastWriteTime 与 ChangeTime；不能只依赖 mtime。实现逐块复制、长度有界校验、同句柄第二遍完整 hash 复核，再复用 R06 同卷临时文件写入/flush/fsync/读回 hash/非覆盖完成协议。源路径在接纳前再次核对。[CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)、[FILE_BASIC_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_basic_info)。

实际测试包含独立进程持读取句柄时写/改名/删除被拒绝、已有写句柄、关闭原文件句柄后仍持 Win32 可写映射、真实元数据变化后恢复 mtime，以及采集中实际终止解释器。完整双读并不承诺对恶意同权限内核/驱动或任意硬件故障提供系统级快照；归档协议也不代替掉电持久化认证。Linux 当前依靠 O_NOFOLLOW、对象/状态/长度和双 hash 检测，尚未经本项实机验收。

每份 RawSnapshot 保存完整原字节 hash、大小、实际采集 UTC 时间、来源 URI/名称/media_type 和文件状态；不把内部归档路径作为用户来源。空文件及非 UTF-8 字节也按原件保存，是否可解析由 R08 判定。多个文件各自一致，不宣称整个目录同一时刻快照。

真实路径/共享/状态变化为文件级 SOURCE_CHANGED；不支持路径/类型/权限错误保留具体诊断，IO/空间不足/归档失败明确失败，取消返回 CANCELLED。没有盲目重试或回退。正式测试中的 ENOSPC、fsync、读错误和完成错误是注入，不能称真实磁盘耗尽/断电；真实 kill 分别位于第二文件部分复制与归档已完成但结果未登记的位置。

## 重开、恢复与变化对照

`get_input_manifest/get_input_items` 重开可读取固定原清单和终态结果；`verify_inputs` 返回原 ProcessingSnapshot 及每项有效性视图，不修改历史。`read_input` 对实际返回字节核对大小/hash，只读归档。源文件后改、删除、移动或目录新增均不影响本批结果。归档丢失/损坏/不可读返回 CHECKPOINT_INVALID，不读取最新源修补。

旧进程死亡后先沿 R06 协议识别并由调用方明确接管；新的 epoch 下 `capture_inputs` 仅把旧 pending 项登记为 CHECKPOINT_INVALID，不重新采集源文件。已完整归档但尚未登记结果的对象是孤儿，也不能凭文件存在冒充检查点。即便原源文件现在看起来相同，R07 也不实现修补；用户恢复/新批处理与等 hash 修复方案由 R14 承接。schema1 历史批次只有清单 hash 时，明确报告“无持久输入清单”，不补造输入范围。

变化只对照本批基准发布 revision 的 member 和该版实际处理配置。未发布/失败 attempt 的原件不是线上基准。分类为 new/content_changed/source_changed/encoding_changed/index_changed/unchanged；编码变化另给 requires_rebuild_confirmation（包括向已有库新增文档时），不执行确认或重建。相同原件但解析/分块/模型身份改变不能判 unchanged；只改无关预算可保持处理兼容。测试中的 READY/member 是明确的合成元数据夹具，不代表 Milvus 或生产发布。测试配置中的 parser 名/version 只表达实验身份，不声称该 parser 已实现。

路径官方依据：[GetFinalPathNameByHandleW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfinalpathnamebyhandlew)、[Windows 目录大小写](https://learn.microsoft.com/en-us/windows/wsl/case-sensitivity)、[Unicode 文件名](https://learn.microsoft.com/en-us/windows/win32/intl/character-sets-used-in-file-names)。正式回归入口为 `tests/test_input_snapshots.py` 和 `tests/test_package_install.py`。
