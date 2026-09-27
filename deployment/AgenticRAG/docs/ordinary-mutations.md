# 普通文档增删改与整批发布

R13 在独立核心新增 `begin_changes`、`build_changes`、`retry_failed` 与 `mutation_summary`。沿用同一个库级 Mutation、不可变归档和 `storage.publication` 发布事务；没有新建产品入口。首次冻结评测建库仍使用严格的 `build_first_revision`，要求全部输入成功。R14 的[手动恢复与放弃](manual-recovery.md)继续原批次并复用完整文档检查点；GC 和模型更换确认分别由 R15、R16 接续。

```python
from agentic_rag.ingestion import InputSelection, begin_changes, build_changes

with begin_changes(catalog, kb_id, snapshot,
                   (InputSelection(path=absolute_path),),
                   delete_document_ids=(deleted_document_id,)) as owner:
    result = build_changes(catalog, owner, provider, backend, tokenizer)
```

`snapshot` 固定该批配置；`provider`、`backend`、`tokenizer` 为原有实际能力。相同路径继续使用文档身份；移动/改名默认新增，显式 `InputSelection(document_id=...)` 才延续旧身份。删除必须提供该库已有的 document ID，目录缺失不推断删除。删除已不在当前版本中的历史文档是无变化；删除最后一篇有效文档会发布真实空 Collection，新运行获得空结果，历史原文和旧运行仍可读。

普通导入的来源路径归属在成功发布事务内更新。失败显式移动不修改已发布文档的逻辑路径；旧版本 `source_uri` 与原始名称永久来自对应 DocumentVersion。底层 R07 原始 `begin_import`/`capture_inputs` 默认行为保持兼容；普通生命周期由上述协调接口处理。

每个文件先完整捕获、解析、分块，再按模型小批次编码。只有全部必需 Chunk 完成且归档校验通过，才登记该文档的完整编码检查点；后段编码失败不会发布成功前缀。明确的输入拒绝属于文件级失败；模型不可用、响应身份错误、取消、元数据损坏、服务写入/验证和发布错误属于批次故障，保留旧指针及待处理状态。索引 UTF-8 超限不截断。取消不视为正常整批完成。

全部文件终结后，完整候选包含未涉及、未变与失败更新的旧成员，加上完整成功的新成员，减去显式删除。未变输入不重新解析或编码。复用向量前校验基版发布回执、artifact、配置、所有成员、原件/解析/结构归档和完整行清单；Milvus 逐行读取并核对全部标量以及 little-endian float32 SHA256，不能仅凭维度相同复用。新版写入独立 Collection，BM25 按完整新版语料重建，旧 Collection 不改写。

普通构建在操作开始完整派生候选，中间登记与编码记录复用这个内部结果；它绑定 catalog、完整 owner token、batch、revision、base、输入清单、快照和请求，每个使用边界仍检查当前所有权及这些身份。候选成员、行和配置留在内部，插入传输接收独立行字典及 dense 列表，不再对内部候选反复计算整份摘要。旧版 artifact/expected rows 只在 read_vectors 时使用，由正式适配器完整校验实际索引内容。复用范围只覆盖本次构建，不接受调用方提供的候选或通过标志，退出后失效。

最终 validate 仍从不可变记录及真实归档重新派生，并完整验证实际 Milvus 内容；独立调用公开 register、record_encoded、complete_no_change 也仍完整重验。混合构建的完整旧成员准备由五次收为初始和最终两次，无变化构建为一次；这仅表示读取次数，不是端到端耗时结论。

最终一个短事务登记发布回执、切换 current pointer、接受成功文件来源身份、写终态汇总并释放批次依赖。旧 receipt 重试只返回原结果，即使已有后续版本也不回拨。

没有成功成员变更、真实删除或成功的明确索引配置变更时，完成为 `COMPLETED_NO_CHANGE`：不创建 revision、Collection 或 publication，解除本批占用。全部文件失败时，单独的索引配置差异不会触发发布；首次全失败明确没有可查询版本。成功的 `index_changed` 输入可以在编码兼容时复用全部向量重建索引，汇总单列 `published_index_changed`。

`mutation_summary` 分别返回 `published_new`、`published_updated`、`published_deleted`、`published_index_changed`、`unchanged`、`failed` 和 `processed_unpublished`，并保留逐文件身份、来源、原文 hash、错误及检查点。编码完成而库级发布失败的文档计入未发布处理结果，不冒称已成功发布。普通部分成功不代表冻结评测语料齐备。

失败项重试是新的受锁修改操作：

```python
from agentic_rag.ingestion import retry_failed

with retry_failed(catalog, completed_batch_id) as owner:
    result = build_changes(catalog, owner, provider, backend, tokenizer)
```

只选择原终态中的失败文件，沿用原配置并核对新捕获字节；输入已改变或原来没有完整快照时，需要调用方显式 `accept_input_changes=True`。配置变化要求新操作。此参数只确认输入变化，不授权覆盖后续更新。重试在同一注册事务内核对原文档身份，并从原完成 owner epoch 起检查所有后续发布中该成员的变化，防止新增→删除的 ABA 复活。无原身份的失败路径若后来已属于其他文档，或失败选择展开成不同文件范围，明确要求新导入。无关文档的正常发布不妨碍重试；旧失败项已被成功重试或另一次发布取代时，返回明确 `IDENTITY_MISMATCH`，不创建新批次。

普通导入若改变文档编码指纹，在登记持久批次前拒绝并说明需要 D16 重建。这里没有实现模型更换确认，也不能把 Git 本地提交授权当作产品内模型切换确认。

SQLite 升至 schema 7，仅新增 `mutations.sql`，v1–v6 SQL 字节不变。新增请求、文件编码结果与完成记录均不可变；原 owner epoch 和 artifact 写入代次没有混同。正式回归由现有 mutation、recovery 和 package install 测试覆盖版本、发布与恢复；检索及答案质量另用正式语料评测。
