# 中断恢复

导入或重建中断后，批次进入待恢复状态，同库暂时不能开始新的修改；已发布资料仍可查询。

```text
/knowledge status
/knowledge recover <批次UUID>
/knowledge recover <批次UUID> --choice continue
/knowledge abandon <批次UUID>
```

recover 显示原输入、配置和处理进度。continue 沿用原批次与归档，复用已完成的文档处理；abandon 结束该批次并保留旧发布版。恢复不会自动读取源文件后来的改动。

继续时需要原模型和服务环境可用。缺失原件可通过 `repair_missing_original` 补回与登记 hash 一致的字节；若要采用新输入，放弃旧批次后重新导入。

已经结束、仅部分文件失败的批次使用[retry](ordinary-mutations.md)，不使用中断恢复。
