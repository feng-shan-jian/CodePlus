# 文档更新

```text
/knowledge import "D:\资料\手册.md"
/knowledge reimport <文档UUID> "D:\资料\新手册.md"
/knowledge remove <文档UUID>
```

普通导入允许部分文件成功：成功的新文件或更新进入新版，更新失败的文档保留旧内容。未变化的文件不重新解析和编码；没有变化时不产生新版本。

配置兼容时，更新只写变化文档的 chunks，复用其他向量。完整候选准备好后一次切换发布版本；发布失败仍使用旧版。新任务使用新版，已有任务保持原文档集合。

删除最后一篇文档会发布空版本，历史引用仍可读取。删除源文件不会自动删库；移动/改名默认新增，reimport 用于延续原文档身份。

已结束批次的失败文件用 `/knowledge retry <批次UUID>` 重试；接受变化后的输入时加 `--accept-input-changes`。尚未结束的批次使用[恢复命令](manual-recovery.md)。后台自动更新见[来源同步](codeplus-integration.md#来源同步)。
