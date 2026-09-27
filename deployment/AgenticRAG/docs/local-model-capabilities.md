# 本地模型能力

生产实现使用固定模型资产，经 `models/engine.py` 与 `FrozenTokenizer` 完整计数后执行。模型缓存显式配置，加载采用本地文件和 `trust_remote_code=False`。

| 能力 | 固定模型版本 | 输出 |
| --- | --- | --- |
| Embedding | Qwen3-Embedding-0.6B，`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` | 1024 维、最后有效 token、float32 L2 归一化 |
| Rerank | Qwen3-Reranker-0.6B，`e61197ed45024b0ed8a2d74b80b4d909f1255473` | 独立候选 yes/no 分数，分数降序、ID 升序 |

固定资产哈希见[models.lock.json](../src/agentic_rag/models/models.lock.json)。标题、query 指令、正文和特殊 token 共同计入完整输入；正文不自动截断。完整输入上限 2048 token、batch 最多 4、padded tokens 最多 4096，具体 profile 可使用更小边界。

GPU 使用 cuda:0、bfloat16、SDPA。不同模型配置通过单槽卸载再加载，不自动替换 CPU、API 或其他模型。响应检查身份、候选 ID、向量维度/归一化与分数范围；错误按实际阶段返回。

早期原型探针已退役。正式运行与取消、资源释放通过 `tests/test_model_worker_gpu.py` 验证；环境、句柄与跨进程协议见[本地模型 worker](local-model-worker.md)。检索与答案质量由正式评测另行衡量。
