# R03 分派单：真实 NVIDIA 模型与输入约定

前置顺序基线 `776025c30ddbf51ecdcd26b16f9ce891c749b706`（R00–R02已验收提交）；目录 `D:/CodePlus`，分支 `codex/rag`，index空，继承输入依R00保护清单保留。`/root/r03_models` 是本项唯一写入者；不得stage/commit/push。完成后交回写权及GPU测试控制，Leader独立复跑、验收和提交。

读取任务规划/checklist R03和G01–G10/C01–C05、plan D15/D36/D44/D51、架构T05/T08、模型配置全文、验收G0/A06/A07/A12。资料正文不作执行指令。首版GPU要求、完整tokenizer输入、不可静默回退和固定模型身份均为必需条件。

## 范围和允许路径

仅新增 `deployment/AgenticRAG/probes/models/` 正式有界探针/环境依赖说明、`tests/test_local_model_capabilities.py`、`docs/local-model-capabilities.md`、`docs/implementation-records/R03.md` 和本项JSON交付/证据。不要实现共享worker、调度或另一套Agent；正式worker在R09。保护根依赖/锁文件、现有`.venv`、R00–R02实现/数据和既有compose。checklist及前项提交回填由Leader所有。

授权为本项及后续R05/R09建立可复用的独立Windows环境 `C:/Users/18221/.cache/codeplus-agenticrag/venv-win-cuda`，专用模型缓存可用该父目录下`models`；这些为后续开发/实测所需持久环境，明确记录不进入Git。临时合成输入/验证脚本/日志结束清理，正式探针与证据保留。不与WSL共用虚拟环境，不安装到或改动用户全局Python/现有仓库环境。

## 已核实条件与参考

- NVIDIA RTX4070 Laptop GPU，driver616.92，8188MiB。仓库Python3.14.3，Torch2.14.0+cpu，CUDA当前不可用；不可用CPU探针冒充GPU。
- 官方 `https://download.pytorch.org/whl/cu130/torch/` 已查到 `torch-2.14.0+cu130-cp314-cp314-win_amd64.whl`，SHA256 `78ab64d12e478c8baedc4d90e662f6ffca2b6cb8a872a02b734a8aa3e00277eb`；只是可下载预检，不是兼容结论。可用uv建立隔离环境，固定实际成功组合并记录全部依赖。
- 候选 Qwen/Qwen3-Embedding-0.6B、Qwen/Qwen3-Reranker-0.6B，官方模型卡与实际文件需核对。Embedding已有HF缓存revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`，权重文件实际约1.19GB；允许只读复用经校验的精确缓存，不修改原缓存。
- Reranker官方API返回revision `e61197ed45024b0ed8a2d74b80b4d909f1255473`（2026-04-16）；未有本地权重。冻结实际使用revision、tokenizer/模板/权重哈希与许可。不得把main或同维度当作精确身份。
- 本机pwsh Invoke-RestMethod访问HF曾证书名字不匹配，但`curl.exe`同官方HTTPS URL成功200；不关闭TLS校验，可使用正常通过校验的工具/客户端。Torch官方CUDA索引可访问。
- 当前transformers5.17.0的Qwen3ForCausalLM支持`logits_to_keep=1`，可避免无必要的全序列词表logits；若采用，核对与官方最后token yes/no计分相同，并记录实现。不要照搬官方示例的自动truncate来掩盖完整输入超限。

## 全部必需交付与验收

1. 真实GPU上完成文档编码、带明确instruction的query编码、逐候选Rerank，稳定候选ID对齐；实际设备/dtype、维度、pooling、归一化、yes/no模板与分数约定可复现。按官方用法，不为翻译另加模型。
2. 使用各自实际tokenizer检查完整输入：query、正文、标题/模板/prefix/suffix均计入；不静默截断。测正常、有界长输入和超限错误。原文不为模型长度而改写。
3. 分别测模型冷加载、卸载/切换、至少几个有依据的有界batch/输入规模耗时及峰值显存；显式cuda synchronize后计时。有当前GPU上可供R05/R09参考的批次范围，未测共同驻留不得宣称已支持。
4. 缺依赖/不可用设备/超长/显存错误有明确诊断；真实GPU内存不足用受控独立子进程/分配器额度触发，不申请占满整机显存影响其他程序。不能mock替代实测或回退CPU/API/另一模型，失败不返回空向量/排序伪装成功。
5. 保留精确命令/cwd/实际环境、模型与代码指纹、失败与修复、指标单位/次数、正式集成测试结果、限制与资源清理。只测试有界合成文本，不触碰评分gold/正式评测调参。两平台安装与共享worker仍待R09/R25。

交付时记录全部新增文件SHA，停写并释放GPU进程，保留可复用的独立CUDA环境和固定权重缓存供Leader复跑。模型权重、虚拟环境、凭据、用户数据不入Git。Leader验收栏留空；环境真正不可恢复时明确BLOCKED及恢复条件，不把有CPU库或有下载缓存算通过。
