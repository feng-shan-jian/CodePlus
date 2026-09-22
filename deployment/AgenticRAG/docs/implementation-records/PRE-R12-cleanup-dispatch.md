# PRE-R12：旧 RAG 清理整合任务卡

状态 RUNNING；执行者 `/root/pre_r12_cleanup`。这是一项经 R04 冻结的独立清理整合提交，不占用或替换 R00–R26 编号。全项目仍需继续 R12–R26，总体验收未完成。

## 基线与所有权

唯一集成目录 `D:/CodePlus`，分支 `codex/rag`，前置 R11 已独立验收提交 `cd6ed3470c1ce5619c6e8bc6791346ac577e64c1`；parent R10 `81bf1461c73d9d640144eb1ed8b9a193d0f4f953`。Leader已逐项核对 R11 55 个提交路径与blob、空index。当前 R11.md 和 checklist 的真实SHA后记由 Leader 保有，随本次正常提交保存，不amend。

严格等待 Leader 明确 WRITE_GRANTED 后方可写文件、测试、安装、启动进程。交权期间仅执行者一个写者；子评审只读。不得stage/commit/push，不得恢复或重置继承改动。交付后明确 STOP_WRITE，停止全部文件/环境/服务操作，由Leader独立验收。

读取用户最新指令和适用AGENTS，然后读取 `docs/host-integration-contract.md` §7、R04记录、R01记录和评测契约、R11交付及现有调用者。采用已读 `C:/Users/18221/.codex/skills/code-refactor/SKILL.md` 和reference；不据此增加未经用户要求的确认门。

旧任务“清理并删除 CodePlus 旧 RAG 实现”，ID `01a0c3a2-0ef4-72d3-ac46-9da178c4e6cf`，保持只读，不唤醒。旧 worktree `C:/Users/18221/.codex/worktrees/144f/CodePlus`，HEAD `90811304e8214db6cff0fad5c3e5593955451591`。旁边 `rag-cleanup-review/cleanup.patch` SHA `ddaeacc7e481ea29958bfcef7dcb1e7a1f15fc79a8a38242a3012ef46511aedc`，`changes.json` SHA `33ee88e37f4e9194f9d7641390d05ebcaa761dea05cb35563389f833a2b449d8`。这些是相对于旧脏工作区的候选，非当前HEAD可盲用补丁。

Leader新核对见 `PRE-R12-cleanup-baseline.json`：56个旧交接路径after全部吻合，当前before仅R01的check/run两处不同；6553保护项无额外漂移，R11 63冻结项一致。只读评审见 `PRE-R12-cleanup-pre-review.json`，历史测试718/3skip和197以及旧报告都不作当前通过证据。

## 允许范围与禁止范围

基础白名单是该baseline的 `paths` 中标记 TASK_SCOPE_SUBJECT_TO_INHERITED_HUNK_PROTECTION 的路径。逐文件读取 before/current/old after，再实施一致的删除或调用方调整；禁止整包apply、整工作树覆盖或带入无关hunk。包括旧knowledge模块、tools/knowledge、命令菜单/handler/source_preview、旧专属docs/tests，以及Agent/CLI/TUI/config/prompts/session/Remote/validator/web rendering依赖的对应移除、根pyproject/uv.lock和必要使用文档。保留普通Agent的工具权限、hooks、MCP、团队/子Agent、压缩、输出、会话恢复及远程协议。

明确禁止：
- `eval/RAG-eval/check.py`、`tests/test_multihop_evaluation.py` 字节变化；旧patch对这两者不接收。
- `dataset_io.py`、`replay.py`、`score.py`、`prepare.py`、原题/609语料/gold/upstream/source-lock/tiers/query runtime inputs和R01正式协议变化。不得顺带提交完整继承MultiHop替换。
- 继承 `deployment/knowledge/compose.yaml` 删除、独立 `deployment/AgenticRAG/compose.yaml`、模型权重/缓存、凭据、用户数据与旧任务Temp目录均不处理。
- 独立核心 src、既有正式测试、既有R00–R11报告、spec、默认模型/配置不变。R12新hook/第二Agent/新产品入口/旧库兼容层不在本项。
- Leader保有 checklist、R11.md提交后记、本dispatch、baseline、pre-review；执行者不得覆写。

