# 环境与验证命令

Windows 使用 PowerShell 7。宿主、独立 RAG 包与 CUDA worker 按实际安装环境指定 Python；Windows 与 WSL 不共用虚拟环境。

| 范围 | 正式入口 | 验证内容 |
| --- | --- | --- |
| 核心构建 | `uv build deployment/AgenticRAG --out-dir <绝对产物目录>` | wheel/sdist 安装、资源和可选依赖边界 |
| 核心回归 | `python -B -m pytest deployment/AgenticRAG/tests -q -p no:cacheprovider` | 检索、来源、发布、恢复和宿主绑定；真实环境测试显式启用 |
| 实际 GPU | `test_model_worker_gpu.py`，设置 `R09_REAL=1`、`R09_CUDA_PYTHON`、`R09_MODEL_CACHE` | 生产 worker、模型身份、取消和完成回执 |
| 实际 Milvus | `test_publication_milvus.py`，设置 `R10_REAL=1` | 生产 schema、发布、错误与物理版本隔离 |
| 宿主 | `tests/test_agent.py`、`tests/test_context.py`、`tests/test_serialization.py`、`tests/test_subagent.py` | 普通工具循环、compact、流式、权限与协作 |
| MultiHop | `eval/RAG-eval/run.ps1 -Check/-Replay/-Answers` | 冻结数据与原计分口径 |
| Agent 运行 | `deployment/AgenticRAG/eval/run_agent.py` | 普通 Agent 答案和实际来源交付记录 |
| SciFact | `eval/scifact-eval/retrieval_runner.py` 与 `summarize_retrieval.py` | development/test 独立运行、阶段召回与最终正文 |

真实 GPU、数据库和供应商执行与受控测试分别记录。运行命令及数据要求见[开发评测](../eval/README.md)与[SciFact](../../../eval/scifact-eval/README.md)。模型缓存及用户库不用于临时测试清理。
