# R22 分派：模型调度、多进程与故障完善
基线f0e1538beeedfe96d6be7ed99e422d4026ffc3b7。唯一D:/CodePlus codex/rag，新执行者独占写入，Leader独立验收，本地精确提交，不push/发布/新worktree；清理交另会话。先读R22卡/checklist、D36/D39-D41及T08，核对R09/R14/R18/R19/R21实际最终证据。

复用现有models/worker.py/client/engine，不新建调度服务或另一套GPU加载器。先测再改：当前单GPU串行有限批次，前台qa/report与后台import/rebuild两类，同级FIFO，有后台时至多4个前台批次后让1个后台批次。T08明确FIFO，不能凭“公平”自行扩大成必需owner轮询框架。用实测决定配额和batch token界限是否需要调整，并保留取舍依据。
用两个真实宿主进程证明相同实际profile复用同一worker/model实例，长导入与持续前台搜索/报告并发，记录request/owner/run/profile、提交/排队/加载/推理/完成时间线、前台等待及后台持续进度、同级FIFO。缓存文件存在不证明内存复用。模型切换/加载算入截止时间，GPU仅在实际有限批次边界让出；不声称强制抢占内核。
R18正式开启rerank对照的加载总成本已独立核过：embedding478110ms、rerank484823ms，表明切换代价真实存在；这不是直接要求多模型驻留。结合8GiB实际设备容量、批次峰值与质量预算决定必要改进，不能静默CPU/API/模型降级。
复用有界队列、排队超时、取消、client disconnect、真实completion/death回执、OOM卸载和闲置退出。取消/失效结果不能回写成功，两个客户端互不误伤；实际reader完成前pin保护不能提前释放。同库在长导入期间查询旧发布版本，新版发布后新run绑定新版；依旧使用原发布/CAS/归档协议。
正式test_model_worker/test_model_worker_gpu/test_worker_history覆盖既有协议；只补实际缺口。实际GPU正常负载与受控OOM/断连/进程终止分层报告，原失败留分母/日志，不以MockEngine吞吐作为GPU实测。最终安装包、依赖、profile/tokenizer身份与时序可核；普通host不开GPU导入。
不顺手改RAG检索策略、语料/评测分母、R23质量阈值或UI。如配置字段确需变更，保持旧冻结run/profile指纹与旧调用可读，同步调用方/测试/文档；旧11SQL不改。
交付实际改动（若现有实现满足则以必要正式验收交付，不为凑代码而重写）、定量公平性/吞吐/等待/峰值表、精确文件清单和原始命令证据、资源归属。禁止自行stage/commit，结束全部写入/命令后STOP_WRITE。
## 已核对的现有测试与验收接点
实际共享实现是 src/agentic_rag/models/worker.py（没有 runtime/worker.py）；当前单队列按同级 FIFO 选 front[0]/back[0]，front_streak>=4 时让后台。models/engine.py 缓存当前单 profile，切换卸载旧模型，响应 status 已有 model_instance_id/load_count/profile_fingerprint、queue_ms/load_ms/inference_ms/峰值等，优先复用。
正式 GPU 套件 deployment/AgenticRAG/tests/test_model_worker_gpu.py 通过 R09_REAL=1 显式启用；R09_CUDA_PYTHON、R09_HOST_PYTHON、R09_MODEL_CACHE、R09_GPU_REPORT 指定本阶段私有路径，不能跳过后称通过。该套件模块内串行使用真实 GPU。六项包括真实两进程/嵌入重排切换/取消截止队列/OOM恢复/断连死亡/协议与设备依赖拒绝/受控首层后故障；实际以 pytest 收集结果为准。test_worker_history.py 有三份历史 Git revision 的真实安装重开，不能把其历史源导出当当前源码；若修改 profile/配置/协议按影响执行，未变存储不无故重复整套历史安装。
测试/独立长导入不可与其他真实 GPU 组同时争同设备。实测前确认前项 worker 和命令已结束；异步协程取消不等于 CUDA 已释放，保持 R15/R21 actual-finished 约束。不要为测吞吐调大答题预算或修改官方语料。

并发证据须区分 RPC 负载和产品使用链：仅把 RequestContext.purpose 写成 qa/report 不能称已运行真实报告。至少用现有核心搜索和一轮现有 CodePlus Agent QA/报告与同库长导入真实重叠，保留旧版读取/最终新发布绑定的证据；性能压力可另用受控、可复现的真实 CUDA 批次，明确两类分母。没有改模型质量和研究预算的必要；无需重跑 medium 或通过反复付费报告挑结果。原批准 DeepSeek 可用于必要集成检验，凭据仅环境传入。
## 实际交接基线与资源

R21 已本地提交 f0e1538beeedfe96d6be7ed99e422d4026ffc3b7，31 个精确路径、父提交和全部 blob 已复核，index 空；提交后 945 份执行者与 423 份 Leader 原始证据再次逐项 SHA 相等。R21-leader-postcommit.json 与台账实际 SHA 更新按既有规则随下一正常阶段提交，执行者冻结保留。R21全部正式文件及此前原始失败不改写。

当前最终安装包：host 8029589bd17f6a52b70f352b0c92428ef693d629e2ce3786d9345928831b3ccb，RAG e53c92bef0fe9523e22cfa93bfca7fc6f3bd4756ee12dd9eff5cb43a51ec8da2。R21路由退修已独立71项与原输入复验通过；原不同候选树372项及真实三轮链按未变资源适用，不把重复测试相加。六轮真实 Agent 均0个knowledge_open；引用/版本/历史保全通过，但“已实际打开”等无支持措辞仍留R23，不在本项调提示或宣布质量通过。

执行者唯一私有根 C:/Users/18221/AppData/Local/Temp/codeplus-r22-executor-20260923；19554/9115。Leader未来验收私有根 C:/Users/18221/AppData/Local/Temp/codeplus-r22-leader-20260923；19555/9116，执行期不启动。交接前两根不存在，四端口无监听，R21双方自有Python进程均0；启动前再次实查。仅普通复制已结束的 C:/Users/18221/AppData/Local/Temp/codeplus-r21-leader-20260923/core、cuda 环境到私有根；copy2普通文件，不硬链接、不回写源环境。

共享模型缓存 C:/Users/18221/.cache/codeplus-agenticrag/models 只读；实际 NVIDIA RTX4070 Laptop 8GiB，Milvus3.0.1。固定回答 tokenizer SHA c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b，从前项正式环境证据定位源并复制自有根，显式传入当前配置。必要真实 Agent 使用原批准的 D:/CodePlus/.codeplus/config.yaml 回答提供方；只在进程环境传入凭据，参数、日志、证据不得包含密钥。

实现前冻结当前完整工作树与继承改动，保护根 README/README_EN、官方评测数据、旧11份SQL、前项证据/台账/本分派卡。唯一可写仓库 D:/CodePlus；不迁移 Linux、不迁移主包、不另建worktree，不派额外子agent。R22文件清单仅列本项实际变更；正式测试留仓库，临时测量和日志归属私有根，清理交用户另派。所有自有命令结束后提交STOP_WRITE；不stage/commit/push。Leader自派发后只读，待STOP_WRITE再独立审查、测试并精确本地提交。