特许最小R01接缝：`eval/RAG-eval/run.ps1` 不接收旧patch，但允许在当前R01版本上保留既有参数签名、Check/Answers/Replay和native report wrapper，移除仍调用已删 `codeplus.knowledge` 的 Retrieval 分支。-KbId/原Retrieval参数应在旧模块调用和报告生成前准确报“旧检索引擎已移除，新引擎宿主/评测接入尚未完成”，不得静默成功或发明新入口。删除旧 --extra knowledge 安装提示并改成实际有效命令。保留caller-relative路径、数据完整性检查、官方评分、失败/null分母及原wrapper。这里只是暂时准确报错，新引擎正式接入归后续计划。需新正式seam测试可写 `deployment/AgenticRAG/tests/test_pre_r12_cleanup.py`；既有R01测试不放宽。

README.md/README_EN.md第一240行个人叙事保持字节语义；保护继承状态行归属，移除/替换仅已授权旧RAG段落。若同一旧功能状态行被继承改动与清理共同覆盖，保留R00 preimage和基线hash，在报告说明授权替代，不冒称继承内容自创；其余继承hunk不入提交。旧候选README“独立模块尚未实现”已过时，应准确写核心R00–R11已在独立区验收、宿主还未接入；未来OCR路线保留其未来语义，不改成首版交付。

根依赖只删旧RAG专属extra与失效资源force-include，无升级。旧候选按唯一包名142→55，删87、新增0，第三方保留package stanza完全相同；执行者对当前最终锁复核。根sdist不能打入独立开发包/验收数据；root wheel/sdist均应能实际构建安装，普通host可用。两个平台/长期环境隔离，不修改共享core/CUDA/root环境；使用自有新Temp宿主环境。

可新增正式执行记录 `docs/implementation-records/PRE-R12-cleanup-executor*.{md,json,txt}`、精确manifest、正式安装/回归报告和必要正式安装验收helper `tests/pre_r12_cleanup_install.py`（位于独立开发区的tests）。安装/测试helper必须有实际边界价值，不为一次性调试保留；其他临时文件任务结束前清理。如确有新调用者路径必要，先给Leader实际证据再扩白名单，不问用户常规技术选择。

## 执行与验收

1. 保存本轮精确current→final差异和修改前输入hash，逐项处理旧交接并复核注册、反射/字符串入口、配置、模板、打包和调用方。不是“rg无命中即通过”：历史规格/报告的旧命中保留正确语义。
2. 全量普通宿主正式tests在真实新环境执行；先近处后全量，所有失败/skip保留并解释，修复范围内回退，不删除/放宽无关测试。验证Agent权限/hooks/MCP/subagent、CLI两种输出、TUI命令/session和Remote stream/replay相关实际路径；受控LLM fixture明确为fixture，不声称联网Agent验收。
3. 复跑R01正式 `deployment/AgenticRAG/tests/test_evaluation_protocol.py`、root `tests/test_multihop_evaluation.py` 和lite/medium/full check、prepare --check。对Check/Answers/Replay三条真实pwsh入口及Retrieval早拒绝出具精确command/exit/report，无真实GPU/Milvus/模型需求，不启动它们。引用既有R01用例复用，不能只测Python绕过PS入口。
4. 重定位R04宿主接点、复跑 `deployment/AgenticRAG/tests/test_host_contract.py`；记录旧行→清理后实际文件/行。若实验仅旧knowledge名称导致fixture不再成立，提供证据再作最小同语义更新，不擅改R04契约。
5. 真实构建root direct wheel与sdist再构建wheel，在仓库外两个全新环境安装，-I -B无PYTHONPATH，从site-packages导入，普通CLI/help可用、旧knowledge/资源/extra不存在，独立新引擎暂不偷偷打入host。核验uv lock保留版本和平台marker/引用一致；本任务不提前声称R25Linux最终部署或R12联网Agent通过。
6. 最终hash冻结实际源码/测试/lock/文档，精确manifest不混Leader资料。核对R00保护基线只允许本项授权路径差异、R11代码63项不漂移，记录必须排除的继承hunks。特别run.ps1/eval README/benchmark等current与Git HEAD不同：提供本项语义delta及Leader精确hunk暂存依据，不能交整份文件让Leader误stage继承替换。
7. 清理仅自有已核验绝对Temp根；无reparse遍历，无共享硬链接属性修改，所有子进程结束，原始正式命令记录保留。不要绕过旧历史目录的自动审批拒绝。然后明确STOP_WRITE，报告真实测试/安装/skip、全部路径/hash、剩余问题与修复记录。不得自标ACCEPTED/COMMITTED。

Leader随后独立实际读取和复跑，失败退回修复；通过后精确hunk/文件本地提交。提交后保存真SHA，R12在新基线接hook，不能再套旧清理patch。用户已授权此流程，不需逐项询问继续，不push、不发布。
