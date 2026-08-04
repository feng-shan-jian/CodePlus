# CodePlus

[中文](README.md) | [English](README_EN.md)

> 探索任务之间如何直接通信、交接并长期协作，同时提供一个真正可用的终端 AI 编程工具。

CodePlus 并不是为了直接复刻 Codex。我希望通过这个项目持续探索新的 Agent 协作方式，同时吸收现有工具中已经得到验证的优秀实践，并逐步把自己的理解变成可以公开、可以使用的技术实现。

## 为什么会有 CodePlus

我看到很多人分享自己的 Agent 工作流，其中不少都会使用多个子 Agent。但真正利用“任务与任务之间直接通信”这一能力的工作流，似乎并不多见或者说还未见到。

Codex 并没有特别明显地宣传这个能力，至少我最初没有看到相关介绍。我是在一次偶然的交互中发现它的：我让一个任务为另一个任务准备提示词，原本以为它会把提示词发给我复制，结果它直接把消息发送给了目标任务。当时我甚至不知道任务之间可以这样通信。

后来我开始尝试把它用于不同场景：

- **任务交接**：任务 A 完成后，创建一个拥有全新上下文的任务，并把整理后的交接信息直接发送给它。
- **模型分工**：创建使用不同模型或推理能力的任务，只把适合其能力和成本的工作交给它们。
- **进度监控**：让一个任务定期用自然语言总结另一个任务的进展，并判断是否需要人工调整；需要调整时，可以直接把纠偏指令发送给实现任务。
- **多层编排**：由总控任务创建多个独立任务，独立任务在完成或遇到阻塞时主动汇报；每个任务内部还可以继续使用子 Agent 拆分局部工作。
- **高可见度的并行协作**：把原本隐藏在单个 Agent 内部的执行过程，变成用户可以观察、打开、介入、停止和重新连接的独立任务。

这还不是全部。任务可以归档不再需要的会话、提前创建并等待触发条件、在空闲后被再次唤醒，也可以同时接收统一的新指令。一个任务甚至可以先确认自己理解了工作，然后保持等待，直到另一个任务在条件满足时只发送一句“开始”。

CodePlus 也把这种工作方式作为长期方向：让多个任务不再是互相隔离的一次性对话，而是能够持续通信、交接、等待、恢复和协同的工作单元。

## 两种并行协作

用户可见任务和内部子 Agent 解决的是不同问题，不应该混为一谈。

```text
用户可见任务                         当前任务内部

任务 A      任务 B      任务 C       当前任务
  │           │           │             └── 子 Agent
  └──── 消息、等待、交接 ──┘                  └── 子 Agent

独立窗口、独立上下文、可重新附着       局部研究、审查和有界并行
```

用户可见任务适合长期存在、跨任务通信和人工介入。内部子 Agent 更适合在当前任务内部完成代码审查、局部研究或并行修改。CodePlus 会明确区分这两种模型。当前版本先实现任务内部的 Agent 与 Team 协作；用户可见、可长期恢复的独立任务仍属于下一阶段。

## 我在 Codex 中观察到的工具系统

下面这些名称来自我当前使用 Codex Desktop 时观察到的工具环境。它们用于说明 CodePlus 的灵感来源和能力映射，不代表 OpenAI 对这些内部工具名称或行为作出的长期兼容承诺；不同版本、账户和运行环境中可见的能力也可能不同。

### 用户可见任务与线程协调

| 工具 | 作用 |
| --- | --- |
| `codex_app__create_thread` | 创建新的用户可见 Codex 任务，可指定项目和工作目录 |
| `codex_app__send_message_to_thread` | 向已有任务发送后续指令、调整信息或汇报要求 |
| `codex_app__wait_threads` | 等待一个或多个任务完成、请求输入或进入需要关注状态；一次最多等待 8 个 |
| `codex_app__read_thread` | 读取任务状态、Turn 摘要、最终回复和工具输出 |
| `codex_app__list_threads` | 查询用户可见任务列表 |
| `codex_app__fork_thread` | 从已有任务的已完成上下文创建分支任务 |
| `codex_app__handoff_thread` | 把任务交接给另一个任务或执行环境 |
| `codex_app__get_handoff_status` | 查询异步任务交接状态 |
| `codex_app__set_thread_archived` | 归档或恢复任务 |
| `codex_app__set_thread_pinned` | 固定或取消固定任务 |
| `codex_app__set_thread_title` | 修改任务标题 |
| `codex_app__navigate_to_codex_page` | 在 Codex 应用中打开指定任务 |
| `codex_app__read_thread_terminal` | 读取当前桌面任务关联的终端输出 |
| `codex_app__list_projects` | 查询可用于创建任务的本地项目、路径和 Git 状态 |

一个典型流程是：

```text
create_thread
    -> send_message_to_thread
    -> wait_threads
    -> read_thread
```

这里有三个关键区别：

