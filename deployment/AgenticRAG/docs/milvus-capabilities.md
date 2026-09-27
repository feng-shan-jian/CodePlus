# Milvus 能力与版本隔离

生产索引使用 Milvus 3.0.1 与 PyMilvus 3.0.2。每个知识库发布版本对应独立 Collection，Dense 向量与原生 BM25 共享同一版本成员；新版本不会修改旧版本的成员或词频统计。

Schema、analyzer、向量维度及索引配置由 `indexes/manifest.py` 和 `indexes/milvus.py` 构建。Dense 使用 IVF_FLAT/COSINE，BM25 使用原生 sparse 索引和字符串查询；纯 BM25 不调用查询 Embedding。读取使用 Strong，一次发布在完整成员、schema、索引与向量身份校验后切换指针。

正常无命中返回空结果。不存在的 Collection、服务不可达、加载或响应错误保持明确失败，不改连其他实例或返回伪空结果。索引回收必须核对物理归属与版本 pin，不操作共享服务、模型缓存或其他知识库。

部署文件为本模块 [compose.yaml](../compose.yaml)，正式服务测试为 `tests/test_publication_milvus.py`（`R10_REAL=1`）。测试使用专属 Collection，执行前确认目标 endpoint；真实模型检索使用[SciFact](../../../eval/scifact-eval/README.md)等正式语料入口。早期 4 维合成探针及其专属 Compose 已退役。

详细发布与恢复行为见[首次发布](first-publication.md)、[索引生命周期](index-lifetimes.md)及[恢复](manual-recovery.md)。
