from __future__ import annotations

from codeplus.commands.handlers.clear import CLEAR_COMMAND
from codeplus.commands.handlers.compact import COMPACT_COMMAND
from codeplus.commands.handlers.help import HELP_COMMAND
from codeplus.commands.handlers.mcp import MCP_COMMAND
from codeplus.commands.handlers.memory import MEMORY_COMMAND
from codeplus.commands.handlers.plan import PLAN_COMMAND
from codeplus.commands.handlers.sandbox import SANDBOX_COMMAND
from codeplus.commands.handlers.session import SESSION_COMMAND
from codeplus.commands.handlers.skill import SKILL_COMMAND
from codeplus.commands.handlers.rewind import REWIND_COMMAND
from codeplus.commands.handlers.status import STATUS_COMMAND
from codeplus.commands.registry import CommandRegistry


ALL_COMMANDS = [
    HELP_COMMAND,
    COMPACT_COMMAND,
    CLEAR_COMMAND,
    PLAN_COMMAND,
    SESSION_COMMAND,
    MCP_COMMAND,
    MEMORY_COMMAND,
    SANDBOX_COMMAND,
    REWIND_COMMAND,
    STATUS_COMMAND,
    SKILL_COMMAND,
]


def register_all_commands(registry: CommandRegistry) -> None:
    for cmd in ALL_COMMANDS:
        registry.register_sync(cmd)

