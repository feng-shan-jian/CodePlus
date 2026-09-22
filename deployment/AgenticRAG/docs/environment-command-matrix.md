# 环境与命令矩阵

2026-09-22，R04 范围仅为静态源码与本机接口实验。命令的 cwd 均显式给出；未来命令不是已通过记录。R04 未构建/安装新包、未请求真实模型、未启动 GPU/Milvus。

## 本次实际环境

| 用途 | 实际环境与状态 |
| --- | --- |
| R04 主机 | Windows 11 `10.0.26200`，PowerShell 7.6.5，cwd `D:/CodePlus` |
| 本次 Python | `D:/CodePlus/.venv/Scripts/python.exe`，Python 3.14.3；只做 CPU 接口实验 |
| 实际相关依赖 | pytest 9.0.3、pytest-asyncio 1.3.0、anthropic 0.98.1、openai 2.34.0、httpx 0.28.1；本次未安装或改依赖 |
| 构建依赖 | 当前根 .venv 查询 hatchling 返回 PackageNotFoundError；R04 未执行构建，不能由此宣称 wheel 已验证。未来隔离构建环境另行安装 build backend |
| R03 独立环境（只引用已交付证据） | `C:/Users/18221/.cache/codeplus-agenticrag/venv-win-cuda/Scripts/python.exe`；R03 已验证的模型/revision/输入限制以 [R03](implementation-records/R03.md) 为准，本次未执行或修改此环境 |
| Linux（本次未复测） | 恢复卡记录 WSL Ubuntu 24.04 / Python 3.12.3 / NVIDIA passthrough 存在；不等于 Linux 发行安装已通过。R25 使用 Linux 独立 venv，禁止共用 Windows .venv/node_modules |

## R04 已执行

以下测试前均设置 `$env:PYTHONDONTWRITEBYTECODE='1'`，使用 `-B` 和 `-p no:cacheprovider`；临时目录位于系统 Temp。执行结束删除尝试被自动审批拒绝，现交由Leader核对收尾；确切命令与拒绝原因见R04-evidence.json。输出文本是正式验证报告，按任务约定保留。

共同命令前缀（cwd `D:/CodePlus`，pwsh）：

```powershell
& '.venv/Scripts/python.exe' -B -m pytest deployment/AgenticRAG/tests/test_host_contract.py -q -p no:cacheprovider --basetemp=C:/Users/18221/AppData/Local/Temp/codeplus-r04-resume-tests
```

| 执行 | 退出码与真实结果 | 证据 |
| --- | --- | --- |
| 恢复草稿基线，以上命令 | 0；19 passed / 5.71s | `implementation-records/R04-tests-baseline.txt` |
| 添加三协议失败来源用例后，以上命令 | 1；2 failed / 20 passed / 4.70s | `R04-tests-failure.txt`；OpenAI 两协议丢失 is_error 导致错误资格 |
| 成功资格/绑定/usage 修复并增加 registry 测试后，以上命令 | 1；1 failed / 30 passed / 4.72s | `R04-tests-repair-attempt.txt`；测试误写 fork_registry 导入名 |
| 修正真实函数名 `clone_registry_for_fork`，增加专属 SDK 关闭断言，将 `-q` 改为 `-vv` | 0；31 passed / 4.89s | `R04-tests-final.txt` |
| 补充SDK包装预算异常的三协议hook拒绝实验，仍用 `-vv` | 0；**34 passed / 4.92s（最终）** | `R04-tests-final-presend.txt`；APIConnectionError可经可信request gate恢复，transport收到0次 |
| `git rev-parse HEAD`、`git branch --show-current`、`git diff --cached --name-only` | 0；基线 `43e9af9…` / codex/rag / 空 index | `R04-static-evidence.json` |
| PowerShell `Get-FileHash` 按旧 changes.json 逐条比较56路径、读取 patch | 0；54 before、0 after、2 diverged；分歧仅 R01 check.py/run.ps1 | `R04-static-evidence.json` |
| PowerShell 按 R00 entries 检查6553路径 | 0；仅既有四项授权变化和本次两个 spec 链接变化，0 unexpected | `R04-protection-audit.json` |

源码核对使用 `rg`、`Get-Content` 读取两个真实循环、所有工具执行/序列化/计量/权限/包接点；文档指纹与旧 patch 指纹在 static evidence。一次读源码搜索将 Windows `codeplus/agents/*.py` 直接传给 rg，报路径语法错误，改为搜索实际目录；一次查询 hatchling metadata 退出 1，随后只查询已安装测试/SDK包成功。这两项没有修改依赖或降低验收范围。

