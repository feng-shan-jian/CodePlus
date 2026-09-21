# R02 Milvus 能力与版本隔离探针

本项验证真实数据库协议，不是检索质量评测。全部 Dense 向量均为手工构造的 4 维向量；没有加载 Embedding、Reranker 或 Agent，也没有导入冻结评测语料。609 篇真实语料的完整建库成本、语义效果和生产配置仍由 R10 及后续任务验收。

## 固定环境与隔离

探针组合为 Milvus `v3.0.1`、PyMilvus `3.0.2`、etcd `v3.5.25`、MinIO `RELEASE.2024-12-18T13-15-44Z`。镜像 digest、服务器实际版本、Python/平台与前后容器清单由每次正式报告记录，不能只凭配置中的标签认定版本。

`probes/milvus/compose.yaml` 专用于 `codeplus-r02-probe` 项目：

| 资源 | 独占范围 |
| --- | --- |
| gRPC / HTTP 健康检查 | `127.0.0.1:19531` / `127.0.0.1:9092` |
| Compose 服务 | `etcd`、`minio`、`standalone` |
| named volumes | `codeplus-r02-probe_etcd`、`codeplus-r02-probe_minio`、`codeplus-r02-probe_milvus` |
| 网络 | `codeplus-r02-probe_default` |
| Collection | 每次运行随机的 `r02_<uuid>_v1` / `_v2` / `_bad_analyzer`；不创建别名 |

启动前检查端口和同名项目/卷的所有权，不覆盖已有资源。测试进一步核对项目标签、三个服务、固定镜像、卷名和端口映射；仅重启已核对 ID 的 standalone 容器。MinIO named volume 显式使用镜像声明的 `/data`，避免产生不易追踪的匿名卷。探针使用本地隔离实例的默认认证设置，不是 R25 最终部署配置。

测试结束精确删除本轮创建的 Collection，保留服务供 Leader 独立复跑。其他容器的 ID、启动时间、状态、镜像及卷清单必须前后一致；没有停止或修改既有 SonarQube/Postgres。

## Schema 与真实索引

一个 Collection 对应一个完整版本。Schema 禁用动态字段，使用手工 INT64 主键、`VARCHAR(4096)` 原文、`FLOAT_VECTOR(dim=4)` 与 `SPARSE_FLOAT_VECTOR`。文本字段启用 analyzer，明确 `standard` tokenizer 与 `lowercase` filter；原生 `FunctionType.BM25` 从 text 写入 sparse。BM25 查询仅传入原始字符串，不调用模型或自建关键词评分器。

探针 Dense 索引为单分区 `IVF_FLAT/COSINE`，`nlist=1`、查询 `nprobe=1`；Sparse 为 `SPARSE_INVERTED_INDEX/BM25`，`DAAT_MAXSCORE`、`k1=1.2`、`b=0.75`。单分区用作合成向量距离的可复算协议 oracle，同时仍核实真实持久化索引已构建；这不是对大语料的性能推荐。这些参数仅锁定本项数据库实验，不能解释为已经冻结真实语料的最佳参数。

创建 Collection 与每次搜索/查询均显式指定 `Strong`。写后读取在 flush 前执行，随后 flush、等待真实数据索引完成，再 release/load 并核对服务端报告的 sealed segments。两个索引都要求 `state=Finished`、`indexed_rows=total_rows`、`pending_index_rows=0`，并要求实际索引和 sealed 行数不低于相应数据规模。等待有 180 秒边界；空集合 Finished 或小 segment 的暴力搜索不能替代此证据。

## 版本统计实验

v1 包含 1028 行：4 个锚点与 1024 个不含 `quasar` 的填充行，足以超过该实例常规建索引行数阈值。v1 建成后不再修改。v2 是独立 Collection，先导入同一完整基线，随后在候选期删除旧成员 ID 4、增加新成员 ID 5，并加入 1024 行高频 `quasar` 文本。最终 v2 有 2052 个可见成员；索引中的物理行数可为 2053，因为删除行仍可能保留 tombstone。

用无版本过滤的真实查询保存 v1/v2 全部成员 ID、成员正文 SHA-256、BM25 排名与分数，以及稳定锚点分数。验收同时要求：

- 新版增量前，两个独立 Collection 的锚点分数一致。
- 新版增量后，新版成员和 BM25 分数发生可观察变化。
- 旧版成员、正文指纹、排名和分数保持不变；新成员不进入旧版，旧成员不因新版删除而消失。
- 客户端新进程以及 standalone 服务真实重启后，旧版和新版均保持各自快照。

