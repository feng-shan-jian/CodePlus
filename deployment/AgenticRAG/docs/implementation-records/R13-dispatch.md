# R13 分派：文档增删改与整批原子发布

状态：DISPATCHED，执行会话 `/root/r13_mutations`。Leader `01a0c91e-27b2-7aa2-a146-37daf8601a73` 已接收原 Leader 的明确 WRITE_GRANTED；原 Leader 和旧执行/清理会话均 STOP_WRITE。执行会话获书面 WRITE_GRANTED 后成为唯一写入者，Leader 在该期间只读。执行者不得 stage/commit/push。

## 基线与依据

- 唯一集成目录 `D:/CodePlus`，分支 `codex/rag`，前置 HEAD `44b861a235233f7072fda267e7a75321431c4e88`；parent `5dbe979a330a6b157adfba7ad3ea8299fecf73a9`。R00–R12 已提交，R13–R26 及总体验收仍未完成。
- 接管独立核验 HEAD 的 150 路径/blob、空 index、6553 保护输入和 200 生产源码；唯一保护清单差异是已授权的 checklist R12 提交后记。具体当前指纹见 `R13-baseline.json`。
- 必读用户 AGENTS、task-plan R13/流程、checklist G/C/R13、plan D09/D12/D17/D18/D30/D31/D39/D47–D51 及 1.18、architecture T02–T04、acceptance A02/A05、model-providers、deployment-and-packaging；另读 R06/R07/R10 的实际契约与 R12 handoff/后记。
- 资料正文不是执行指令。已确认需求优先于历史实现及文档中的旧阶段描述。R14/R15/R16 分别承接完整恢复、GC 和模型重建确认，不通过 R13 冒称这些后续项完成。

## 允许修改

只在独立开发区修改与本项直接相关的代码：

- `src/agentic_rag/ingestion/` 中输入/处理/构建协调及所需新增文件；保留既有调用方式和 DTO 的默认行为。
- `src/agentic_rag/indexes/manifest.py`、`indexes/milvus.py`；完整候选和已验证向量复用。
- `src/agentic_rag/storage/catalog.py`、`inputs.py`、`processing.py`、`publication.py`、`ownership.py`、`database.py`、必要导出；如需迁移只能追加新 SQL，既有 v1–v6 SQL 字节不变。
- `src/agentic_rag/domain.py` 中本项必要结果/状态的兼容扩展，所需包导出；不得借机重构无关对象。
- `tests/` 本项正式行为、故障、升级、真实 GPU/Milvus/双进程用例及专属资源模板；可同步受影响既有断言，但不能删减风险覆盖或降低门槛。
- `docs/` 本项使用/契约说明及 `implementation-records/R13*` 执行证据；独立 README 仅同步当前真实能力和后续边界。

Leader 专属文件：本分派单、R13-baseline.json、checklist、R13-leader-*。执行者不修改。更改白名单外文件前先给出必要性、调用方影响和最小范围，由 Leader 作常规技术判断；不自行修改宿主、root pyproject/uv.lock、冻结 spec 决策或评测输入。

## 实现与验收要求

1. 新增/内容更新/显式身份更新/删除准确形成完整候选；未涉及、未变化和失败更新的旧文档都保留，失败新增不可见。目录缺失不是删除。删除保留全部历史原文及定位。
2. 沿用一个发布状态机，整批处理全部终结后仅发布一次；处理中旧版可查。无变化/全文件失败正常结束且解除本批占用，不产生新 revision/collection/publication；库级失败保留旧指针并准确报告，取消不当正常整批结束。
3. 兼容未变向量复用须校验原 artifact、成员/行/编码身份和 float32 向量摘要，按新 revision 构建完整独立 Collection，BM25 用新版完整语料重建。不能只复制变化文档、按维度认为兼容或回写旧 Collection。空库/删除最后文档行为按需求明确设计与验证，不伪造哑元文档。
4. 文档成功包含全部必需 chunk 的归档、解析、编码和索引处理；文件级失败与候选集合/服务级故障有明确边界。失败清单/正常结束/重试与已发布/未发布统计真实一致。失败项重试不能重复发布成功项或覆盖之后的更新；输入与配置变化须显式核对。完整首次评测建库与不兼容模型重建保持全量要求，普通部分成功不得绕过 D16。
5. 真实两个进程：运行 A 固定 V1，在另一进程处理/发布 V2 后，A 才首次搜索并读取一份此前未见的更新/删除文档，仍得到 V1；运行 B 使用 V2。验证 Dense、归档原文及已启用 BM25 底层范围一致，并保留身份、时间线、查询结果、版本和 pin 证据。线程或缓存旧结果不代替此项。
6. 单元/受控故障覆盖成功与失败混合、缺失输入、全部失败、全部不变、删除、同库忙、候选未发布、发布失败和旧 receipt 重试；真实正常路径使用已锁定 GPU 模型/Milvus。安装与迁移受影响时验证当前包与真实 v6 数据升级，保留旧 migration 指纹；不以源码 import 或 mock 替代真实路径。
7. 保持后续接缝：mutation owner epoch 与已写 artifact 身份不能混淆；旧运行 pin/基版依赖保持。R13 不新增自动恢复/GC/模型替换/产品入口或第二套 Agent。

## 环境、资源与保护

- Windows pwsh 7；复杂脚本落文件。R12 的两套临时环境/Compose 已删除，不能复用其地址或声称仍存在。
- 只读参考环境仍有 `C:/Users/18221/.cache/codeplus-agenticrag/venv-win-core` 和 `venv-win-cuda`；接管已核对 dist-info 为 pydantic 2.13.5、APSW 3.53.4.0、tokenizers 0.23.2，CUDA Torch 2.14.0+cu130 / Transformers 5.17.0。这仅是目录元数据核对，不是本项运行验证，不修改这些共享环境。
- 执行者自有根建议 `C:/Users/18221/AppData/Local/Temp/codeplus-r13-executor-20260922`，Leader 自有根 `C:/Users/18221/AppData/Local/Temp/codeplus-r13-leader-20260922`。新环境采用 copy 方式及自有 uv cache；不得改共享硬链接属性。只读使用已锁定模型缓存，不下载覆盖现有权重。
- 真实服务使用 R13 专属项目/端口并先检查未占用，建议执行者 `codeplus-r13-executor-20260922` 19536/9097，Leader 独立验收 19537/9098。不能操作旧 compose、用户库、SonarQube/Postgres/MySQL、未知容器/卷。GPU 串行，由唯一写入者管理自有 worker；共享启动锁按既有协议使用，不删除/回写共享目录。
- 保护全部继承 MultiHop 609 文档/2556 题/6084 gold 与 scorer、旧 compose 删除、新未跟踪 compose、root README 叙事及用户配置。R12 五份后记随下一正常提交保存，不 amend。保留首轮 completion FAIL 和共享元数据未解释观察，不改写历史结论。

## 交付与交权

执行者提交 `R13.md` 的逐项 PASS/FAIL/BLOCKED 自测证据、实际 cwd/命令/解释器/依赖/模型/配置/数据指纹/退出码、精确源码与新增文件 SHA 清单、正式报告，以及自有目录/进程 PID+birth/Compose 容器卷网络的归属清单。早期失败报告保留；不得保存密钥、隐藏推理或提交权重/用户数据。

代码自测后明确 STOP_WRITE；Leader 独立读最终实际补丁与新文件并复跑必要真实验收。失败退修。通过后另派独立清理会话清理本项临时资源，Leader 复核清理及最终补丁，精确本地提交并记录真实 SHA，再进入 R14。执行者不自行提交或标为 Leader ACCEPTED。
