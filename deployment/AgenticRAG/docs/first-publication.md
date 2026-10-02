# 首次索引发布

首次建库依次完成：保存原件 → 解析分块 → Embedding 编码 → 写入 Milvus → 发布版本。`build_first_revision` 要求全部输入成功；完成前没有可查询版本。

当前使用 Milvus 3.0.1、PyMilvus 3.0.2，部署配置见 [compose.yaml](../compose.yaml)。每个 Collection 同时包含 1024 维 Dense 索引和原生 BM25 索引：

| 路线 | 索引 |
| --- | --- |
| Dense | IVF_FLAT / COSINE |
| BM25 | SPARSE_INVERTED_INDEX / BM25，standard/lowercase analyzer |

发布后，新问答使用该版本。后续[文档更新](ordinary-mutations.md)可复用兼容且具有物理归属记录的 Collection；旧索引缺少归属记录或配置不兼容时另建 Collection。构建中断后的处理见[恢复](manual-recovery.md)。
