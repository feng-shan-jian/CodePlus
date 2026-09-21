# R01 分派单：评测依赖解耦与冻结协议

前置：R00 验收后提交；实际 SHA 由 Leader 派发时填入 R01.md。唯一集成目录 `D:/CodePlus`，分支 `codex/rag`。只允许本项执行子会话写入；Leader 同期只读，完成后收回写权独立验收。执行者不得暂存、提交、推送。

必须读取：任务规划 R01、checklist R01 与 G01–G10/C01–C05、plan D03/D29、acceptance-and-implementation 第 3–5 节及 A07/A13、R00.md 和两个 R00 JSON 输入清单。用户最新授权优先于规划文件中历史的“本轮不执行”描述。

## 允许修改

- `eval/RAG-eval/check.py`：仅本任务的中立加载/指纹接点；已有 31/+108 行替换属于继承工作，保留。
- `eval/RAG-eval/dataset_io.py`：新增标准库中立加载与指纹（若选择不同新文件名须在交付明确）。不得导入宿主或旧 knowledge。
- `deployment/AgenticRAG/eval/`：仅内部运行侧输入白名单、冻结协议和开发/验收 ID 清单，不实现检索或 Agent，不复制评分器。
- `deployment/AgenticRAG/tests/test_evaluation_protocol.py`：新增正式风险测试，含旧模块不可导入、数据一致性和 gold 隔离。
- `deployment/AgenticRAG/docs/evaluation-protocol.md`、`docs/implementation-records/R01.md` 及本项正式 JSON 证据。

禁止修改 corpus、dataset.json、questions.json、tiers.json、prepare.py、score.py、source-lock.json、upstream、validation.json、既有 tests/test_multihop_evaluation.py 或其他继承变更；禁止修改宿主业务代码。在线检索仍连旧引擎且未接新核心，R10/R17 接管。

执行期间 Leader 已追加两个必要例外并在独立验收时核对：新增 `eval/RAG-eval/replay.py` 标准库离线重放模块，仅在 `run.ps1` 加入 Replay 命令前缀接点，保留其余参数与继承改动；新增 `deployment/AgenticRAG/eval/.gitattributes`，固定 JSON 原字节以消除 Windows checkout 的哈希变化。相关正式测试、协议和证据同步更新。此授权不包含在线检索路由或整套继承评测替换。

## 必需交付

1. 保持 load_dataset 返回结构与 fingerprint 编码完全相同；以旧模块不能导入的真实子进程验证 check/score 加载。
2. 对比 R00 输入清单，逐文件哈希及集合/顺序不变；lite/medium/full 指纹与原实现一致，609 文档、2556 题、6084 证据及 null_query 分母不变。
3. medium 200 为开发 ID，full-minus-medium 2356 为验收 ID，保持原顺序且互斥，lite 嵌套；历史暴露一律不声称未见，明确已知/未知。
4. 运行侧只提供 query/ID 与语料路径，不含 answer/gold/supporting_context/题型标签等评分侧字段；评分侧继续使用独立加载。正式测试防止通过 payload 或嵌套字段泄漏。
5. check、原评分入口及现有 -Check 命令实测可用；错误分母、Top-K=10、词重合语义不变。不要运行旧在线检索来冒充新核心。
6. 交付实际命令/cwd/环境/退出码、文件清单和 SHA、限制、临时资源清理。R01.md 由执行者填证据，Leader 验收栏留待独立核查。

## 精确提交策略

R00 固定实际工作区及未提交输入，不授权提交整套继承评测替换。Leader 保存修改前 check.py 内容，用本项 before/after 差异产生接点 hunk；在原 HEAD 的相同 import 接点精确暂存，保留工作区其余继承改动。新增文件全量审阅后逐路径暂存。若本项接点无法与继承改动分离，报告阻塞，不能整文件 add 吞入它们。

旧清理补丁尚未合入；其 `check.py/run.ps1/README.md/benchmark.md/tests/test_multihop_evaluation.py` 五个重叠路径禁止整补丁覆盖，后续清理单独审阅。R01 不接管其他清理职责，不删除旧实现。
