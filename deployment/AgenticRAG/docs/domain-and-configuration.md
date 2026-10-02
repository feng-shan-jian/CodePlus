# 配置

CodePlus 的 `knowledge_development_config` 指向知识功能配置文件的绝对路径。文件包含 `knowledge` 和 `worker` 两部分。

| 配置 | 用途 |
| --- | --- |
| `knowledge.storage` | 本地数据目录、Milvus 地址、namespace |
| `knowledge.processing` | 解析、分块和索引参数 |
| `knowledge.models/model_profiles` | Embedding、Rerank 的选择及模型配置 |
| `knowledge.retrieval` | 检索路线、候选数、精排和单次正文上限 |
| `worker` | CUDA Python、模型缓存、运行目录和空闲退出时间 |

程序通过 `assemble_configuration(defaults=..., configured=..., explicit=...)` 装配配置，优先级从左到右递增。对象递归合并，数组整体替换；省略字段使用默认值。完整字段见 [config.py](../src/agentic_rag/config.py)。

每次导入保存当时的配置；每轮问答绑定一个发布版本。fixed 模式固定检索策略，auto 允许 Agent 为每次搜索选择路线与精排开关，见[检索](retrieval.md)。

选定检索参数见 [retrieval-selected.json](retrieval-selected.json)。修改解析、分块或 Embedding 配置需要[重建](model-switching.md)；单改 Rerank 或检索参数不重建文档向量。
