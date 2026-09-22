# PRE-R12 旧 RAG 清理整合执行交付

状态：执行交付，待 Leader 独立验收；未暂存、未提交、未推送。基线 `cd6ed3470c1ce5619c6e8bc6791346ac577e64c1` / `codex/rag`。本项不替代 R12，也不表示 R00–R26 完成。

## 实际范围与归属

依据任务卡、R04 宿主合同 §7、code-refactor skill/reference，逐文件核对旧候选与当前树，再逐文件检查和应用已审阅 hunk；未整包 apply、未复制旧工作树。共 57 项源码/测试/文档/锁输入变化，其中删除 29 个旧专属文件。精确清单和原始 SHA 在 `PRE-R12-cleanup-executor-code-final.json` 的 scope_manifest；另外冻结 189 个实际测试/打包输入。根依赖 143 个版本条目（142 唯一包名）裁为 55 条；删除 87 唯一包名，新增 0，所有保留第三方 package stanza 与原锁完全相同，无版本升级。

清除旧 knowledge 核心、工具、菜单、来源预览、配置、Agent 强制预搜索/报告改写和三入口绑定；普通 Agent 工具权限、Hook、MCP、团队/子 Agent、压缩、流式输出、Session 与 Remote 通用契约保留。普通中文输入/粘贴、CLI 两种输出、TUI 恢复与 Remote stream/replay 正式回归保留在 tests/test_entrypoints.py。旧 metadata 的历史文字保留；旧 knowledge 字段不再参与运行，没有批量迁移用户记录。

根 knowledge extra 与不存在的旧 compose force-include 被移除；根 sdist 排除独立开发区。独立核心、用户 compose/旧 compose 继承删除、数据、模型权重与共享环境未操作。README 首 240 行与本轮 before 快照逐字节相同；被继承旧状态行与本任务共同覆盖的归属保存在 `PRE-R12-cleanup-executor-inherited-readme.json`（HEAD→before 原始 hunk + R00 对应 hash）。新状态准确区分独立 R00–R11 已验收与宿主待 R12；OCR/复杂版面仍是后续愿景。

## R01 接缝及暂存注意

当前 run.ps1 保留参数签名、Check/Answers/Replay、caller-relative 输入和 native report wrapper，仅在 Retrieval 最前准确拒绝旧入口，并更新安装提示为 uv sync --locked。check.py、test_multihop_evaluation.py、dataset_io/replay/score/prepare 与冻结题库完全未变。

Leader 追加授权仅更新 test_evaluation_protocol.py 哈希用例 seams：加入两份已经授权更新的使用文档 README.md/benchmark.md，并写明原因；原题、609 篇语料、gold、upstream、分母、数量、顺序及评分断言未放宽。新 test_pre_r12_cleanup.py 真实调用 pwsh 验证早拒绝、caller-relative Answers/Replay、失败非零、原生报告及 50/44/6 分母。

**不可直接 stage 整份当前 eval README/benchmark/run.ps1**：它们仍包含用户未提交的 MultiHop 整体替换。`PRE-R12-cleanup-executor-current-deltas.json` 保存 current→final；`PRE-R12-cleanup-executor-head-candidates.json` 保存仅从 HEAD 移除旧入口/历史标注的最小候选和 diff，未写入工作区、未作为实际运行候选测试。Leader 须独立审查并仅 stage 该语义补丁，剩余继承替换保留。候选 hash `e7786e814bdb59a10784c78c18af142e6447beab59f52ddd0f46cd38e97f1e3f`。

## 实际执行与边界

