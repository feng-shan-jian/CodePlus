# PRE-R12 旧 RAG 清理整合：Leader 验收
状态 ACCEPTED，尚待本地提交；这是一项独立清理提交，不占用 R00–R26 编号。R12–R26 与总体验收仍未完成。

## 基线、范围和评审
基线 `cd6ed3470c1ce5619c6e8bc6791346ac577e64c1`，分支 `codex/rag`。旧任务保持只读，本轮执行者 `/root/pre_r12_cleanup` 已明确 STOP_WRITE；Leader 收回唯一写权后独立验收。执行者未暂存、提交或推送。

G01–G03：真实前置 R11 和空 index 已核对；任务卡、baseline、pre-review、当前 delta 和57项源码/测试/文档清单明确了唯一写者及保护范围。只在已有授权之内追加 test_evaluation_protocol.py 两份使用文档的哈希 seam，原题/评分/语料断言未放宽。

G04–G08：Leader 阅读实际 Agent/CLI/TUI/Remote、配置/注册/权限调用方、锁与打包补丁，结合独立只读评审 `/root/r10_publication`，未发现阻塞项。评审记录在 PRE-R12-cleanup-leader-review.json。普通 Agent 三条工具执行路径的权限、参数和 Hook 次序保留；没有第二套 Agent、旧库兼容层或新 RAG 产品入口。旧接口移除属于本清理明确授权，引用它们的调用方/依赖/文档同步处理。

依赖锁143版本条目、142唯一包名减为55；删除87、增加0、无版本升级，保留第三方 stanza 不变。README 原个人叙事前240行未改；继承状态行被本任务授权替代的原始 hunk 及归属保存在 executor-inherited-readme.json。OCR/复杂版面仍为后续范围。

## Leader 独立运行证据
实际命令、cwd、环境、退出码、时间、输出 SHA 与前后输入校验见 PRE-R12-cleanup-leader-acceptance.json 及同前缀 txt：
- 自有新环境 Windows / Python3.14.3 / pwsh7.6.5，uv --locked 安装 root host；未同步共享 core/CUDA/root .venv。执行者使用的是另一新环境 Python3.12.13。
- 完整普通宿主 + R01/R04 + 新 cleanup seam：**809 passed、3 skipped、1 warning，53.60s**，退出0。三个 skip 是未设 CODEPLUS_TEST_API_KEY、Windows rm 不可用、无适用系统 symlink 文件；warning 为既有 timeout marker。受控 LLM/SDK transport fixture 不作为网络模型证明。
- 真实 PowerShell lite/medium/full Check 全通过：50/200/2556题，609资料、6084 gold；prepare --check 612文件一致。合并测试包含真实 pwsh Answers/Replay、caller-relative 路径、失败非零及原生报告断言。
- 两个仓库外全新环境分别安装 direct wheel 和 sdist→wheel，-I -B 且无 PYTHONPATH；从 site-packages 逐文件核验、pip check、普通 CLI --help 均通过。两路 wheel SHA 均为 `f3fefb96e0896551e9ed6ded61f345153d1c90c7ec6a974148764258de85d4ab`；sdist SHA `9038a8fd94acc4890610c1d2558a00f84a97919647c01292bf6400e87e925b57`。源码包188项、wheel151项，宿主不含独立包或旧 knowledge extra/模块/资源。与执行者最终构建相同。
- Leader 在验收前后校验执行者94项交付清单、57项变更、189项测试输入、R11的63项冻结输入及R00的6553保护项；无未授权漂移。代码 freeze SHA `73fc1c80c761a16f4a57d81fbcb1aec83af76cbbe405df980f1f59a3c9a72074`，执行者 manifest SHA `0c23f0eda546efad8d2289aedb874de25f3533813902977e97e0f620274723be`。
- 另对三份 HEAD 候选逐项核验 HEAD/candidate hash；对待暂存 run.ps1 实际 pwsh AST 解析及 Retrieval 早拒绝验证通过，预期退出1，未进入解释器/旧模块路径。详见 PRE-R12-cleanup-leader-head-candidates.json。

G09：执行者自有 Temp 已清理，Leader自有 Temp 也已清理15597文件、689 reparse项、3573目录；57个只读自建 Git 对象的全部硬链接先核验均在本根内，才清除自有只读标记。未遍历 reparse、未改共享硬链接属性；所有自有 Python进程已结束。临时脚本随自有根删除；精确暂存 helper 在执行完成后自删。旧任务历史目录/自动审核拒绝未触碰。见双方 cleanup.json。

## 精确提交边界与交接
C01 ACCEPTED：本清理必需条件均通过，无 FAIL/BLOCKED。执行者两轮失败的原始报告保留；最终报告对应修复后冻结输入。

C02–C03 由 Leader 提交门核验并记录在 PRE-R12-cleanup-leader-stage.json：精确路径/删除项与 filtered Git blob 比较，三份 eval 文件仅写入已审查的 HEAD→最小清理候选；工作树始终保持实际验收的 R01版本，用户继承 MultiHop替换保留为未暂存。**不声称纯 HEAD 克隆包含全部本地未提交评测输入**。这三项不进入 root wheel/sdist，安装验证未被局部暂存改变。禁止整文件 git add 这三项，禁止 git add .、reset、stash。

首轮暂存预检在尚未修改 index 时发现已授权删除的 docs/knowledge-data-preparation-plan.md 原为未跟踪输入，不能把它作为 Git 删除项。其 before SHA 与删除授权保留在 baseline；实际29删除中28可进入提交，1项为工作树清理。修正提交路径计算后重验，不改生产代码、不 reset，失败记录见 leader-first-stage.json。

其余继承评测数据、旧 deployment/knowledge/compose.yaml 删除、新独立 compose、模型/权重/凭据/用户配置均不暂存。R11真实提交后记、checklist和本轮正式记录随此正常提交保存，不 amend。

第二次提交检查发现首次失败的 pytest 原始日志有3行行尾空白；沿用现有 records/.gitattributes 做法，仅为 PRE-R12-cleanup-executor-first-contracts.txt 精确豁免 blank-at-eol，保留原始证据 SHA。未放宽源码或自写文档检查，记录见 leader-second-stage.json。

C04–C05 在提交成功后填写真实 SHA 并核对 exact paths/blobs，作为下次正常记录提交的后记。之后从清理后基线派发 R12，在原 CodePlus Agent 的 TUI 与 -p 路径接入固定 Dense 开发能力；联网模型、GPU/Milvus真实证据、引用/预算/取消为 R12 必需验证。本轮不宣称这些已通过，Linux最终安装和全部迁移仍归后续任务。完成 R12 后继续 R13–R26，不将 M1 当作总目标完成。

## 提交后记
COMMITTED：`5dbe979a330a6b157adfba7ad3ea8299fecf73a9`，parent `cd6ed3470c1ce5619c6e8bc6791346ac577e64c1`。116路径、9180新增行/10813删除行、28 tracked删除；提交后 exact路径与blob、保留工作树哈希、空index均已核验，见 PRE-R12-cleanup-postcommit.json。未推送/发布。C04–C05通过，下一项R12；本后记随下一正常提交保存，不amend。