分数比较容差 `rel=1e-6, abs=1e-7`；新版同分的高频词行允许内部顺序变化，不把无定义的同分次序当作版本统计变更。稳定旧版锚点的完整排名仍要求一致。该实验支持 T03 的物理 Collection 隔离选择，未验证共享 Collection 加 filter 能冻结统计，也未实现后续版本发布状态机或 GC。

## 空结果、错误与重启就绪

正常空结果包括不存在的词、过滤掉全部成员的 BM25 和 Dense 查询。负例分别访问不存在的 Collection、向真实服务请求不存在的 tokenizer，以及连接本机保留但未监听的端口；必须返回具体异常，不能返回空结果或改连其他实例。不存在 tokenizer 的服务端拒绝证明能力不兼容可以显式诊断，不代表已实测所有旧版服务器或认证/TLS 组合。

重启恢复明确区分容器健康和数据面就绪。实测出现 health 已 healthy、查询通道仍返回 `503 channel distribution is not serviceable` 的窗口。因此只在明确的重启恢复阶段，针对同一地址、同一批 Collection，以最多 90 秒的就绪循环调用 load 与 Strong count，保存每次 `503/901` 和耗时；随后重新保存真实完整快照。普通查询和能力负例没有这层恢复循环。恢复等待失败仍使本项失败。

## 成本测量与边界

报告分别记录空 Schema 创建、索引创建、load、实际插入/Strong 读/flush、等待真实数据索引与 reload 的 wall-clock 秒数。实际建库总成本需要合计相关阶段，不能只取空集合 create_index 的时间。

空间由短生命周期、无网络的 Alpine `3.22` helper 对三个已核对 owned volumes 只读挂载，再执行 `du -sk`。挂载显式 readonly 和 volume-nocopy，容器 `--rm`。记录建库前、v1 完成、v2 完成和重启后的 allocated KiB。

这是整个专用实例的观测量，包含 etcd、对象存储、WAL、元数据、既有本项探针的回收延迟和后台维护；不是单 Collection 的精确净大小。比较相邻快照可观察当前阶段开销，但异步压缩/回收可能造成噪声或负差，不能把卷总量按行数线性外推为 609 篇语料成本。R10 应在明确的冷/热状态、相同配置下另行测量真实完整版本、并存版本峰值与回收后状态。

## 复跑与清理

在 `D:/CodePlus`、pwsh 7 中执行：

```powershell
docker compose -f deployment/AgenticRAG/probes/milvus/compose.yaml -p codeplus-r02-probe pull
docker compose -f deployment/AgenticRAG/probes/milvus/compose.yaml -p codeplus-r02-probe up -d --wait --wait-timeout 240
$env:R02_RUN_REAL = '1'
$env:R02_REPORT_PATH = 'D:/CodePlus/deployment/AgenticRAG/docs/implementation-records/R02-leader-observations.json'
$env:PYTHONDONTWRITEBYTECODE = '1'
.venv/Scripts/python.exe -B -m pytest deployment/AgenticRAG/tests/test_milvus_capabilities.py -q -p no:cacheprovider
```

这条正式测试会真实创建/删除专属 Collection 并重启专属 Milvus 服务，只能串行运行。未设置 `R02_RUN_REAL=1` 的 skip 不算验收。显式启用后，服务不可用直接失败，没有 mock 替代。

Leader 复跑通过后，核对正式报告中的容器 ID、project 标签及三个 named volumes 仍属于本项，再执行：

```powershell
docker compose -f deployment/AgenticRAG/probes/milvus/compose.yaml -p codeplus-r02-probe down --volumes
docker ps -a --filter label=com.docker.compose.project=codeplus-r02-probe
docker volume ls --filter label=com.docker.compose.project=codeplus-r02-probe
```

只删除本项服务、网络和卷，不 prune 全机、不删除镜像缓存、不管理另一个 `compose.yaml`。异常退出时先根据当次报告核对随机 Collection 名和资源 ID，再做相同范围清理。正式测试、配置和验收 JSON 保留；pytest 临时目录按任务所有权删除。

参考：[Milvus 原生全文检索](https://milvus.io/docs/full-text-search.md)、[一致性](https://milvus.io/docs/consistency.md)、[官方 Compose 部署](https://milvus.io/docs/install_standalone-docker-compose.md)。最终能力结论以本仓记录的实际版本和运行证据为准。
