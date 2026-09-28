# 手动恢复与放弃

独立核心通过 `inspect_recovery`、`continue_recovery`、`abandon_recovery` 提供手动恢复，普通增删改和严格首次建库共用检查点与发布协议。打开 `Catalog` 不运行恢复，也不加载模型。产品命令见[宿主接入](codeplus-integration.md)；本页描述调用方契约。

```python
from uuid import UUID
from agentic_rag.ingestion import inspect_recovery, continue_recovery, abandon_recovery
from agentic_rag.storage import OwnerToken

plan = inspect_recovery(catalog, batch_id, current_config=current_defaults)
if plan["state"] == "WAITING_RECOVERY":
    expected = OwnerToken(**{
        key: value if key == "owner_epoch" else UUID(value)
        for key, value in plan["expected"].items()
    })
    # Only after the caller has selected continuation:
    result = continue_recovery(catalog, expected, runtime_factory=open_frozen_runtime)
    # The alternative user choice is abandon_recovery(catalog, expected).
```

`open_frozen_runtime(config)` receives the original batch's resolved `KnowledgeConfig`. It returns `(tokenizer, provider, backend)` or a context manager yielding that tuple. Plain returned components remain the caller's responsibility; context-managed components close on success and failure. The backend must use that catalog and frozen storage endpoint. Runtime credentials remain explicit inputs to the adapter and are never copied into the snapshot or recovery plan.

## 检查与选择

`inspect_recovery` 在取得并立即释放实际库级 OS 锁后识别失联 owner，持久保存本次计划。另一个进程仍持锁时返回忙。计划列出冻结输入清单、实际配置、当前默认配置差异、原基准版、逐项检查点及原因、未确认结束的外部请求和用于 CAS 的 owner 身份。

`can_continue` 只表示输入/检查点层面允许尝试；`execution_environment` 明示此时没有验证原模型、tokenizer 或服务可加载。`runtime_factory` 才打开冻结环境，失败保留待恢复批次和旧版本，不替换成当前默认模型或 CPU/API。每次失败后的再尝试应重新检查并使用新的 `expected`；陈旧 token 不能越过实际 OS 锁和 SQL CAS。

清单中尚未提交原件的输入在恢复时明确标记快照失败，绝不从当前路径补读。普通导入保留部分成功规则；严格首次建库遇到任何此类失败或不可变解析失败时，计划明确 `can_continue=False`，继续也会失败，不能发布缺件语料。已提交但缺失/损坏的检查点会阻止继续并保留现场。

`repair_missing_original(catalog, batch_id, item_id, original_bytes)` 仅接受与已登记 SHA256 和字节数完全相同的原件，支持调用方明确提供的缺失原件修复；它不能覆盖现有损坏的不可变归档，不接受当前源文件的新内容。缺失输入清单无法通过读当前目录重新生成。

## 检查点与候选代次

继续始终沿用原 batch、原输入、完整冻结配置和原 base revision。源文件后来的修改、移动、删除、目录新文件以及默认 profile 改变均不进入该批次。`retry_failed` 是已正常结束批次之后的新操作，不用于替代同批恢复。

完整文档编码检查点核对原件、配置与编码身份、DocumentVersion、解析/映射/结构归档、完整有序 Chunk、规范文本和 little-endian float32 摘要。只有全部 Chunk 的真实结果均已提交，文档才能复用；有效的完整文档不会再次调用编码。没有提交的编码工作从原件重算，不使用成功前缀冒充完整文档。真正 v7 的编码归档保留原字节，并通过其不可变 SQL 和完整处理链验证。

每次处理、编码、准备或恢复检查可在本次调用内复用一次完整认证的输入集合；读取每一项时仍窄查当前 SQL 行并与已认证 checkpoint 比较，真实原件和处理归档仍逐次读取校验，不缓存成功状态。inspect 与取得新 owner 后的 continue 各自建立读取范围，不复用前次计划中的校验结果。公开读取有效检查点时也重新认证集合；原有无检查点返回 None 的语义保持。该读取范围不授予写权限，旧 capture epoch 的有效原件可以读取，写入仍由当前 owner/CAS 决定。

