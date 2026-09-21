# 本地模型能力实测（R03）

执行日期：2026-09-22，Windows，基线 `776025c30ddbf51ecdcd26b16f9ce891c749b706`。执行者 `/root/r03_models`；正式集成测试 **6 passed in 42.39s**。这是有界真实 GPU 能力探针，Leader 验收及正式 worker 仍分别进行。

## 固定身份与环境

| 能力 | 模型 / 精确 revision | 输出与输入约定 |
| --- | --- | --- |
| Embedding | Qwen/Qwen3-Embedding-0.6B / `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` | 1024 维；left padding 最后有效 token；转 float32 后 L2 归一化；文档不加 instruction，query 必须加 |
| Reranker | Qwen/Qwen3-Reranker-0.6B / `e61197ed45024b0ed8a2d74b80b4d909f1255473` | 每个候选独立；最后位置的 `[no, yes]` logits 转 float32 后 softmax 取 yes；按分数降序、ID 升序稳定排序 |

两个模型均 Apache-2.0。各权重、config、tokenizer、vocab、merges 和模板文件的实际 SHA-256/官方 blob/LFS 身份在 [models.lock.json](../probes/models/models.lock.json)。每次加载全量核验哈希，再以本地目录、`local_files_only=True`、`trust_remote_code=False` 装载；没有 main、远程代码或模型替换路径。相同维度不构成兼容证明；后续索引身份应包括 revision、tokenizer/模板、pooling、归一化、dtype 和实现。完整 profile 指纹在原始报告。

实际环境：Python 3.14.3、Torch 2.14.0+cu130、CUDA runtime 13.0、Transformers 5.17.0、cuDNN 92400、驱动 616.92、NVIDIA GeForce RTX 4070 Laptop GPU（8188 MiB）。全部参数实际为 cuda:0/bfloat16，attention 实际为 SDPA，`eval()`/`inference_mode()`/`use_cache=False`。38 个依赖版本见 [environment-win-cuda.freeze.txt](../probes/models/environment-win-cuda.freeze.txt)。独立环境和固定权重位于用户 `.cache/codeplus-agenticrag/`，不进入 Git；未改变根依赖锁、既有 `.venv` 或全局 Python。

## 完整输入与边界

文档正文保留不变；有标题时模型检索文本为 `Title: {title}\n{text}`，无标题时直接使用正文。Embedding query 为 `Instruct: {instruction}\nQuery:{query}`。固定英文 instruction、Reranker 的官方 prefix/body/suffix 全文、实际完整输入及计数都保留在 [原始报告](implementation-records/R03-observations.json)。没有额外翻译模型。

Reranker 明确采用官方 Transformers 示例的 prefix token + body token + suffix token 拼接方式，计入 query、标题、正文、instruction 和特殊 token，绝不自动截断。`logits_to_keep=1` 的短输入结果与完整 logits 最后位置 oracle 完全相同（最大分数差 0）。此 yes/no 分数不当作答案置信度，也不能与其他模型的分数直接比较。

R05/R09 可采用本次有依据的保守起点：完整输入上限 2048 token、batch 1–4、`batch_size * max(input_tokens) <= 4096`，单模型串行驻留，PyTorch allocator 配额 2048 MiB。已实际执行 Embedding 2048×1/2、Reranker 2047×1/2，并测下表的其他规模；未穷举范围中每个组合，不是吞吐 SLA。架构 config 上限分别为 32768/40960，公共模型卡标 32k；这些值均未作为本机可执行长上下文承诺。

长正文、长标题、长 query、混合批次中的超限项均在 GPU 推理前返回 `INPUT_TOO_LONG`；全候选先校验，失败不返回部分向量/排名。超出 padded-token 额度返回 `BATCH_TOO_LARGE`。稳定候选 ID 在 batch 1/2/3 重组后均对齐；文档和 query 向量数量、顺序、1024 维、有限数值与范数验证通过。合成 Beijing/无关材料样例只证明调用与基本排序正常，不是检索质量验收。

## 加载、切换与测量

每项 GPU 计时前后均 synchronize，单位毫秒；峰值是进程 PyTorch allocator 的 MiB，包含已驻留模型。batch 计时包括张量传输、forward 和结果回传，完整 tokenizer 检查在计时外。表内常规规模各 3 次，长输入各 1 次；不据此报告 P95。

