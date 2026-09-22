# 本地模型与共享 worker

R09 提供独立包真实 Embedding/Rerank 和本机进程生命周期。Windows/Python3.14.3、RTX4070 Laptop 8GiB、驱动616.92、Torch2.14.0+cu130/Transformers5.17.0/tokenizers0.23.2 是本次实测组合。Linux 在 R25，完整负载公平性在 R22，宿主 drain 在 R12；不把本项称为 Agent 问答或检索质量验收。

## 安装与配置

核心环境安装普通 wheel；CUDA 环境显式安装依赖和同一 wheel。生产启动使用目标解释器 `-I -B -m agentic_rag.models.worker`，不依赖 cwd/PYTHONPATH、editable 或 probes，不自动安装/下载。先准备与包内 `agentic_rag.models/models.lock.json` hash 一致的固定 revision 缓存；R03 探针旧锁是历史取证副本。

```powershell
uv venv <CUDA环境绝对路径> --python <Python3.14解释器绝对路径>
uv pip install --python <CUDA解释器绝对路径> torch==2.14.0+cu130 --index-url https://download.pytorch.org/whl/cu130
uv pip install --python <CUDA解释器绝对路径> --require-hashes -r <requirements-local-models-win-py314.lock绝对路径>
uv pip install --python <CUDA解释器绝对路径> --no-deps <本次wheel绝对路径>
```

`uv.lock` 保留core/dev/extra通用解析结果；Windows/Python3.14专用lock固定实际CUDA运行闭包和hash，不含pytest/build。先安装精确CUDA Torch，避免误用CPU wheel；Linux不得复用Windows环境。Windows私有目录由原生受保护DACL创建并核验既有目录，只允许当前用户和SYSTEM，不依赖早期Python3.11/3.12不支持的mkdir(0700)。

`assemble_configuration` 输入增加可选 `worker` 同级键，按同一优先级合并并记录来源；返回 `.knowledge` 和 `.worker`。操作路径不加入KnowledgeConfig、模型身份或ProcessingSnapshot v1。R08原snapshot/run JSON及指纹保持相等，旧版本实际处理库可重开。

```python
import time
from uuid import uuid4
from agentic_rag.capabilities import ModelInput, RequestContext
from agentic_rag.config import assemble_configuration
from agentic_rag.models import create_local_provider

assembled = assemble_configuration(defaults=knowledge_mapping, explicit={
    "worker": {"executable": cuda_python_absolute,
               "model_cache": model_cache_absolute,
               "runtime_dir": private_runtime_absolute,
               "idle_timeout_ms": 60000}
})
with create_local_provider(assembled) as provider:
    context = RequestContext(request_id=uuid4(), owner_id=provider.owner_id,
        purpose="qa", deadline_monotonic_ns=time.monotonic_ns() + 60_000_000_000)
    handle = provider.submit_query(ModelInput(item_id=uuid4(), text="How does version isolation work?"),
        assembled.knowledge.embedding, context)
    response = handle.result()
```

同步 `embed_documents/embed_query/rerank` 包装同一 `submit_documents/submit_query/submit_rerank`。client自有owner UUID，一个owner只能绑定一个认证session；session内请求ID不复用，达到有界请求数后上层创建新owner。每次最多4项、完整token<=2048、padded<=4096，R08冻结tokenizer先计数且不截断。Embedding标题/EOS/query instruction及Rerank三段完整模板都计入。向量1024维、有限float32 L2且保持输入顺序；重排float32 softmax([no,yes])[yes]，分数降序后ID升序。

## 所有权与边界

Windows协调域固定为原生用户LocalAppData的 `Temp/.codeplus-agenticrag-workers/cuda-0`，Linux固定在当前uid实际home；不使用调用者TEMP/TMP、data/cache/runtime_dir。此机LocalAppData继承EFS，使同目录rename实际报WinError17，故选固定Temp子目录，不解密用户目录。锁文件稳定保留且持有时不unlink。不同runtime目录、代码、依赖或设备映射不兼容时显式拒绝，不另占同一设备。

目标解释器预检只读依赖metadata。worker取得固定生命周期锁才初始化CUDA。loopback长度前缀严格JSON握手绑定256bit随机令牌、协议、instance UUID、实际解释器PID/创建身份、同boot单调时钟、真实依赖/设备映射和GPU UUID。安装模块/资源摘要启动时一次冻结，版本号不能代替代码身份。令牌不进入正常日志/报告；认证后才能请求状态或推理，无pickle/eval、请求代码或路径执行。

Schema试验默认：帧1MiB、连接16、等待16项/4MiB、session4096请求、握手3秒、帧/写操作5秒，均有硬上界。满队列WORKER_BUSY。单线程独占GPU加载、卸载、同步和推理，IPC事件循环处理取消。单槽按profile.identity复用，名称不参与；切换记录model UUID/load_count、弱引用和显存，不删除模型cache。

## 取消与实际完成

唯一绝对截止 `context.deadline_monotonic_ns` 包含客户端锁、预检/启动、验证/资产/加载、排队、切换及推理，不重置。过期/取消结果不得作为成功交付。

`cancel()` 请求取消，`cancel_ack` 只代表收到，不能据此释放R12资源。排队取消立即摘除；运行批次真正结束才发execution_finished。`inference_started`在真实第一个decoder层执行后观测。`result(timeout=...)`的可选timeout只停止本次等待，不宣称底层结束。

`wait_finished(timeout)`只接受worker完成通知，或实际原worker PID/创建身份死亡证据。断连立即失败，但保留execution_unknown；别的请求成功不能证明它完成。`close(drain_timeout=...)`阻止新提交、只取消自己、限时等待，再关自己的连接；超时保留handle供宿主继续核验，不杀其他客户/用户进程。

所有GPU异常出口在同线程同步后才返回可确认完成；同步失败进程退出，不伪造finished。OOM有阶段并清理单槽，不跨空闲期保留Torch traceback；无CPU/API/别模型替代或自动重放。无客户且无在途后idle退出。认证失败而设备锁仍持有只报错，不杀PID/另起worker。

证据见[R09](implementation-records/R09.md)：协议替身、受控真实CUDA异常、正常GPU、多解释器和两次干净核心安装分别标明。
