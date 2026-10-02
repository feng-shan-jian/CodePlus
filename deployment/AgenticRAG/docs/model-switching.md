# 模型切换

修改 Embedding、解析、分块或索引配置后，用 model 查看当前库与目标配置的差异，再选择操作：

```text
/knowledge model
/knowledge model <提议UUID> --choice confirm
/knowledge model <提议UUID> --choice retry
/knowledge model <提议UUID> --choice keep_original
```

confirm 开始重建，retry 继续失败的已确认目标，keep_original 放弃目标并使用原版。提议只作用于当前指定库。

重建读取已归档原件，不要求原路径仍存在。完成后一次切换发布版；失败期间保留原版，已有运行继续使用原配置。

仅修改 Rerank 或检索参数不重建文档向量；新任务采用新设置。模型资产与运行环境见[本地模型](local-model-worker.md)。
