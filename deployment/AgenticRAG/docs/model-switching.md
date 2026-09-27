# 模型切换服务

R16 为一个已发布知识库提供明确的模型切换入口。完整批量交互与 CLI 属于 R21，正式 Rerank 排序属于 R18。

## 查看和选择

```python
from agentic_rag.model_switch import inspect_model_switch, apply_model_switch

plan = inspect_model_switch(catalog, kb_id, desired_config)
# 向用户展示 plan["scope"]、plan["actual"]、plan["target"]。
# 只有明确选择后才调用；没有选择时 choice=None 保持待确认。
result = apply_model_switch(
    catalog, kb_id, plan["proposal_id"], desired_config,
    choice="confirm", runtime_factory=runtime_factory,
)
```

`desired_config` 是调用方当前期望的 `KnowledgeConfig`。提议只处理参数指定的一个 `kb_id`；同名 profile 被多个库使用，也不会连带重建其他库。返回的 `desired` 与 `actual` 分别显示期望和已发布版本的真实编码配置，`scope` 固定库与基准版本，`target` 保存完整处理快照。

兼容检查使用既有 `document_encoding_identity` / `index_identity`，不是 profile 名称或向量维度。兼容时返回 `CURRENT`，无提议、模型调用或构建。检查不长期持有修改权；原件路径也不被重新读取。

`runtime_factory(frozen_config)` 沿用 R14 契约，返回 `(tokenizer, provider, backend)`，或返回产出此元组的上下文管理器。它只在确认被现有库修改事务受理后、或明确重试取得恢复权后调用。调用方传入的 `desired_config` 不会被回写。

| 返回状态 | 含义与选择 |
| --- | --- |
| `WAITING_CONFIRMATION` | 可选择 `confirm` 或 `keep_original`；空输入、`None` 和未批准的 `retry` 都不授权执行 |
| `BUILDING` | 实际修改者仍在运行，不提供失败选择 |
| `WAITING_RECOVERY` | 失败或中断，展示累计错误和真实执行次数；可选择 `retry` 或 `keep_original` |
| `STALE_PROPOSAL` | 基准或目标发生变化，原确认不再执行；仍有旧批次时先保留原版结束该批次，再检查新目标 |
| `PUBLISHED` | 既有发布事务已确认成功，返回不可变发布回执 |
| `KEPT_ORIGINAL` | 本次目标已放弃；重启后相同目标不重复提示，实际配置仍来自当前发布版本 |

`retry` 继续同一批准目标、范围和修改批次，不再次要求开始确认；重复 `confirm` 不会变成隐藏重试。其他库修改可正常进行；同库已有待恢复普通导入时，本服务也必须等待其完成或明确放弃。

## 构建与恢复

批准、批次占用和全部原件检查点一次写入已有 admission 事务。原件元数据来自基准版本成员的历史输入记录，原文字节仍由现有 `process_inputs` 和恢复校验读取、验证。源文件已删除不影响重建；已登记归档暂时不可读时，批次进入原有待恢复路径，修复后可继续。

重建使用首次导入共用的严格全成员编码、索引校验与发布主体。全部成员成功且真实索引可查询后才移动 current；不兼容的新向量不会混入旧版。已发布空库允许零成员重建，零文档编码调用；首次空导入的原限制保持不变。

失败期间旧 current 保持不变。继续、放弃、所有者代次、迟到 IO、候选资源、版本依赖及 GC 都沿用 R14/R15。切换表只记录提议、处理选择、批次关联和错误；执行次数直接读取已有 `mutation_executions`，不维护另一套构建状态。

## 正常查询接点

`KnowledgePolicy.start()` 检查配置并将结果暴露为 `policy.model_switch`，随后使用 `Catalog.start_current_run(kb_id, desired_config, task_kind)`。该入口在同一事务中读取实际发布配置、冻结运行并建立版本 pin。选中的 Embedding profile 对象、解析分块和索引存储配置来自当前版本；本次检索设置和 Rerank 选择来自期望配置。

旧 `Catalog.start_run(kb_id, resolved_config)` 接口及其严格兼容检查不变。`DenseSearch` 始终使用已绑定版本的快照；旧运行即使首次读取发生在发布之后，也使用旧编码。旧模型环境不可用时返回原依赖错误，不改用新模型。

单改 Rerank 不改变文档编码/索引身份，不重建向量。新运行的完整配置身份包含 Rerank，旧运行的配置保持冻结。目前没有排序结果缓存，R16 不增加缓存或排序流水线。

## 数据升级

schema10 只追加 `model_switches.sql`。前九份 SQL、已有表和历史数据不改写。正式升级验证使用前置提交的真实 schema9 wheel 创建 GPU/Milvus 数据、保存历史引用，再安装新包升级；同时验证故障事务回滚和升级后真实查询。