- 自有 Temp 环境：Windows / pwsh 7.6.5，uv 0.11.13，Python 3.12.13；UV_PROJECT_ENVIRONMENT 指向本任务 hostenv，UV_LINK_MODE=copy，未变更 root/.venv、共享 core 或 CUDA 环境。R01 的真实 PowerShell 脚本按原契约调用 root/.venv，只执行其已有解释器且禁写 bytecode，不安装/同步它。
- 首轮普通宿主：715 passed，3 skipped，1 warning。三项 skip 为缺 CODEPLUS_TEST_API_KEY 的真实 Memory 合并、Windows rm、无可用系统 symlink 目标；警告为既有未注册 timeout marker。联网模型不在本清理验收范围，R12 仍必须真实验证。
- 首轮契约：115 passed / 1 failed；失败由新增 Replay fixture 缺 elapsed_ms、retrieval_request.candidates/rrf_k 引起，修正为已有原生协议后 116 passed。未改生产 replay/scorer。一次自有部分 report.json 已按 replayed_from 的绝对 Temp 归属删除，记录单独保存。
- 最终冻结输入上的合并回归：809 passed、3 skipped、1 既有 warning，53.16s；详见 `PRE-R12-cleanup-executor-final-tests.txt/json`，退出 0，前后 189 个输入 hash 一致。含完整普通宿主、R01、R04 及新 cleanup seam；R04 是真实宿主变换与 SDK MockTransport 实验，不能宣称联网 Agent 通过。
- lite/medium/full 使用实际 check.py -S 通过（50/200/2556，均 609 篇、6084 gold）；prepare --check 612 适配文件逐字节一致。SWE-bench 15 题既有完整性通过，模型/官方 Docker not_executed。Remote inline JavaScript 由 node --check 通过。
- 两个全新仓库外环境真实安装 root direct wheel 与 sdist→wheel，-I -B 且无 PYTHONPATH。全部 site-packages 源文件逐字节匹配 wheel，CLI --help 可用，旧 knowledge/module/resource/extra 与独立 agentic_rag 均不在宿主包中，未导入 Torch/Transformers/PyMilvus。两路 package 源文件完全一致。

最终 direct wheel SHA `f3fefb96e0896551e9ed6ded61f345153d1c90c7ec6a974148764258de85d4ab`；重建 wheel SHA `f3fefb96e0896551e9ed6ded61f345153d1c90c7ec6a974148764258de85d4ab`；sdist SHA `9038a8fd94acc4890610c1d2558a00f84a97919647c01292bf6400e87e925b57`。正式可复跑安装 helper 为 `tests/pre_r12_cleanup_install.py`（独立开发区），每条真实 argv/cwd/exit/stdout/stderr 在安装 JSON 中。

## 失败记录与修复

1. 逐文件应用首次 raw after hash 不同，原因是 Git core.autocrlf；逐项核对 LF 归一后的候选内容完全一致，恢复原本 LF 文件格式并确认 README 前 240 行原字节不变。没有将格式漂移当成内容通过。
2. 新 Replay fixture 字段缺失，见 first-contracts；按既有契约补齐后通过，未放宽断言。
3. 首次隔离安装缺 Python3.12 对应 wheel 哈希缓存而离线失败。依赖安装改为仅允许下载 uv.lock 固定版本/哈希；未升级。second-installations 中真实安装 smoke 又发现辅助用例 AppConfig() 少 providers 必填参数，修正为 providers=[]，不改宿主 API。
4. before-doc-clarity-installations 是此前通过的候选；README 澄清 OCR 后重新构建/安装，只有最终 installations.json 对应交付 sdist，旧批次保留不冒充最终。

## 完整性与交接

freeze SHA `73fc1c80c761a16f4a57d81fbcb1aec83af76cbbe405df980f1f59a3c9a72074`；R11 63 个冻结文件完全一致；6553 个 R00 保护项没有未授权漂移；R01 immutable 全部匹配。源码/测试/文档 diff --check 退出 0（仅 core.autocrlf 提示）。旧→当前 AST 接点坐标在 `PRE-R12-cleanup-executor-host-hooks.json`，按函数符号重定位；后续 R12 只在本清理提交之后接 hook。

临时清理已完成，见 `PRE-R12-cleanup-executor-cleanup.json`：删除 31,276 个自有文件、1,385 个 reparse 条目和 7,213 个目录，根目录已不存在；未遍历 reparse、未修改共享硬链接属性。首次预检在 pytest 自建 Git 只读对象处停止，尚未删除；随后对 114 个只读文件逐项用 fsutil 验证所有硬链接都在已枚举的自有普通文件集合内，才清除这些自有只读标志。JSON 的 hardlinks 显示字段因 PowerShell 默认深度转为字符串，产生序列化 warning；实际路径/链接门在删除前已逐项执行，不能把此显示字段当作重新枚举的证据。旧清理任务的历史目录及自动审批拒绝未触碰。代码冻结后只新增正式记录；Leader 的 checklist、R11.md、dispatch、baseline、pre-review 保持其所有权。

真实网络 Agent、GPU/Milvus 新验证、Linux/双平台最终部署：not_executed，均属于后续任务，不能用本次 fixture 或安装通过替代。尚待 Leader 独立验收、精确暂存/本地提交及继续 R12–R26。