1. `create_thread` 创建的是用户能够在侧边栏看到和打开的独立任务。
2. `send_message_to_thread` 是跨任务发送后续信息，不是把提示词复制回当前对话。
3. `wait_threads` 等待的是状态事件，不需要反复读取其他任务的完整历史。

任务完成后，目标任务还可以主动通过 `send_message_to_thread` 向来源任务汇报结果或阻塞原因。

### 内部子 Agent 协调

| 工具 | 作用 |
| --- | --- |
| `collaboration.spawn_agent` | 创建当前任务内部的子 Agent |
| `collaboration.followup_task` | 向已有子 Agent 分配后续任务并唤醒它 |
| `collaboration.send_message` | 向正在运行的子 Agent 追加信息，不一定触发新一轮 |
| `collaboration.wait_agent` | 等待子 Agent 完成或产生消息 |
| `collaboration.interrupt_agent` | 中断子 Agent 当前工作 |
| `collaboration.list_agents` | 查看当前 Agent 树和运行状态 |

内部子 Agent 通常不作为平级任务出现在用户任务列表中，适合有边界、可并行的局部工作。用户可见任务则更接近独立工作线程：它们拥有自己的生命周期，可以被单独打开、等待和继续发送消息。

### 本地文件和命令

| 工具 | 作用 |
| --- | --- |
| `shell_command` | 执行 PowerShell 命令，读取文件、构建项目和运行测试 |
| `apply_patch` | 对文件进行精确补丁修改 |
| `view_image` | 查看本地图片和视觉结果 |
| `codex_app__load_workspace_dependencies` | 查询桌面环境提供的 Node、Python、文档和媒体处理依赖 |

它们组成最常见的Agent 工程闭环：

```text
读取代码 -> 修改代码 -> 构建或测试 -> 检查真实输出
```

### 规划、目标和 MCP 资源

| 工具 | 作用 |
| --- | --- |
| `update_plan` | 更新当前任务的计划和步骤状态 |
| `create_goal` | 创建一个明确的长期目标 |
| `get_goal` | 查询当前目标和执行状态 |
| `update_goal` | 把目标标记为完成或阻塞 |
| `list_mcp_resources` | 查询 MCP 服务提供的资源 |
| `read_mcp_resource` | 读取指定 MCP 资源 |
| `list_mcp_resource_templates` | 查询带参数的 MCP 资源模板 |

这些工具管理目标、计划和上下文，本身不替代真实的代码执行工具。

### 自动化和任务管理

| 工具 | 作用 |
| --- | --- |
| `codex_app__automation_update` | 创建、查看、更新或删除定时任务、提醒、监控和后续唤醒 |
| `codex_app__set_thread_archived` | 在任务完成后归档，保留历史但减少列表干扰 |
| `codex_app__set_thread_pinned` | 固定需要持续关注的重要任务 |
| `codex_app__set_thread_title` | 为长期任务提供稳定、可识别的名称 |
| `codex_app__navigate_to_codex_page` | 在桌面应用中跳转到需要处理的任务 |

## CodePlus 已经实现了什么

当前 CodePlus 已经具备可运行的终端入口、真实模型调用、本地工程工具、权限控制、内部 Agent/Team 协作、Skill、MCP、上下文与 worktree 支持。这里仅描述能在当前仓库代码和测试中对应到的能力。

### 可运行的终端入口

- 交互式 Textual TUI：在终端中持续对话、查看流式输出、处理工具调用和权限请求。
- 非交互模式：通过 `-p` 执行单次任务并输出最终文本。
- 结构化输出：非交互模式可使用 `stream-json` 输出 NDJSON 事件。
- Remote 入口：可启动 WebSocket 服务和浏览器界面，默认监听 `0.0.0.0:18888`。

```powershell
uv run codeplus
uv run codeplus -p "检查当前项目并总结风险"
uv run codeplus -p "运行测试" --output-format stream-json
uv run codeplus --remote
```

### 内部 Agent 与 Team 协作

- `Agent` 用于当前会话内部的有界子 Agent 工作，可承担研究、实现、审查和验证等局部任务。
- `TeamCreate`、`TeamDelete`、`SendMessage` 和 `TaskStop` 管理内部团队、队友通信与停止操作。
- `TaskCreate`、`TaskGet`、`TaskList` 和 `TaskUpdate` 提供团队内部共享任务板；这里的 Task 是内部工作项，不是独立的用户可见 Runtime Task。
- Team 可按环境使用进程内、tmux 或 iTerm2 后端；在不适合独立窗格的环境中使用进程内执行。
- 可选的 coordinator mode 会收窄 Lead 的工具范围，使其专注于分解、派发、跟进和整合。

### 工程工具、权限和安全边界

- 本地工具：`ReadFile`、`WriteFile`、`EditFile`、`Bash`、`Glob` 和 `Grep`。
- 交互与工作区工具：`AskUserQuestion`、`ExitPlanMode`、`EnterWorktree` 和 `ExitWorktree`。
- 支持 `default`、`acceptEdits`、`plan` 和 `bypassPermissions` 权限模式。
- 权限规则可从用户、项目和本地覆盖层加载，并结合路径边界、危险命令检测和可选 OS 沙箱。
- Git 项目可创建隔离 worktree，并提供变更检查、清理和会话集成。

