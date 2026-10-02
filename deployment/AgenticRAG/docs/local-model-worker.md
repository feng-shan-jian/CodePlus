# 本地模型安装与运行

Embedding 使用 Qwen3-Embedding-0.6B，输出 1024 维向量；精排使用 Qwen3-Reranker-0.6B。固定 revision 和文件 hash 见 [models.lock.json](../src/agentic_rag/models/models.lock.json)。模型缓存需提前准备，运行时从本地加载。

## Windows CUDA 环境

核心环境与 CUDA 环境安装同一版本的 RAG wheel。CUDA 环境使用 Python 3.14 和[锁定依赖](../requirements-local-models-win-py314.lock)：

```powershell
uv venv <CUDA环境路径> --python <Python3.14解释器>
uv pip install --python <CUDA解释器> torch==2.14.0+cu130 --index-url https://download.pytorch.org/whl/cu130
uv pip install --python <CUDA解释器> --require-hashes -r <requirements-local-models-win-py314.lock路径>
uv pip install --python <CUDA解释器> --no-deps <RAG-wheel路径>
```

在知识配置的 worker 中指定 executable、model_cache、runtime_dir 和 idle_timeout_ms，路径使用绝对路径；字段见[配置](domain-and-configuration.md)。Windows 与 WSL 分别创建环境。

## 运行方式

模型由共享 worker 在 cuda:0 上执行，使用 bfloat16 和 SDPA。单槽按需加载 Embedding 或 Rerank，空闲后退出。问答/报告优先；后台有等待任务时，最多执行四个前台批次便处理一个后台批次。

完整输入最多 2048 tokens，每批最多 4 项且 padded tokens 最多 4096，标题和模板计入长度。取消后等待当前 GPU 批次结束再释放资源。实际 GPU 测试开关见[环境命令](environment-command-matrix.md)。
