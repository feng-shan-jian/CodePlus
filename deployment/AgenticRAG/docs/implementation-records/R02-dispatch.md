# R02 分派单：真实 Milvus 兼容、BM25 与版本隔离探针

前置 R00 `7ef7ee6c26e5a104da212a6b7788d9b884ef04fa`、R01 `79054d92fa05f06f6b2d0b4deee2a976ac7f7c4f` 均已独立验收并本地提交。集成目录 `D:/CodePlus`，分支 `codex/rag`；基线同时包含 R00 保护的继承输入，不是干净 HEAD。执行者 `/root/r02_milvus` 为唯一写入者，禁止暂存/提交/推送；Leader 收回写权后独立验收。

## 依据及范围

读取任务规划 R02、checklist R02/G01–G10/C01–C05、plan D12/D37/D42、架构 T03、验收 G0/A05/A07/A14、部署第 3.2 节及 R00 资源/保护记录。实现范围限于小规模数据库协议探针，明确合成向量，不宣称真实模型语义检索质量。

允许新增 `deployment/AgenticRAG/probes/milvus/` 下可复跑正式探针/专用 compose 资源、`tests/test_milvus_capabilities.py`、`docs/milvus-capabilities.md`、`docs/implementation-records/R02.md` 及 R02 JSON 正式证据/交付清单。不修改现有未跟踪 `compose.yaml`、宿主业务、R01 文件、冻结评测输入、根依赖/锁文件。必要额外路径先报 Leader 判断。R01 提交回填和 checklist 由 Leader 所有。

## 已核实环境与资源边界

- Windows pwsh 7.6.5，仓库 `.venv` Python 3.14.3 / pymilvus 3.0.2。可直接用于数据库探针；若确需独立环境，创建任务专属环境，不改项目根依赖。
- Docker Desktop Linux engine 29.7.2 可用。已有 SonarQube/Postgres 容器禁止修改、重启或删除。未发现 Milvus 容器；19530/19531/9091/9092 预检空闲，启动前重查。
- `milvusdb/milvus:v3.0.1` registry manifest 已真实查询成功；镜像尚未下载。现有 compose 仅作只读参考，不能使用其默认项目名管理未知数据。
- 建议专用项目 `codeplus-r02-probe`、loopback 19531/9092、独占命名 volumes 和集合前缀。记录服务版本、客户端版本、镜像 digest、项目/容器/volume/集合所有权与操作前清单。探针仅管理明确属于自己的资源。
- 临时数据结束清理；可复跑配置和正式验收报告保留。模型权重、凭据、用户语料不入 Git。图片/容器缓存不是本项需要清空的对象。

## 必需验收与交付

1. 真实实例中原生 BM25 Function、明确 tokenizer/analyzer、FLOAT_VECTOR 与 SPARSE_FLOAT_VECTOR 同 Collection；构建/加载 dense 与 BM25 sparse 索引，明确 Strong 一致性，写后查询。
2. 两个独立版本 Collection，旧版成员/排名/分数基线，新版增加大量高频词并变更成员；验证旧版成员和 BM25 统计分数不受影响，不能仅 filter/结果标签伪装。新版自身统计变化也须可观察。
3. 覆盖客户端进程重建、专用服务真实重启后可读取；正常空结果与连接错误、缺失/不兼容能力错误分开，不静默转另一实例或返回空命中。
4. 记录版本/Schema 选择、单版本构建时间与空间测量方法、小样本实测范围；完整 609 篇真实建库成本明确留给 R10。
5. 正式集成测试须显式要求可用真实 Milvus，不以 mock 通过宣称验收。交付命令/cwd/版本/退出码/实际结果、文件 SHA、测试资源清理方法；先让 Leader 可复跑同一服务，再由 Leader 复验后安全清理专用资源。

交付后停写，保留服务供 Leader 独立复跑，给出资源清单；`R02.md` 的 Leader 验收栏留空。若镜像/实例不可用，先诊断具体失败，明确 BLOCKED 而不降级。任务完成不等于项目完成，后续必须继续 R03–R26。
