"""
Remote Control 服务器：通过 WebSocket 桥接 Agent 事件和 Web UI。

使用 websockets 库提供 HTTP（静态 HTML）+ WebSocket 服务，
让用户在浏览器中与 CodePlus Agent 交互。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import aclosing
from dataclasses import asdict
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.server import Server as WSServer, ServerConnection
from websockets.http11 import Request, Response

from codeplus.agent import (
    Agent,
    CompactNotification,
    ErrorEvent,
    HookEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    RetryEvent,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from codeplus.client import create_client, resolve_context_window
from codeplus.commands import CommandContext, CommandRegistry, CommandType
from codeplus.commands.handlers import register_all_commands
from codeplus.commands.parser import parse_command
from codeplus.config import MCPServerConfig, ProviderConfig
from codeplus.conversation import ConversationManager, Message
from codeplus.hooks import HookEngine
from codeplus.mcp import MCPManager
from codeplus.mcp.tool_wrapper import mcp_tool_name_prefix
from codeplus.memory import MemoryManager, load_instructions
from codeplus.memory.session import Session, SessionManager, make_compact_boundary
from codeplus.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from codeplus.skills.loader import SkillLoader
from codeplus.tools import ToolRegistry, create_default_registry
from codeplus.tools.impl.tool_search import ToolSearchTool
from codeplus.tools.mcp_call import McpCallTool
from codeplus.tools.load_skill import LoadSkill
from codeplus.web_content import INDEX_HTML

log = logging.getLogger(__name__)


class RemoteServer:
    knowledge_feature_available = True
    """Remote Control 核心：桥接 Agent 事件和 WebSocket 客户端。"""

    def __init__(
        self,
        providers: list[ProviderConfig],
        mcp_servers: list[MCPServerConfig] | None = None,
        hook_engine: HookEngine | None = None,
        addr: str = "0.0.0.0",
        port: int = 18888,
        config: object | None = None,
    ) -> None:
        self._config = config
        self.providers = providers
        self._mcp_server_configs = mcp_servers or []
        self.hook_engine = hook_engine
        self.addr = addr
        self.port = port

        # WebSocket 连接池（支持多客户端广播）
        self._connections: set[ServerConnection] = set()

        # Agent 相关状态
        self.agent: Agent | None = None
        self.conversation: ConversationManager | None = None
        self.registry: ToolRegistry | None = None
        self.session_id: str = ""
        self._streaming = False
        self._cancel_event: asyncio.Event | None = None
        self._command_running = False
        self._message_tasks: set[asyncio.Task] = set()
        self._ui_tasks: list[asyncio.Task] = []
        self._active_task: asyncio.Task | None = None
        self._active_connection: ServerConnection | None = None
        self.knowledge_development_config = getattr(config, 'knowledge_development_config', '')
        self.knowledge_library = None
        self.last_knowledge_outcome = None
        self.last_knowledge_run_id = None
        self._knowledge_active = False
        self._knowledge_request = None

        # 权限请求的 pending 队列：id -> Future
        self._pending_perms: dict[str, asyncio.Future[PermissionResponse]] = {}

        # 命令注册表
        self.command_registry = CommandRegistry()
        register_all_commands(self.command_registry)

        # MCP 相关
        self.mcp_manager: MCPManager | None = None
        self._mcp_instructions: str = ""

        # Skill 加载器
        self.skill_loader: SkillLoader | None = None

        # Memory / Session
        self.memory_manager: MemoryManager | None = None
        self.session_manager: SessionManager | None = None
        self.session: Session | None = None

    # ------------------------------------------------------------------
    # 启动入口
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """启动 HTTP + WebSocket 服务器。"""
        # 初始化 Agent
        self._init_agent()

        # 初始化 MCP（如果有配置）
        await self._init_mcp()

        print(f"\n  Remote UI: http://localhost:{self.port}\n")

        # websockets 的 serve 支持 process_request 回调来处理普通 HTTP
        try:
            async with websockets.serve(
                self._ws_handler,
                self.addr,
                self.port,
                process_request=self._process_http_request,
                max_size=4 * 1024 * 1024,  # 4MB 消息上限
            ):
                await asyncio.Future()
        finally:
            self._cancel_active()
            # Finish active message handlers before closing the session.
            await asyncio.gather(*self._message_tasks, return_exceptions=True)
            await self._flush_ui_messages()
            if self.session:
                self.session.close()
            if self.mcp_manager:
                await self.mcp_manager.shutdown()

    # ------------------------------------------------------------------
    # HTTP 请求处理（为 / 路径提供前端 HTML）
    # ------------------------------------------------------------------

    def _process_http_request(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        """拦截 HTTP 请求，对 / 路径返回 HTML 页面。
        返回 None 表示继续走 WebSocket 升级流程。
        """
        if request.path == "/":
            return Response(
                200,
                "OK",
                websockets.Headers({"Content-Type": "text/html; charset=utf-8"}),
                INDEX_HTML.encode("utf-8"),
            )
        if request.path != "/ws":
            return Response(404, "Not Found", websockets.Headers(), b"404 Not Found")
        # /ws 路径 → 继续 WebSocket 升级
        return None

    # ------------------------------------------------------------------
    # WebSocket 连接处理
    # ------------------------------------------------------------------

    async def _ws_handler(self, websocket: ServerConnection) -> None:
        """处理单个 WebSocket 连接的全生命周期。"""
        self._connections.add(websocket)
        try:
            # 连接建立时推送会话信息
            await self._broadcast({
                "type": "connected",
                "data": {
                    "session": self.session_id,
                    "cwd": os.getcwd(),
                },
            })

            # 推送命令列表
            await self._broadcast({
                "type": "commands",
                "data": self._build_command_list(),
            })

            # 消息循环
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")
                data = msg.get("data", {})

                if msg_type == "user_message":
                    content = data.get("content", "").strip()
                    if content:
                        # 在后台任务中处理，不阻塞 WebSocket 读循环
                        self.send_user_message(content, connection=websocket)

                elif msg_type == "permission_response":
                    self._handle_permission_response(data)

                elif msg_type == "cancel":
                    if websocket is self._active_connection:
                        self._cancel_active()

                elif msg_type == "ping":
                    # 应用层保活
                    await self._broadcast({"type": "pong", "data": None})

        except websockets.ConnectionClosed:
            pass
        finally:
            self._connections.discard(websocket)
            if websocket is self._active_connection:
                self._cancel_active()

    # ------------------------------------------------------------------
    # Agent 初始化（复刻 TUI 的 _select_provider 流程）
    # ------------------------------------------------------------------

    def _init_agent(self) -> None:
        """初始化 Agent 及相关子系统。"""
        provider = self.providers[0]
        work_dir = os.getcwd()
        home = Path.home()

        # 权限系统
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(work_dir),
            rule_engine=RuleEngine(
                user_rules_path=home / ".codeplus" / "permissions.yaml",
                project_rules_path=Path(work_dir) / ".codeplus" / "permissions.yaml",
                local_rules_path=Path(work_dir) / ".codeplus" / "permissions.local.yaml",
            ),
            mode=PermissionMode.DEFAULT,
        )

        # 加载自定义指令和记忆
        instructions = load_instructions(work_dir)
        self.memory_manager = MemoryManager(work_dir)
        self.session_manager = SessionManager(work_dir)
        self.session = self.session_manager.create()
        self.session_id = self.session.session_id

        # 创建 LLM 客户端
        client = create_client(provider)

        # 工具注册表
        self.registry = create_default_registry()
        self.registry.register(ToolSearchTool(self.registry, protocol=provider.protocol))
        self.registry.register(McpCallTool(self.registry))

        # Skill 加载
        self.skill_loader = SkillLoader(work_dir)
        self.skill_loader.load_all()
        load_skill_tool = LoadSkill()
        self.registry.register(load_skill_tool)

        # 创建 Agent
        self.agent = Agent(
            client=client,
            registry=self.registry,
            protocol=provider.protocol,
            work_dir=work_dir,
            permission_checker=checker,
            context_window=provider.get_context_window(),
            instructions_content=instructions,
            memory_manager=self.memory_manager,
            hook_engine=self.hook_engine,
        )
        self.agent.session_id = self.session_id

        # 团队工具在 remote 模式下同样可用，Lead 能在浏览器会话里组建团队把活派出去
        from codeplus.agents.loader import AgentLoader
        from codeplus.agents.task_manager import TaskManager
        from codeplus.agents.trace import TraceManager
        from codeplus.config import WorktreeConfig
        from codeplus.teams.manager import TeamManager
        from codeplus.tools.agent_tool import AgentTool
        from codeplus.tools.synthetic_output import SyntheticOutputTool
        from codeplus.tools.task_stop import TaskStopTool
        from codeplus.tools.team_create import TeamCreateTool
        from codeplus.tools.team_delete import TeamDeleteTool
        from codeplus.worktree import WorktreeManager

        cfg = self._config
        enable_fork = getattr(cfg, "enable_fork", False)
        enable_verification = getattr(cfg, "enable_verification_agent", False)
        enable_coordinator = getattr(cfg, "enable_coordinator_mode", False)
        wt_cfg = getattr(cfg, "worktree", None) or WorktreeConfig()

        wt_manager = WorktreeManager(
            repo_root=work_dir,
            symlink_directories=wt_cfg.symlink_directories,
        )
        trace_manager = TraceManager()
        self.task_manager = TaskManager()
        agent_loader = AgentLoader(work_dir, enable_verification=enable_verification)
        agent_loader.load_all()
        self.team_manager = TeamManager(
            worktree_manager=wt_manager, trace_manager=trace_manager
        )

        self.registry.register(AgentTool(
            agent_loader=agent_loader,
            task_manager=self.task_manager,
            trace_manager=trace_manager,
            parent_agent=self.agent,
            enable_fork=enable_fork,
            provider_config=provider,
            worktree_manager=wt_manager,
            team_manager=self.team_manager,
        ))
        self.registry.register(TeamCreateTool(
            team_manager=self.team_manager,
            parent_agent=self.agent,
            teammate_mode="in-process",
            is_interactive=False,
            enable_coordinator_mode=enable_coordinator,
        ))
        self.registry.register(TeamDeleteTool(
            team_manager=self.team_manager, parent_agent=self.agent
        ))
        self.registry.register(TaskStopTool(team_manager=self.team_manager))
        self.registry.register(SyntheticOutputTool())
        # Lead 给队员派活、续写都走这个工具，团队要等 TeamCreate 才存在，
        # 所以这里不绑定团队，发信时再取当前团队
        from codeplus.tools.send_message import SendMessageTool

        self.registry.register(SendMessageTool(team_manager=self.team_manager))

        # 队员干完活的回传落在 lead 信箱里，每轮排空成 system-reminder 交给 Lead
        self.agent.notification_fn = self.team_manager.drain_lead_mailbox

        # 连接 Skill 到 Agent
        load_skill_tool.set_loader(self.skill_loader)
        load_skill_tool.set_agent(self.agent)

        catalog = self.skill_loader.get_catalog()
        if catalog:
            lines = ["You can use the following Skills:", ""]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append("If the user's request matches a Skill, call LoadSkill to activate it.")
            self.agent.set_skill_catalog("\n".join(lines))

        # 初始化对话管理器
        self.conversation = ConversationManager()

        log.info("Agent initialized: session=%s, model=%s", self.session_id, provider.model)

    # ------------------------------------------------------------------
    # MCP 初始化
    # ------------------------------------------------------------------

    async def _init_mcp(self) -> None:
        """连接所有配置的 MCP 服务器，注册工具。"""
        if not self._mcp_server_configs or self.registry is None:
            return

        manager = MCPManager()
        manager.load_configs(self._mcp_server_configs)
        connect_result = await manager.register_all_tools(self.registry)
        self.mcp_manager = manager

        for err in connect_result.errors:
            log.warning("MCP error: %s", err)

        # 工具都在位了才算得准 schema 总量跟上下文窗口的比例
        if self.providers:
            from codeplus.mcp.loading_strategy import decide_and_apply

            provider = self.providers[0]
            decide_and_apply(
                self.registry,
                base_url=provider.base_url,
                context_window=provider.get_context_window(),
            )

        # 构建 MCP 指令（首次发送消息时注入 conversation）
        if connect_result.servers:
            parts = []
            for srv_info in connect_result.servers:
                section = f"## {srv_info.name}\n"
                if srv_info.instructions:
                    section += srv_info.instructions
                else:
                    prefix = mcp_tool_name_prefix(srv_info.name)
                    tool_names = [
                        t.name for t in self.registry.list_tools()
                        if t.name.startswith(prefix)
                    ]
                    if tool_names:
                        section += "Available tools: " + ", ".join(tool_names)
                parts.append(section)
            self._mcp_instructions = (
                "# MCP Server Instructions\n\n"
                "The following MCP servers have provided instructions "
                "for how to use their tools and resources:\n\n"
                + "\n\n".join(parts)
            )

    # ------------------------------------------------------------------
    # 用户消息处理
    # ------------------------------------------------------------------

    async def _handle_user_message(self, content: str, *, connection=None, _knowledge=False) -> None:
        """处理来自 Web UI 的用户消息或斜杠命令。"""
        if not _knowledge and (self._streaming or self._command_running or self._knowledge_active):
            await self._broadcast({"type": "system", "data": {
                "message": "当前回答或命令正在执行；完成后再提问、操作知识库或切换会话。"}})
            return

        self._active_task, self._active_connection = asyncio.current_task(), connection

        # 已选知识动作的正文直接交给 Agent，不再次解释成斜杠命令。
        if not _knowledge and content.startswith("/"):
            self._command_running = True
            knowledge_request = False
            try:
                await self._handle_slash_command(content)
                if self._knowledge_request is not None:
                    knowledge_request = True
                    text, policy = self._knowledge_request
                    self._knowledge_request = None
                    await self._run_knowledge(text, policy, connection)
            finally:
                if self._knowledge_request is not None:
                    knowledge_request = True
                    self._knowledge_request = None
                    self._knowledge_active = False
                self._command_running = False
                if self._active_task is asyncio.current_task():
                    self._active_task = self._active_connection = None
                if knowledge_request:
                    await self._broadcast({'type': 'command_done', 'data': None})
            return

        # 普通消息 → 发给 Agent
        self._streaming = True
        assert self.conversation is not None
        assert self.agent is not None

        self.conversation.add_user_message(content)
        if self.session:
            self.session.append(self.conversation.history[-1])

        # 首次注入 MCP 指令
        if self._mcp_instructions and not _knowledge:
            self.conversation.add_system_reminder(self._mcp_instructions)
            self._mcp_instructions = ""

        # 创建取消事件
        self._cancel_event = asyncio.Event()
        start_time = time.monotonic()
        stream_buf = ""
        history_cursor = len(self.conversation.history)

        try:
            async with aclosing(self.agent.run(self.conversation)) as stream:
                async for event in stream:
                    # 检查取消信号
                    if self._cancel_event.is_set():
                        break

                    if isinstance(event, StreamText):
                        stream_buf += event.text
                        await self._broadcast({
                            "type": "stream_text",
                            "data": {"text": event.text},
                        })

                    elif isinstance(event, ThinkingText):
                        await self._broadcast({
                            "type": "thinking_text",
                            "data": {"text": event.text},
                        })

                    elif isinstance(event, ToolUseEvent):
                        await self._broadcast({
                            "type": "tool_use",
                            "data": {
                                "toolId": event.tool_id,
                                "toolName": event.tool_name,
                                "args": event.arguments,
                            },
                        })

                    elif isinstance(event, ToolResultEvent):
                        # 如果之前有累积的流式文本，先结束它
                        if stream_buf:
                            await self._broadcast({
                                "type": "stream_end",
                                "data": {"text": stream_buf},
                            })
                            stream_buf = ""
                        await self._broadcast({
                            "type": "tool_result",
                            "data": {
                                "toolId": event.tool_id,
                                "toolName": event.tool_name,
                                "output": event.output,
                                "isError": event.is_error,
                                "elapsed": event.elapsed,
                            },
                        })

                    elif isinstance(event, PermissionRequest):
                        # 生成唯一 ID，等待 Web 端回复
                        perm_id = f"perm_{time.time_ns()}"
                        self._pending_perms[perm_id] = event.future
                        event.future.add_done_callback(lambda future, identity=perm_id: self._permission_finished(identity, future))
                        await self._broadcast({
                            "type": "permission_request",
                            "data": {
                                "id": perm_id,
                                "toolName": event.tool_name,
                                "description": event.description,
                            },
                        })

                    elif isinstance(event, TurnComplete):
                        if self.session and not _knowledge:
                            for message in self.conversation.history[history_cursor:]:
                                self.session.append(message)
                            history_cursor = len(self.conversation.history)
                        if stream_buf:
                            await self._broadcast({
                                "type": "stream_end",
                                "data": {"text": stream_buf},
                            })
                            stream_buf = ""
                        await self._broadcast({
                            "type": "turn_complete",
                            "data": {"turn": event.turn},
                        })

                    elif isinstance(event, LoopComplete):
                        if self.session and _knowledge and stream_buf:
                            self.session.append(Message(role='assistant', content=stream_buf))
                        if stream_buf:
                            await self._broadcast({
                                "type": "stream_end",
                                "data": {"text": stream_buf},
                            })
                            stream_buf = ""
                        elapsed = time.monotonic() - start_time
                        if _knowledge:
                            self._knowledge_completed_turns = event.total_turns
                            continue  # Knowledge finishes after scope cleanup below.
                        await self._broadcast({
                            "type": "loop_complete",
                            "data": {
                                "totalTurns": event.total_turns,
                                "elapsed": elapsed,
                            },
                        })

                    elif isinstance(event, UsageEvent):
                        await self._broadcast({
                            "type": "usage",
                            "data": {
                                "inputTokens": event.input_tokens,
                                "outputTokens": event.output_tokens,
                            },
                        })

                    elif isinstance(event, ErrorEvent):
                        await self._broadcast({
                            "type": "error",
                            "data": {"message": event.message},
                        })

                    elif isinstance(event, CompactNotification):
                        if not _knowledge:
                            self._persist_compact_boundary(event)
                        history_cursor = len(self.conversation.history)
                        await self._broadcast({
                            "type": "compact",
                            "data": {"message": event.message},
                        })

                    elif isinstance(event, RetryEvent):
                        await self._broadcast({
                            "type": "retry",
                            "data": {
                                "reason": event.reason,
                                "waitMs": int(event.wait * 1000),
                            },
                        })

                    elif isinstance(event, HookEvent):
                        status = "ok" if event.success else "error"
                        await self._broadcast({
                            "type": "system",
                            "data": {
                                "message": f"Hook [{event.hook_id}] {status}: {event.output}"
                            },
                        })

        except asyncio.CancelledError:
            await self._broadcast({
                "type": "error",
                "data": {"message": "Operation cancelled"},
            })
        except Exception as exc:
            log.exception("Agent run error")
            await self._broadcast({
                "type": "error",
                "data": {"message": str(exc)},
            })
        finally:
            self._deny_pending_permissions()
            if self.session and not _knowledge:
                self.session.meta.total_tokens = self.agent.total_input_tokens + self.agent.total_output_tokens
                for message in self.conversation.history[history_cursor:]:
                    self.session.append(message)
            self._streaming = False
            self._cancel_event = None
            if self._active_task is asyncio.current_task() and not _knowledge:
                self._active_task = self._active_connection = None

    # ------------------------------------------------------------------
    # 斜杠命令处理
    # ------------------------------------------------------------------

    async def _handle_slash_command(self, input_text: str) -> None:
        """分发斜杠命令。"""
        name, args, is_command = parse_command(input_text)
        if not is_command or not name:
            return

        cmd = self.command_registry.find(name)
        if cmd is None:
            await self._broadcast({
                "type": "error",
                "data": {"message": f"Unknown command: /{name} — type /help to see available commands"},
            })
            await self._broadcast({"type": "command_done", "data": None})
            return

        # 需要参数但没给
        if not args and cmd.arg_prompt:
            await self._broadcast({
                "type": "system",
                "data": {"message": cmd.arg_prompt},
            })
            await self._broadcast({"type": "command_done", "data": None})
            return

        if cmd.type == CommandType.LOCAL or name == "clear":
            # 本地命令直接执行
            ctx = self._build_command_context(args)
            try:
                await cmd.handler(ctx)
            except Exception as exc:
                await self._broadcast({
                    "type": "error",
                    "data": {"message": f"Command error: {exc}"},
                })
            await self._flush_ui_messages()
            if self._knowledge_request is None:
                await self._broadcast({"type": "command_done", "data": None})

        elif cmd.type == CommandType.LOCAL_UI:
            # UI 命令需要特殊处理
            if name == "compact":
                await self._handle_compact()
                return

            else:
                await self._broadcast({
                    "type": "system",
                    "data": {"message": f"/{name} is not fully supported in remote mode."},
                })

            await self._broadcast({"type": "command_done", "data": None})

        elif cmd.type == CommandType.PROMPT:
            # Prompt 类命令：handler 返回 prompt 文本，注入给 agent
            ctx = self._build_command_context(args)
            try:
                await cmd.handler(ctx)
            except Exception as exc:
                await self._broadcast({
                    "type": "error",
                    "data": {"message": f"Command error: {exc}"},
                })
                await self._broadcast({"type": "command_done", "data": None})

    def _build_command_context(self, args: str) -> CommandContext:
        """构建命令上下文。"""
        return CommandContext(
            args=args,
            agent=self.agent,
            conversation=self.conversation,
            session=self.session,
            session_manager=self.session_manager,
            memory_manager=self.memory_manager,
            ui=self,  # type: ignore[arg-type]
            config={
                "registry": self.command_registry,
                "set_session": self._set_session,
                "set_conversation": self._set_conversation,
                "clear_chat": self._clear_chat,
                "render_restored": self._render_restored_messages,
            },
        )

    async def _handle_compact(self) -> None:
        """处理 /compact 命令。"""
        if self.agent is None or self.conversation is None:
            await self._broadcast({
                "type": "error",
                "data": {"message": "Compact requires an active agent."},
            })
            await self._broadcast({"type": "command_done", "data": None})
            return

        await self._broadcast({
            "type": "system",
            "data": {"message": "Compacting conversation..."},
        })

        result = await self.agent.manual_compact(self.conversation)
        if isinstance(result, CompactNotification):
            self._persist_compact_boundary(result)
            await self._broadcast({
                "type": "system",
                "data": {"message": result.message},
            })
        elif isinstance(result, ErrorEvent):
            await self._broadcast({
                "type": "error",
                "data": {"message": result.message},
            })

        await self._broadcast({"type": "command_done", "data": None})

    # ------------------------------------------------------------------
    # UIController 协议实现（供命令系统回调）
    # ------------------------------------------------------------------

    def add_system_message(self, text: str) -> None:
        """同步接口 — 在事件循环中调度广播。"""
        self._ui_tasks.append(asyncio.create_task(self._broadcast({
            "type": "system",
            "data": {"message": text},
        })))

    async def _flush_ui_messages(self) -> None:
        pending, self._ui_tasks = self._ui_tasks, []
        await asyncio.gather(*pending)

    def send_user_message(self, text: str, *, connection=None) -> None:
        """同步接口 — 注入用户消息并触发 agent。"""
        task = asyncio.create_task(self._handle_user_message(text, connection=connection))
        self._message_tasks.add(task)
        task.add_done_callback(self._message_tasks.discard)

    def send_knowledge_message(self, text: str, *, mode=None, task_kind='qa', report_path=None, parent_run_id=None) -> None:
        if self._streaming or self._knowledge_active or self.agent is None:
            self.add_system_message('An operation is still active.')
            return
        try:
            from agentic_rag.adapters.codeplus.policy import load_policy
            policy = load_policy(self.knowledge_development_config, self.knowledge_library, self.providers[0],
                mode=mode, task_kind=task_kind, report_path=report_path, parent_run_id=parent_run_id)
        except ImportError:
            self.add_system_message('Install the local codeplus-agentic-rag development wheel into this host environment.')
            return
        except (ValueError, OSError):
            self.add_system_message('Knowledge configuration or library is unavailable or invalid.')
            return
        self._knowledge_active = True
        # Continue in the initiating message task, with the same WS owner.
        self._knowledge_request = (text, policy)

    async def _run_knowledge(self, text, policy, connection):
        started = time.monotonic()
        self._knowledge_completed_turns = None
        original_agent, original_conversation = self.agent, self.conversation
        controlled = Agent(client=original_agent.client, registry=original_agent.registry, protocol=original_agent.protocol,
            work_dir=original_agent.work_dir, permission_checker=original_agent.permission_checker,
            context_window=original_agent.context_window, hook_engine=original_agent.hook_engine, execution_policy=policy)
        controlled.session_id, controlled.file_history = original_agent.session_id, original_agent.file_history
        self.agent, self.conversation = controlled, ConversationManager()
        try:
            await self._handle_user_message(text, connection=connection, _knowledge=True)
        finally:
            self.last_knowledge_outcome = controlled.last_run_outcome
            self.agent, self.conversation = original_agent, original_conversation
            self._knowledge_active = False
            self._active_task = self._active_connection = None
            if self.last_knowledge_outcome:
                self.last_knowledge_run_id = self.last_knowledge_outcome.run_id
                if self.session:
                    self.session.set_rag_selection(self.knowledge_library, self.last_knowledge_run_id)
                await self._broadcast({'type': 'system', 'data': {'message':
                    json.dumps({'knowledge_run': asdict(self.last_knowledge_outcome)}, ensure_ascii=False)}})
                if self._knowledge_completed_turns is not None and self.last_knowledge_outcome.status not in {'failed', 'cancelled'}:
                    await self._broadcast({'type': 'loop_complete', 'data': {
                        'totalTurns': self._knowledge_completed_turns, 'elapsed': time.monotonic()-started}})
            await self._flush_ui_messages()

    def _deny_pending_permissions(self):
        for identity, future in tuple(self._pending_perms.items()):
            if not future.done():
                future.set_result(PermissionResponse.DENY)
                self._permission_finished(identity, future, reason='cancelled')

    def _permission_finished(self, identity, future, *, reason=None):
        if self._pending_perms.get(identity) is not future:
            return
        self._pending_perms.pop(identity, None)
        self._ui_tasks.append(asyncio.create_task(self._broadcast({'type': 'permission_resolved',
            'data': {'id': identity, 'reason': reason or ('cancelled' if future.cancelled() else 'answered')}})))

    def _cancel_active(self):
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._deny_pending_permissions()
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()


    def _set_session(self, session: Session) -> None:
        self.session = session
        self.knowledge_library = session.meta.rag_library_id
        self.last_knowledge_run_id = session.meta.rag_last_run_id
        self.last_knowledge_outcome = None
        self.session_id = session.session_id
        self.agent.session_id = self.session_id

    def _set_conversation(self, conversation: ConversationManager) -> None:
        self.conversation = conversation

    def _clear_chat(self) -> None:
        self._ui_tasks.append(asyncio.create_task(self._broadcast({"type": "clear", "data": None})))

    async def _render_restored_messages(self, messages) -> None:
        await self._broadcast({"type": "clear", "data": None})
        for message in messages:
            if message.content and not message.tool_results:
                await self._broadcast({"type": f"replay_{message.role}",
                                       "data": {"content": message.content}})


    def _persist_compact_boundary(self, notification: CompactNotification) -> None:
        if self.session and notification.boundary is not None:
            self.session.append_record(make_compact_boundary(notification.boundary.summary, notification.boundary.keep))

    def set_plan_mode(self, enabled: bool) -> None:
        if self.agent is None:
            return
        if enabled:
            self.agent.set_permission_mode(PermissionMode.PLAN)
        else:
            self.agent.set_permission_mode(PermissionMode.DEFAULT)

    def get_token_count(self) -> tuple[int, int]:
        if self.agent:
            return self.agent.total_input_tokens, self.agent.total_output_tokens
        return 0, 0

    def refresh_status(self) -> None:
        pass  # Remote 模式不需要刷新 TUI 状态栏

    # ------------------------------------------------------------------
    # 权限响应处理
    # ------------------------------------------------------------------

    def _handle_permission_response(self, data: dict[str, Any]) -> None:
        """处理来自 Web UI 的权限回复。"""
        perm_id = data.get("id", "")
        response_str = data.get("response", "deny")

        future = self._pending_perms.get(perm_id)
        if future is None or future.done():
            return

        # 映射字符串到枚举
        mapping = {
            "allow": PermissionResponse.ALLOW,
            "deny": PermissionResponse.DENY,
            "allowAlways": PermissionResponse.ALLOW_ALWAYS,
        }
        response = mapping.get(response_str, PermissionResponse.DENY)
        # future 可能已经结束（本轮被取消时会被 cancel），此时不能再回填结果
        if not future.done():
            future.set_result(response)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _build_command_list(self) -> list[dict[str, str]]:
        """构建命令列表，推送给前端用于斜杠命令菜单。"""
        result = []
        for cmd in self.command_registry.list_commands():
            result.append({
                "name": cmd.name,
                "description": cmd.description,
            })
        return result

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        """向所有已连接的 WebSocket 客户端广播消息。"""
        if not self._connections:
            return
        data = json.dumps(msg, ensure_ascii=False)
        # 复制集合避免迭代中修改
        closed = []
        for ws in list(self._connections):
            try:
                await ws.send(data)
            except websockets.ConnectionClosed:
                closed.append(ws)
            except Exception:
                closed.append(ws)
        for ws in closed:
            self._connections.discard(ws)
