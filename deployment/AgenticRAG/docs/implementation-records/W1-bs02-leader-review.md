# W1 BS02 追加精简：Leader 独立验收

工程结论：通过，等待全新独立清理与 Leader 复核、本地提交。基线为 `b7b4647598c24734df497dd6f40950dcdb9817a5`。本轮只接入四路径，未混入 W2、材料整理或 R15；未推送。

## 实际补丁与边界

冻结补丁 SHA256 `cc0bb314b56a51de00949aa3d0bce622e30848c9194ed6537b73e424814e57a6`，manifest SHA256 `c7b027e57d58b1523bb0cd1ed6e2fb5b05aeefdd8c20e24874a3f03087d5d498`。四个 before/after Git blob 逐项匹配；前三个生产/测试/ordinary 文档与已审阅 slim 一致，convergence 仅更新 BS02 行并追加本批说明，保留 Leader 既有记录。实际生产 mutations 为 +4/-30（净减26行），正式 preparation 测试 +68/-31；没有以行数宣称性能收益。

Leader 直接复核 _BuildPreparation、_build_prepared、publication._prepared/_register/_record_encoded/validate 及 Milvus read_vectors/validate 的调用链。删除内部全候选和旧材料的重复摘要；传输行使用新 dict 和独立 dense list。完整 owner token、catalog/revision/输入/配置/请求绑定、当前 owner.require、短写事务与 CAS 保留。旧向量读取逐项验证实际标量及 float32 摘要；公开入口和最终发布仍重新派生归档及验证实际索引。六个私有对象突变用例改为实际构建/传输边界用例，覆盖消费后缓冲区复用和五种索引损坏；scope/旧owner/归档损坏原门禁保留。

## 本轮实际执行

专用 copy CPU/CUDA 环境及 Docker project 为 codeplus-w1-bs02-leader-20260923，端口19542/9103。GPU 为 NVIDIA GeForce RTX 4070 Laptop GPU，Torch 2.14.0+cu130 / CUDA 13.0，CPU PyMilvus3.0.2；GPU依赖未装入CPU发行安装环境。实际安装的两环境各63份生产 py/sql/json与被测源字节一致。

- 五模块：92 passed，179.73秒，无跳过；44个已加载生产模块全部来自 site-packages，322个冻结文件无漂移。
- 正式发行安装：1 passed，9.95秒。新 wheel 为 `13784ecd6f7470b5e3ccefb9ed74588de883b059b57e0b67e1e3712ff6fe1c8f`；sdist 为 `bd558e4fb35f8824baab57fe32cfb70bb2bf9880ef42c59a73251da00fbc8120`。直接wheel及由sdist重建wheel哈希相同，分别新建 core-only 环境实际运行归档/存储/历史读回。未装 Milvus extra 时 runner 明确失败、200条错误均保留，是原正式负向断言，不是安装失败。
- 新 wheel 的真实 R13 普通变更通过：两个实际进程，旧任务在新版本发布后首次搜索/打开旧文档，另一个任务读新版；真实GPU编码、Dense/BM25、失败更新、删除、未变与空版本路径通过。
- 完整 R14 真实恢复13场景通过，包含实际进程终止、两次索引代次、提交前后、迟到模型/SDK及无物理回执边界。12个被实际终止的PID/birth已独立确认消失；正常模型进程按配置空闲退出，未把client.close当作执行完成。
- Leader另开只读连接核对14份SQLite schema8、integrity/FK、publication/artifact/current及全部登记归档的实际SHA。另用真实Milvus SDK读取迟到插入案例的新旧Collection，独立计算全量dense little-endian float32 SHA及范数、核对所有标量，核对无回执物理身份。停止本轮standalone后，发布和no-change终态重放无需runtime且不回拨当前版，再恢复本轮服务。完整结果见 crosscheck.json。

本轮没有新产品测试失败；原负向安装证据保持FAIL语义。只读路径定位/辅助命令不计产品测试。既有完整core/host、609文档处理、真正v7到v8升级、删源历史引用结果仍属于上一批的原始包身份，本轮未重新执行或冒称这些已覆盖新版本。SQL、配置、依赖、宿主及归档格式未变；没有质量或端到端提速结论。

## 清理与提交状态

等待全新独立清理会话处理本轮自有根与3容器/3卷/1网络，再由Leader核对保护哈希、共享快照和剩余资源。此前三处 Leader 自动审批拒删范围及 bf43 的所有资源均保护，不重试。bf43 v5之后另有W2七项CPU测试的新目录，不能据v5声称它已清理。所有原始失败和历史记录保持，不合并改写。

本批同时携带 W1-batch2-leader-stage-execution.json 和 W1-batch2-leader-postcommit.json；它们记录真实 b7b4647 的113路径提交，不 amend 前一提交。

## 2026-09-23 最新提交判定

用户明确要求主线继续功能实现，清理收敛由用户另派会话处理，不再等待清理或材料整理。Leader 据此撤销后续清理门槛；本批工程验收通过，可以精确本地提交，然后进入 R15。提交前重新核对322个被测文件、206个生产文件、6553个保护输入、297个此前审阅文件、两份后记、8份SQL、7份规范、35份本批审阅文件和411个ignored条目，均与冻结记录一致。其后仅按新指令修订流程/checklist及本说明、convergence状态，生产和测试不变。

本批清理代理收到停止指令时原删除命令已经执行，随后原脚本内置读回也已结束。其报告记载本轮根及3容器/3卷/1网络已移除，172661个共享节点相对v2-predelete零差异；这些是独立代理的交接结果，主线未新增清理复核轮次。Leader此前已完整复算6157个共享普通目录size变化和17个自有普通目录attributes变化，原严格FAIL及原因未知结论保持。停止交接记录为 W1-bs02-independent-cleanup-user-stop-handoff.json；此前其他拒删范围与bf43仍交给用户另派会话。最终提交范围与真实SHA分别由本批stage/postcommit记录给出，不推送。