普通导入与 `build_first_revision` 使用同一完整文档编码实现；后者继续要求所有文件成功。首次建库指标分开保存 `prepare_seconds`、`checkpoint_seconds`、`encode_seconds` 和 `insert_seconds`，兼容字段 `encode_and_insert_seconds` 包含实际编码与插入；恢复有效检查点时本次编码可为零，`build_seconds` 仍包含整个调用。

每次恢复取得新 owner nonce/epoch，以全新 revision、artifact 和 Collection 构建候选。旧 artifact 的创建 epoch 永远不改写，当前候选由显式表选择。旧 worker 的 `produced_by` 被元数据拒收；在失权前已经通过检查并发送的 SDK 请求最多写入旧物理候选，不能写新候选或切换发布指针。

待恢复批次继续占用本库修改入口，新的导入、删除、重建均被拒绝。原发布版查询、运行 pin 与其他库修改可以继续；等待用户选择不持有 SQLite 长写事务，也不要求模型一直加载。

## SQL 终态与清理

所有检查、继续、放弃先核对 SQL 的不可变 publication 或 `COMPLETED_NO_CHANGE` completion。`receipt=None` 也是合法已完成结果。重复请求返回原回执和汇总；后来已有新版时不会回退指针、重新占用或清理旧发布索引。已放弃批次同样直接回放原结果，包括调用方再次传入 backend 的情况；终态回放不连接旧 GPU/Milvus，也不要求历史索引仍存在。

第一次放弃取消批次发布资格、解除占用与恢复依赖，保留全部历史原文、处理产物、来源、引用和任务。传入 backend 可立即尝试候选清理；失败之后使用独立入口显式重试：

```python
from agentic_rag.storage.recovery import cleanup_candidates

attempts = cleanup_candidates(catalog, abandoned_batch_id, frozen_backend)
```

清理只处理该已放弃批次；当前版本、已发布版本、活动 pin、恢复依赖和未确认结束的请求都构成保留理由。未知旧 SDK 请求按其 artifact 保护对应物理候选，尚未完成且没有 artifact 的模型请求保护整个批次。实际 owner 退出、客户端取消或请求超时都不能单独证明实际计算或服务调用已结束。模型 worker 的真实 PID+birth 已消失可证明其计算结束；未知 SDK 请求即使提交进程消失仍保守保留。每次结果和失败原因持久化，可重复调用。

创建前登记冻结 endpoint、实际默认数据库 `default` 和随机描述标记，真实 create/describe 返回一致的 collection ID、创建时间和描述后才保存物理归属证据。名称碰撞、成功响应不确定、凭据提交前进程死亡以及同名物理替换均不允许推断归属。v7 没有这类证据的 Collection 仍可查询和复用向量，但不能仅凭名字清理。

删除适配器还验证实际 SQL 认领状态、已放弃批次和无依赖，直接调用 `drop_owned` 不能绕过清理约束。物理服务 IO 在 SQLite 写事务外，库级生命周期锁和不复用名称阻止本应用内部重建竞态。PyMilvus 的 drop 不支持 expected collection ID 条件，describe→drop 不是原子操作；外部管理员在两者之间重建、以及 SDK 自身重试造成的竞态仍属能力边界，不能保证任意外部并发替换下的条件删除。无可靠证据时保留资源。历史已发布索引的通用 GC 见[索引生命周期](index-lifetimes.md)。

## 迁移与验证

SQLite schema 8 只追加 `recovery.sql`，SQL 1–7 不变。迁移在事务外关闭 FK，在事务内创建新表、复制、删除原表、将新表改回原名，并恢复 artifact 触发器；不将原表改名成备份。提交前执行 `foreign_key_check`，失败回滚并恢复 FK；既有 publication 外键和不可变触发器必须保持原定义。现有 `StorageConfig` 序列化和逻辑 schema 指纹不增加默认字段。

正式验证入口为 `test_recovery.py`、`test_recovery_ownership.py` 和正式安装测试。取消仅请求停止，换代和回收须等待真实 worker 完成或进程死亡证据；受控测试与真实服务执行分别记录。
