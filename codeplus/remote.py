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
from codeplus.conversation import ConversationManager
from codeplus.hooks import HookEngine
from codeplus.mcp import MCPManager
from codeplus.mcp.tool_wrapper import mcp_tool_name_prefix
from codeplus.memory import MemoryManager, load_instructions
from codeplus.memory.session import Session, SessionManager
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
        raise NotImplementedError("Implementation pending")

    # ------------------------------------------------------------------
    # HTTP 请求处理（为 / 路径提供前端 HTML）
    # ------------------------------------------------------------------

    def _process_http_request(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        raise NotImplementedError("Implementation pending")

    # ------------------------------------------------------------------
    # WebSocket 连接处理
    # ------------------------------------------------------------------

    async def _ws_handler(self, websocket: ServerConnection) -> None:
        raise NotImplementedError("Implementation pending")

    # ------------------------------------------------------------------
    # Agent 初始化（复刻 TUI 的 _select_provider 流程）
    # ------------------------------------------------------------------

    def _init_agent(self) -> None:
        raise NotImplementedError("Implementation pending")

    # ------------------------------------------------------------------
    # MCP 初始化
    # ------------------------------------------------------------------

    async def _init_mcp(self) -> None:
        raise NotImplementedError("Implementation pending")

    # ------------------------------------------------------------------
    # 用户消息处理
    # ------------------------------------------------------------------

    async def _handle_user_message(self, content: str) -> None:
        raise NotImplementedError("Implementation pending")

    # ------------------------------------------------------------------
    # 斜杠命令处理
    # ------------------------------------------------------------------

    async def _handle_slash_command(self, input_text: str) -> None:
        raise NotImplementedError("Implementation pending")

    def _build_command_context(self, args: str) -> CommandContext:
        raise NotImplementedError("Implementation pending")

    async def _handle_compact(self) -> None:
        raise NotImplementedError("Implementation pending")

    # ------------------------------------------------------------------
    # UIController 协议实现（供命令系统回调）
    # ------------------------------------------------------------------

    def add_system_message(self, text: str) -> None:
        """同步接口 — 在事件循环中调度广播。"""
        asyncio.ensure_future(self._broadcast({
            "type": "system",
            "data": {"message": text},
        }))

    def send_user_message(self, text: str) -> None:
        """同步接口 — 注入用户消息并触发 agent。"""
        asyncio.create_task(self._handle_user_message(text))

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
        raise NotImplementedError("Implementation pending")

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
        raise NotImplementedError("Implementation pending")