实际 R04 测试使用真宿主 serializer、pair repair、单条/累计 spill、compact 和 registry 函数；摘要模型是测试替身。SDK 测试通过 MockTransport 捕获最终 request.content、验证 cap 和 max_retries=0、关闭 scoped client 不关闭 parent，并验证hook预算拒绝被包装后可用可信gate恢复且transport零调用，网络目标为 `r04.invalid` 且 transport 全拦截。账本和 receipt 是小型接口实验。`test_current_host_compact_swallows_budget_exception_design_gap` 通过意味着确认宿主当前存在需要 R12 修复的缺口，绝不是预算穿透已经落地。

## 后续计划命令（均未在 R04 执行）

占位变量必须由对应执行者设置成真实绝对路径，保留精确环境/产物 SHA/命令/退出码，不复制占位符当验收。新包 pyproject、正式 runner 与以下未来测试尚未全部存在。

| 阶段/平台 | 计划入口 | 必须取得的证据 |
| --- | --- | --- |
| R05 Windows 核心包 | 从 `D:/CodePlus` 执行 `uv build deployment/AgenticRAG --out-dir <absolute-artifacts>`（显式SRC）；独立 venv 安装 wheel；再由 sdist 构建 wheel 单独安装 | 新包 metadata/import/schema/依赖诊断；无 codeplus/GPU 强制依赖；不能受当前根错误 compose 映射影响 |
| R05 干净导入 | 切换到不含源码的目录，移除 PYTHONPATH；`<clean-python> -I -c "import agentic_rag; print(agentic_rag.__file__)"` | 路径位于 clean site-packages，不能命中 editable/仓库；相同检查用于 sdist→wheel |
| R12 Windows 宿主接入 | 清理验收提交后，环境显式安装当前本地宿主 wheel 和开发 RAG wheel；运行届时新增的 `deployment/AgenticRAG/tests/test_codeplus_integration.py`、`test_request_delivery.py`、`test_run_budget.py` | 两个真实 Agent 入口、TUI及-p、权限/取消/终态/实际交付；另列 MockTransport 与联网模型结果；R04 34测试不替代 |
| R12/R19 普通宿主回归 | `.venv/Scripts/python.exe -B -m pytest tests/test_agent.py tests/test_context.py tests/test_context_window.py tests/test_serialization.py tests/test_conversation_pairing.py tests/test_subagent.py tests/test_commands.py -p no:cacheprovider` | 普通流式、权限、共享 client/工具与会话能力保持；按当时改动扩大必要回归 |
| R25 Windows 发行安装 | Windows 专用 clean venv 安装本地宿主+RAG wheel；从 sdist 再构建；使用现有 `codeplus --help`、`codeplus -p` 和届时实现的 `/knowledge` 形式 | 非源码cwd；中文/空格路径；资源导出、真实 worker/Milvus/回答/重启历史引用；记录配置不含密钥 |
| R25 Linux 发行安装 | Linux 文件系统独立 checkout/artifacts 与 `python3 -m venv <linux-clean-venv>`；`<linux-python> -m pip install <host-wheel> <rag-wheel>`；同样执行 sdist 与 `-I` 导入 | 不从 `/mnt/d/CodePlus/.venv` 运行 Python，不共享依赖；实际 GPU/worker、路径大小写/Unicode/换行、真实端到端 |
| R25 两类 Milvus | 按安装包导出绝对 compose 文件，`docker compose -f <exported-compose> -p <owned-project> up -d`；已有实例用显式 URI/归属范围连接 | 两种方式真实读写、隔离和重启；仅清理本任务拥有的项目/资源；R02历史探针不能当生产验收 |
| R26 最终主包 | root 构建已含 `codeplus`+`agentic_rag` 的 wheel/sdist；新环境安装主发行，旧环境卸载开发发行后升级 | import路径/单份实现/资源manifest、数据与引用身份迁移、原命令与普通任务回归、备份恢复 |

真实在线评测入口由 R10/R12/R23 创建并核对后才加入可运行命令；不在此捏造 `agentic_rag.runner` 等模块。R01 现有 `eval/RAG-eval/run.ps1 -Check/-Replay/-Answers` 的已验收离线协议继续保留，不能由旧清理的过时实现覆盖。未来模型/API请求只在其对应任务许可内执行。