### 模型、扩展和上下文

- Provider：支持 Anthropic、OpenAI 和 OpenAI 兼容协议；实际模型、工具和流式能力取决于配置的端点。
- MCP：支持 stdio 和 Streamable HTTP 服务，并可根据工具规模选择直接加载或延迟检索。
- Skill：支持本地加载、安装和运行时执行。
- 上下文：包含 Session 历史、自动 Memory、项目指令、上下文压缩、文件历史和 rewind。
- 扩展：包含生命周期 Hooks、工具搜索以及非交互 `stream-json` 输出。

## 下一步：构建持久化线程协调层

当前版本已经实现任务内部的 Agent 与 Team 协作。下一步，我准备在现有 Agent Loop 之上增加一层持久化线程协调，让多个用户可见任务拥有独立上下文，并能够相互通信、等待、派生和交接。

第一阶段会先把范围收敛在 CodePlus 项目内部，由当前 CodePlus 进程统一管理任务执行和状态变化，并持久化每个任务的消息与执行历史。下面这些工具和 CLI 仍然是目标能力，不代表当前版本已经可以使用。

### 目标工具

| 工具 | 预期行为 |
| --- | --- |
| `TaskSpawn` | 创建独立、用户可见、可持久化的任务，并绑定项目和工作目录 |
| `TaskSend` | 向已有任务发送消息，并按顺序进入任务收件箱 |
| `TaskWait` | 按事件游标等待一个或多个任务，不需要反复读取完整历史 |
| `TaskRead` | 读取任务、消息、Turn、事件和待处理交互 |
| `TaskList` | 查询当前项目中的任务与需要用户关注的状态 |
| `TaskFork` | 从来源任务已经完成的持久化历史创建新任务 |
| `TaskInterrupt` | 请求中断任务，并让持久化状态与当前运行状态保持一致 |

目标 CLI 形态如下；当前版本执行这些 `codeplus task ...` 命令会失败：

```text
codeplus task create --prompt "可选的首轮指令"
codeplus task open
codeplus task respond
codeplus task send
codeplus task wait
codeplus task read
codeplus task list
codeplus task fork
codeplus task interrupt
codeplus task resume
codeplus task delete
```

## 架构

下面展示当前仓库已经存在的五个逻辑区域；用户可见 Runtime Tasks 尚未接入这张图。

![CodePlus 当前架构：引擎、工具、交互、安全和记忆五个逻辑区域](docs/assets/codeplus-architecture-five-regions-zh.png)

从内到外，引擎层负责思考和执行，工具层扩展 Agent 可以完成的工作，交互层让用户看见并控制执行过程，安全层在工具执行前完成权限检查，记忆层保留对话、会话和上下文状态。这五个区域用于说明能力归属，不代表严格的调用栈。内部子 Agent 与 Team 队友仍然属于当前会话的协作单元，不等同于拥有独立生命周期的用户可见 Runtime Task。

## 快速开始

### 环境要求

- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)
- 至少一个可用的模型 Provider 和 API Key

### 安装依赖

```powershell
uv sync --dev
```

### 配置 Provider

Windows PowerShell：

```powershell
Copy-Item .codeplus\config.yaml.example .codeplus\config.yaml
```

Linux 或 macOS：

```bash
cp .codeplus/config.yaml.example .codeplus/config.yaml
```

编辑 `.codeplus/config.yaml`，填写自己的 Provider、模型和 API Key。该文件默认作为本地配置使用。

### 启动 CodePlus

启动交互式 TUI：

```powershell
uv run codeplus
```

## 开发与验证

主要目录：

```text
codeplus/
  agents/          子 Agent 加载、执行和跟踪
  teams/           内部 Team、邮箱与共享工作项协调
  tools/           本地、Agent、Team、Skill 和 MCP 工具
  commands/        TUI 斜杠命令与补全
  permissions/     权限模式、规则与路径边界
  sandbox/         可选 OS 沙箱
  mcp/             MCP 客户端、管理器和工具包装
  skills/          Skill 加载、安装与执行
  memory/          Session、自动记忆和上下文召回
  context/         上下文窗口管理
  hooks/           生命周期 Hooks
  filehistory/     文件历史与恢复
  worktree/        Git worktree 生命周期
tests/             单元、持久化、TUI 和集成测试
```

运行完整测试：

```powershell
uv run pytest
```

## 项目定位


它希望回答一个仍在快速变化的问题：当 Agent 不再只是一次性回答，而是能够拥有独立任务、持久上下文、直接通信和长期协作能力时，软件开发工具应该怎样更合理、更高效地组织这些 Agent，同时仍让用户看得见、管得住，并能在关键节点作出决定？

这个问题值得持续探索，也值得做成一个能够长期使用的完整工具。