| 能力 | batch | 每项完整 token | 耗时 ms（范围） | peak allocated MiB | 次数 |
| --- | --- | --- | --- | --- | --- |
| embedding | 1 | 102 | 28.15–31.80 | 1147.15 | 3 |
| embedding | 2 | 390 | 39.34–52.59 | 1165.96 | 3 |
| embedding | 4 | 774 | 173.19–176.77 | 1223.91 | 3 |
| embedding（长输入） | 1 | 2048 | 122.80 | 1197.79 | 1 |
| embedding（长输入批次） | 2 | 2048 | 259.83 | 1250.07 | 1 |
| reranker | 1 | 179 | 26.72–33.49 | 1151.63 | 3 |
| reranker | 2 | 467 | 42.27–45.75 | 1169.50 | 3 |
| reranker | 4 | 851 | 191.78–205.36 | 1232.57 | 3 |
| reranker（长输入） | 1 | 2047 | 116.04 | 1197.77 | 1 |
| reranker（长输入批次） | 2 | 2047 | 265.24 | 1250.06 | 1 |

Embedding 新实例加载 1558.21 ms，资产校验/tokenizer 准备 963.66 ms；Reranker 分别 1106.59/964.27 ms。Embedding → Reranker → Embedding 顺序切换全部执行；最后 Embedding 重载 1009.55 ms，准备 918.87 ms。两次首次卸载分别 131.48/119.64 ms。这里“冷加载”指新模型实例；OS 文件缓存未清空，加载前 BF16 matmul 已初始化 CUDA BLAS。

在任何模型加载前，最小真实 BF16 matmul 完成并释放临时张量后，allocator 基线为 allocated 8.125 / reserved 20 MiB。三次模型卸载各检查 313 个 model/parameter/buffer 弱引用全部消失，显存均回到同一基线；不是仅凭数值猜测模型已释放。公开 API 返回 cuBLAS workspace 配置 8.125 MiB。该库级工作区留到进程退出；测试主进程 PID 98400 与所有故障子进程均已退出。独立复跑仍需重新验证。

该机桌面程序在使用 GPU，本次开始前 nvidia-smi 约 4395 MiB 已用、3554 MiB 空闲。Windows WDDM 下 `torch.cuda.mem_get_info()` 报告的空闲 7068 MiB 使用另一口径，不能与 nvidia-smi 互换或视作额外独占显存。探针 2 GiB allocator 配额不是整卡极限、最低显存要求或全部进程显存。复跑前人工核对 nvidia-smi 至少 2304 MiB 空闲，不终止其他进程；正式 admission control 留 R09。

## 故障、复现与限制

三个隔离真实子进程分别执行无 site-packages 的 `-S`、隐藏 CUDA 设备，以及在 32 MiB allocator 配额下实际加载 Embedding。均以退出码 2 返回 `DEPENDENCY_UNAVAILABLE`、`DEVICE_UNAVAILABLE`、`CUDA_OUT_OF_MEMORY`。OOM 真正触达 `LocalProbe.load()`，第一个约 298 MiB 参数迁移被拒绝，未返回模型结果；未占满整卡。原始 CUDA 错误中 Windows/NVML 的巨大进程内存哨兵值不用于定量结论。UTF-8 stdout/stderr 明确处理并保留必要诊断。

复现命令和环境安装见 [探针 README](../probes/models/README.md)，精确命令、首次失败与修复见 [R03 执行记录](implementation-records/R03.md) 和 [执行证据](implementation-records/R03-evidence.json)。首次 5 passed/1 failed 的原始观察另行保留：错误是假定卸载应为零，以及子进程 stderr 的 GBK 解码 warning；以模型前基线、弱引用和 UTF-8 修复后完整重跑通过，没有隐藏失败或调节评分 gold。

未验证共同驻留、两客户端/共享 worker、调度、自然物理显存耗尽、CPU/API fallback、Linux 安装、完整语料质量、中文 Agent 查询改写或生产吞吐。正式 worker 属 R09，跨平台安装属 R25，本探针不替代这些验收。

参考：[Embedding 固定模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/blob/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/README.md)、[Reranker 固定模型卡](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/blob/e61197ed45024b0ed8a2d74b80b4d909f1255473/README.md)、[PyTorch 2.14 CUDA 环境变量](https://docs.pytorch.org/docs/2.14/cuda_environment_variables.html)。模型卡与源码只是约定依据；当前可用性结论来自本次 GPU 原始记录。
