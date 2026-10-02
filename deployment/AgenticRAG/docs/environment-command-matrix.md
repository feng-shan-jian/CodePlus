# 构建与测试命令

在仓库根目录使用 PowerShell 7。Windows 与 WSL 使用各自的 Python 环境。

## 构建和常规测试

```powershell
uv build deployment/AgenticRAG --out-dir <绝对产物目录>
uv run --isolated --project . --locked --group dev --with-editable deployment/AgenticRAG --with pymilvus==3.0.2 --with hatchling==1.29.0 python -B -m pytest deployment/AgenticRAG/tests -q -p no:cacheprovider
```

需要局部运行时，将测试目录换成具体文件。宿主测试位于根目录 `tests/`。

## 真实环境开关

| 场景 | 测试文件 | 环境变量 |
| --- | --- | --- |
| GPU 模型 | `test_model_worker_gpu.py` | `R09_REAL=1`、`R09_CUDA_PYTHON`、`R09_MODEL_CACHE` |
| GPU 调度 | `test_model_scheduling_gpu.py` | `R22_REAL=1`，同上 CUDA 路径 |
| Milvus 发布 | `test_publication_milvus.py` | `R10_REAL=1` |
| 增量索引 | `test_incremental_index.py` | `RAG_INCREMENTAL_MILVUS=<测试服务URL>` |

文件均在 [RAG tests](../tests/)；未启用的真实环境场景跳过。模型安装见[本地模型](local-model-worker.md)，检索和回答评测见[评测入口](../eval/README.md)。
