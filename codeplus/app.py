from __future__ import annotations

import asyncio
import os
import random
import time as _time
from pathlib import Path
from typing import Any

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TMessage
from textual.widgets import Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

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
from codeplus.client import (
    AuthenticationError,
    LLMClient,
    LLMError,
    create_client,
    resolve_context_window,
)
from codeplus.commands import (
    CommandContext,
    CommandRegistry,
    complete,
    parse_command,
)
from codeplus import crashlog
from codeplus.commands.completion import CompletionPopup
from codeplus.commands.handlers import register_all_commands
from codeplus.config import MCPServerConfig, ProviderConfig
from codeplus.hooks import HookContext, HookEngine, load_hooks
from codeplus.conversation import ConversationManager, Message
from codeplus.mcp import ConnectResult, MCPManager
from codeplus.mcp.tool_wrapper import mcp_tool_name_prefix
from codeplus.memory import (
    MemoryManager,
    Session,
    SessionManager,
    find_relevant_memories,
    generate_session_summary,
    load_instructions,
    make_compact_boundary,
    render_reminder,
)
from codeplus.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from codeplus.agents.loader import AgentLoader
from codeplus.agents.task_manager import TaskManager
from codeplus.agents.trace import TraceManager
from codeplus.agents.notification import inject_task_notifications
from codeplus.commands.handlers.tasks import create_tasks_command
from codeplus.skills.executor import SkillExecutor
from codeplus.skills.loader import SkillLoader
from codeplus.commands.handlers.skill_register import register_skill_commands
from rich.text import Text as RichText
from textual.theme import Theme
from codeplus.tools import ToolRegistry, create_default_registry
from codeplus.tools.agent_tool import AgentTool
from codeplus.tools.ask_user import AskUserEvent, AskUserTool
from codeplus.tools.impl.tool_search import ToolSearchTool
from codeplus.tools.mcp_call import McpCallTool
from codeplus.tools.install_skill import InstallSkillTool
from codeplus.tools.load_skill import LoadSkill
from codeplus.worktree.cleanup import start_stale_cleanup_task
from codeplus.worktree.manager import WorktreeManager
from codeplus.commands.handlers.worktree import create_worktree_command
from codeplus.teammate_tree import TeammateTree

import re

MAX_TRUNCATED_LINES = 20
MAX_AT_REF_BYTES = 10240

# 启动横幅使用完整的 CodePlus 字标，避免继续沿用与产品品牌无关的猫头图案。
CODEPLUS_ASCII_LOGO = (
    " ██████╗ ██████╗ ██████╗ ███████╗██████╗ ██╗     ██╗   ██╗███████╗",
    "██╔════╝██╔═══██╗██╔══██╗██╔════╝██╔══██╗██║     ██║   ██║██╔════╝",
    "██║     ██║   ██║██║  ██║█████╗  ██████╔╝██║     ██║   ██║███████╗",
    "██║     ██║   ██║██║  ██║██╔══╝  ██╔═══╝ ██║     ██║   ██║╚════██║",
    "╚██████╗╚██████╔╝██████╔╝███████╗██║     ███████╗╚██████╔╝███████║",
    " ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝╚═╝     ╚══════╝ ╚═════╝ ╚══════╝",
)

_AT_REF_RE = re.compile(r"@([\w./_\-]+(?:\.[\w]+)*)")

_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".codeplus", "build", ".gradle"}


def scan_files_for_at(prefix: str, work_dir: str, limit: int = 10) -> list[str]:
    matches: list[str] = []
    base = os.path.join(work_dir, os.path.dirname(prefix)) if "/" in prefix else work_dir
    name_prefix = os.path.basename(prefix).lower()
    if not os.path.isdir(base):
        return matches
    try:
        for entry in sorted(os.listdir(base)):
            if entry in _SKIP_DIRS or entry.startswith("."):
                continue
            if entry.lower().startswith(name_prefix):
                rel = os.path.join(os.path.dirname(prefix), entry) if "/" in prefix else entry
                if os.path.isdir(os.path.join(base, entry)):
                    rel += "/"
                matches.append(rel)
                if len(matches) >= limit:
                    break
    except OSError:
        pass
    return matches


def expand_at_refs(text: str, work_dir: str) -> str:
    def _replace(m: re.Match) -> str:
        rel_path = m.group(1)
        full_path = os.path.join(work_dir, rel_path)
        if not os.path.isfile(full_path):
            return m.group(0)
        try:
            content = open(full_path, encoding="utf-8", errors="replace").read(MAX_AT_REF_BYTES)
            return f"[File: {rel_path}]\n```\n{content}\n```"
        except Exception:
            return m.group(0)
    return _AT_REF_RE.sub(_replace, text)


class ChatInput(TextArea):
    BINDINGS = [
        Binding("enter", "submit", "Submit", priority=True),
        Binding("shift+enter", "newline", "Newline", priority=True),
        Binding("ctrl+j", "newline", "Newline", priority=True),
        Binding("tab", "complete", "Complete", priority=True),
        Binding("escape", "dismiss_popup", "Dismiss", priority=True),
        Binding("up", "nav_up", "Navigate up", priority=True),
        Binding("down", "nav_down", "Navigate down", priority=True),
    ]

    class Submitted(TMessage):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class TabComplete(TMessage):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cursor_blink = False
        self._history: list[str] = []
        self._history_index: int = -1
        self._history_draft: str = ""
        self._history_file: Path | None = None

    def load_history(self, work_dir: str) -> None:
        self._history_file = Path(work_dir) / ".codeplus" / "history"
        if self._history_file.exists():
            try:
                lines = self._history_file.read_text(encoding="utf-8").splitlines()
                self._history = [l for l in lines if l.strip()]
            except Exception:
                pass

    def _persist_entry(self, text: str) -> None:
        if self._history_file is None:
            return
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_file, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def _popup(self) -> CompletionPopup | None:
        try:
            return self.app.query_one(CompletionPopup)
        except Exception:
            return None

    def action_submit(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            selected = popup.get_selected()
            popup.hide()
            if selected:
                self._history.append(selected)
                self._persist_entry(selected)
                self._history_index = -1
                self._history_draft = ""
                self.post_message(self.Submitted(selected))
                self.clear()
                return
        text = self.text.strip()
        if text:
            self._history.append(text)
            self._persist_entry(text)
            self._history_index = -1
            self._history_draft = ""
            self.post_message(self.Submitted(text))
            self.clear()

    def action_newline(self) -> None:
        self.insert("\n")

    def action_complete(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            selected = popup.get_selected()
            if selected:
                popup.hide()
                self.clear()
                self.insert(selected + " ")
            return
        text = self.text.strip()
        if text.startswith("/"):
            self.post_message(self.TabComplete(text))
        else:
            self.insert("\t")

    def action_dismiss_popup(self) -> None:
        popup = self._popup()
        if popup is not None:
            popup.hide()

    def action_nav_up(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            popup.move_up()
            return
        if not self._history:
            return
        if self._history_index == -1:
            self._history_draft = self.text
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        else:
            return
        self.clear()
        self.insert(self._history[self._history_index])

    def action_nav_down(self) -> None:
        popup = self._popup()
        if popup is not None and popup.is_visible:
            popup.move_down()
            return
        if self._history_index == -1:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.clear()
            self.insert(self._history[self._history_index])
        else:
            self._history_index = -1
            self.clear()
            self.insert(self._history_draft)

    class AtFileRequest(TMessage):
        def __init__(self, prefix: str) -> None:
            super().__init__()
            self.prefix = prefix

    class SlashMenuUpdate(TMessage):
        def __init__(self, prefix: str | None) -> None:
            super().__init__()
            self.prefix = prefix

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        text = self.text
        if text.startswith("/") and self._history_index < 0:
            prefix = text[1:]
            if " " not in prefix and "\n" not in prefix:
                self.post_message(self.SlashMenuUpdate(prefix))
            else:
                self.post_message(self.SlashMenuUpdate(None))
        else:
            self.post_message(self.SlashMenuUpdate(None))

        at_idx = text.rfind("@")
        if at_idx < 0:
            return
        after = text[at_idx + 1:]
        if " " in after or "\n" in after:
            return
        if after:
            self.post_message(self.AtFileRequest(after))


COLLAPSIBLE_TOOLS = {"ReadFile", "Glob", "Grep", "ToolSearch"}


def _is_subagent_tool(tool_name: str) -> bool:
    return tool_name == "Agent"


def _tool_title(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "ReadFile":
        path = os.path.basename(arguments.get("file_path", ""))
        return f"Read {path}" if path else "Read"
    if tool_name == "WriteFile":
        path = os.path.basename(arguments.get("file_path", ""))
        content = arguments.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        return f"Write {path} ({lines} lines)" if path else "Write"
    if tool_name == "EditFile":
        path = os.path.basename(arguments.get("file_path", ""))
        return f"Edit {path}" if path else "Edit"
    if tool_name == "Bash":
        cmd = arguments.get("command", "")
        short = cmd[:50] + "…" if len(cmd) > 50 else cmd
        return f"Bash: {short}" if short else "Bash"
    if tool_name == "Glob":
        return f"Glob: {arguments.get('pattern', '')}"
    if tool_name == "Grep":
        return f"Grep: {arguments.get('pattern', '')}"
    return tool_name


def _format_detail(tool_name: str, arguments: dict[str, Any], output: str) -> str:
    parts: list[str] = []

    if tool_name == "Bash":
        parts.append(f"  IN   {arguments.get('command', '')}")
        parts.append("")
        for line in output.splitlines():
            parts.append(f"  OUT  {line}")
    elif tool_name == "EditFile":
        # EditFile 的 output 是 build_diff() 生成的带行号 diff 文本：
        # "+ " 开头绿色、"- " 开头红色，其余（上下文行/摘要行）走 dim。
        # 转义 Rich markup 特殊字符，避免代码里的方括号被当成标签解析。
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            escaped = escape(line)
            if line.startswith("+ "):
                parts.append(f"  [green]{escaped}[/]")
            elif line.startswith("- "):
                parts.append(f"  [red]{escaped}[/]")
            else:
                parts.append(f"  [dim]{escaped}[/]")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  [dim]… ({total - MAX_TRUNCATED_LINES} more lines)[/]")
    elif tool_name in ("ReadFile", "WriteFile"):
        parts.append(f"  {arguments.get('file_path', '')}")
        parts.append("")
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f"  {line}")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  … ({total - MAX_TRUNCATED_LINES} more lines)")
    else:
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f"  {line}")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  … ({total - MAX_TRUNCATED_LINES} more lines)")

    return "\n".join(parts)


class ToolCallBlock(Static, can_focus=True):

    def __init__(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self._arguments = arguments
        self._title = _tool_title(tool_name, arguments)
        self._full_output = ""
        self._is_error = False
        self._elapsed = 0.0
        self._collapsed = True
        self._loading = True
        self._render_loading()

    def _render_loading(self) -> None:
        self.update(f"  ● {self._title} …")
        self.add_class("tool-block-loading")

    def set_result(self, output: str, is_error: bool, elapsed: float) -> None:
        self._full_output = output
        self._is_error = is_error
        self._elapsed = elapsed
        self._loading = False
        self.remove_class("tool-block-loading")
        if is_error:
            self.add_class("tool-block-error")
        # EditFile 的 diff 是最高频需要的信息，默认直接展开，不用等用户点
        # 或按 ctrl+o；其余工具仍然默认折叠，避免刷屏。
        if self.tool_name == "EditFile" and not is_error:
            self._collapsed = False
            self._render_expanded()
        else:
            self._collapsed = True
            self._render_collapsed()

    def _render_collapsed(self) -> None:
        if self._is_error:
            self.update(f"  ✗ {self._title} ({self._elapsed:.1f}s)")
        else:
            self.update(f"  ✓ {self._title} ({self._elapsed:.1f}s)")

    def _render_expanded(self) -> None:
        if self._is_error:
            header = f"  ✗ {self._title} ({self._elapsed:.1f}s)"
        else:
            header = f"  ✓ {self._title} ({self._elapsed:.1f}s)"
        detail = _format_detail(self.tool_name, self._arguments, self._full_output)
        self.update(f"{header}\n{detail}")

    def on_click(self) -> None:
        if self._loading:
            return
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._render_collapsed()
        else:
            self._render_expanded()


_MODE_CYCLE = [
    PermissionMode.DEFAULT,
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.PLAN,
    PermissionMode.BYPASS,
]

_MODE_COLORS = {
    PermissionMode.DEFAULT: "dim",
    PermissionMode.ACCEPT_EDITS: "green",
    PermissionMode.PLAN: "yellow",
    PermissionMode.BYPASS: "red",
}

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _to_past_tense(verb: str) -> str:
    """把现在进行时动词转换为过去式。"""
    if verb.endswith("ing"):
        stem = verb[:-3]
        if stem.endswith("e"):
            return stem + "d"
        if stem and stem[-1] in "atutitet":
            return stem + "ed"
        return stem + "ed"
    return verb + "ed"


THINKING_VERBS = [
    "Accomplishing", "Architecting", "Baking", "Beboppin'", "Befuddling",
    "Bloviating", "Boogieing", "Boondoggling", "Bootstrapping", "Brewing",
    "Calculating", "Canoodling", "Caramelizing", "Cascading", "Cerebrating",
    "Choreographing", "Churning", "Coalescing", "Cogitating", "Combobulating",
    "Composing", "Computing", "Concocting", "Considering", "Contemplating",
    "Cooking", "Crafting", "Creating", "Crunching", "Crystallizing",
    "Cultivating", "Deciphering", "Deliberating", "Dilly-dallying",
    "Discombobulating", "Doodling", "Elucidating", "Enchanting", "Envisioning",
    "Fermenting", "Finagling", "Flambéing", "Flibbertigibbeting", "Flummoxing",
    "Forging", "Frolicking", "Gallivanting", "Garnishing", "Generating",
    "Germinating", "Grooving", "Harmonizing", "Hatching", "Honking",
    "Hullaballooing", "Ideating", "Imagining", "Improvising", "Incubating",
    "Inferring", "Infusing", "Kneading", "Lollygagging", "Manifesting",
    "Marinating", "Meandering", "Metamorphosing", "Mapping", "Moonwalking",
    "Moseying", "Mulling", "Musing", "Noodling", "Orbiting",
    "Orchestrating", "Percolating", "Philosophising", "Pondering",
    "Pontificating", "Pouncing", "Purring", "Puzzling", "Razzle-dazzling",
    "Ruminating", "Scampering", "Simmering", "Sketching", "Spelunking",
    "Spinning", "Sprouting", "Synthesizing", "Thinking", "Tinkering",
    "Transfiguring", "Transmuting", "Undulating", "Unfurling", "Unravelling",
    "Vibing", "Wandering", "Whisking", "Working", "Wrangling", "Zigzagging",
]  # 共 105 个 TUI 快捷键动词


class ToolGroupSummary(Static, can_focus=True):


    def __init__(self, count: int, total_elapsed: float, **kwargs: Any) -> None:
        label = f"● Done ({count} tool uses · {total_elapsed:.1f}s)  (ctrl+o to expand)"
        super().__init__(label, **kwargs)
        self._count = count
        self._total = total_elapsed
        self._expanded = False

    def _refresh_display(self) -> None:
        if self._expanded:
            self.update(f"▼ Done ({self._count} tool uses · {self._total:.1f}s)")
        else:
            self.update(
                f"● Done ({self._count} tool uses · {self._total:.1f}s)"
                "  (ctrl+o to expand)"
            )

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._refresh_display()


    def on_click(self) -> None:
        self.toggle()


class SubAgentBlock(Static, can_focus=True):

    def __init__(self, agent_type: str, description: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._agent_type = agent_type or "agent"
        self._description = description[:60] if description else ""
        self._done = False
        self._is_error = False
        self._elapsed = 0.0
        self._collapsed = True
        self._result_preview = ""
        self._tool_count = 0
        self._render_running()

    def _render_running(self) -> None:
        desc = f"({self._description})" if self._description else ""
        self.update(f"● {self._agent_type}{desc}\n     Running…")

    def set_result(self, output: str, is_error: bool, elapsed: float) -> None:
        self._done = True
        self._is_error = is_error
        self._elapsed = elapsed
        self._result_preview = output[:300] if output else ""
        self._parse_stats(output)
        self._render_done()

    def _parse_stats(self, output: str) -> None:
        import re
        m = re.search(r"(\d+)\s+tool", output[:200])
        if m:
            self._tool_count = int(m.group(1))

    def _render_done(self) -> None:
        desc = f"({self._description})" if self._description else ""
        tool_info = f"{self._tool_count} tool uses · " if self._tool_count else ""
        if self._collapsed:
            self.update(
                f"● {self._agent_type}{desc}\n"
                f"    ⎿  Done ({tool_info}{self._elapsed:.1f}s)  (ctrl+o to expand)"
            )
        else:
            self.update(
                f"● {self._agent_type}{desc}\n"
                f"    ⎿  Done ({tool_info}{self._elapsed:.1f}s)\n"
                f"  {self._result_preview}"
            )

    def on_click(self) -> None:
        if not self._done:
            return
        self._collapsed = not self._collapsed
        self._render_done()


_CODEPLUS_THEME = Theme(
    name="codeplus",
    primary="#875FFF",
    background="#1a1a1a",
    surface="#1a1a1a",
    panel="#1a1a1a",
    dark=True,
)


class CodePlusApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "CodePlus"
    INLINE_PADDING = 0
    theme = "codeplus"
    BINDINGS = [
        Binding("ctrl+c", "handle_ctrl_c", "Quit", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("shift+tab", "cycle_mode", "Cycle mode", priority=True),
        Binding("ctrl+o", "toggle_tool_blocks", "Toggle tools", priority=True),
    ]


    def __init__(
        self,
        providers: list[ProviderConfig],
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        mcp_servers: list[MCPServerConfig] | None = None,
        hook_engine: HookEngine | None = None,
        enable_fork: bool = True,
        enable_verification_agent: bool = False,
        worktree_config: Any = None,
        teammate_mode: str = "",
        enable_coordinator_mode: bool = False,
        driver_class: type | None = None,
        sandbox_config: Any = None,
    ) -> None:
        super().__init__(driver_class=driver_class)
        self.providers = providers
        self._initial_permission_mode = permission_mode
        self._mcp_server_configs = mcp_servers or []
        self.hook_engine = hook_engine
        self._enable_fork = enable_fork
        self._enable_verification_agent = enable_verification_agent
        self._worktree_config = worktree_config
        self._teammate_mode = teammate_mode
        self._enable_coordinator_mode = enable_coordinator_mode
        from codeplus.config import SandboxAppConfig
        self._sandbox_cfg: SandboxAppConfig = sandbox_config or SandboxAppConfig()
        self.client: LLMClient | None = None
        self.conversation = ConversationManager()
        self.registry: ToolRegistry = create_default_registry()
        self.agent: Agent | None = None
        self.mcp_manager: MCPManager | None = None
        self._mcp_init_task: asyncio.Task[None] | None = None
        self._selected_provider: ProviderConfig | None = None
        self._streaming = False
        self._thinking_start: float = 0.0
        self._thinking_verb: str = ""
        self._spinner_idx: int = 0
        self._spinner_timer = None
        self._spinner_label: Static | None = None
        self._mcp_server_info: str = ""
        self._agent_task: asyncio.Task[None] | None = None
        self._subagent_task: asyncio.Task[None] | None = None
        self._subagent_start_time: float | None = None
        self.session_manager: SessionManager | None = None
        self.session: Session | None = None
        self.memory_manager: MemoryManager | None = None
        self._instructions_content: str = ""
        self.command_registry = CommandRegistry()
        register_all_commands(self.command_registry)
        self.skill_loader: SkillLoader | None = None
        self.skill_executor: SkillExecutor | None = None
        self._load_skill_tool: LoadSkill | None = None
        self.agent_loader: AgentLoader | None = None
        self.task_manager: TaskManager = TaskManager()
        self.trace_manager: TraceManager = TraceManager()
        self._notification_check_task: asyncio.Task[None] | None = None
        self.worktree_manager: WorktreeManager | None = None
        self._stale_cleanup_task: asyncio.Task[None] | None = None
        self._current_streaming_label: Static | None = None
        self._current_ai_row: Vertical | None = None
        self._current_accumulated_text: str = ""
        self._mcp_instructions: str = ""
        self._mcp_instructions_ok: bool = False
        self._mcp_connecting: bool = False
        self._teammate_tree: TeammateTree | None = None
        self._teammate_timer = None
        # 记录本次会话是否曾退出过 Plan Mode，用于重入时注入提示
        self._has_exited_plan_mode: bool = False

    @staticmethod
    def _make_banner(model: str = "", work_dir: str = "") -> RichText:
        """生成包含 CodePlus 字标和当前运行信息的启动横幅。

        Args:
            model (str): 当前选中的模型名称；为空时不展示模型信息。
            work_dir (str): 当前项目工作目录；为空时不展示目录信息。

        Returns:
            RichText: 可直接交给 Textual ``Static`` 渲染的富文本横幅。
        """
        t = RichText()
        for logo_line in CODEPLUS_ASCII_LOGO:
            t.append(f"{logo_line}\n", style="bold color(99)")

        # 将运行信息集中到字标下方，避免模型名或工作目录破坏 Logo 的固定宽度。
        metadata_parts = ["CodePlus v0.1.0"]
        if model:
            metadata_parts.append(model)
        if work_dir:
            metadata_parts.append(work_dir)
        t.append("  " + "  ·  ".join(metadata_parts), style="color(242)")
        return t

    def compose(self) -> ComposeResult:
        yield Static(self._make_banner(), id="title-bar")

        if len(self.providers) > 1:
            with Vertical(id="provider-select"):
                yield Static("Select a Provider", id="select-label")
                yield OptionList(
                    *[
                        Option(f"{p.name}  [{p.model}]", id=p.name)
                        for p in self.providers
                    ],
                    id="provider-list",
                )
        yield VerticalScroll(id="chat-area")
        with Vertical(id="input-area"):
            yield ChatInput(id="chat-input")
            with Horizontal(id="status-bar"):
                yield Static("  default", id="mode-label")
                yield Static("", id="teammates-label")
                yield Static("", id="model-label")
            yield CompletionPopup()

    def _handle_exception(self, error: Exception) -> None:
        """接管 Textual 的未处理异常入口，先把现场落盘再交回框架。

        框架拿到未处理异常后会把 traceback 画到终端然后结束应用，终端一关
        就什么都不剩了。事件处理器、后台 worker、刷新回调抛出的异常都汇聚
        到这里，写进崩溃日志才能在事后定位。
        """
        crashlog.record_exception("textual", error)
        super()._handle_exception(error)

    def on_mount(self) -> None:
        self.register_theme(_CODEPLUS_THEME)
        self.theme = "codeplus"
        if len(self.providers) == 1:
            self._select_provider(self.providers[0])
        else:
            self.query_one("#chat-area").display = False
            self.query_one("#input-area").display = False

    def _select_provider(self, provider: ProviderConfig) -> None:
        self._selected_provider = provider
        try:
            self.client = create_client(provider)
        except AuthenticationError as e:
            self._show_error(str(e))
            return

        work_dir = os.getcwd()
        home = Path.home()

        # 根据配置决定是否启用 OS 级沙箱自动放行
        sandbox_auto_allow = (
            self._sandbox_cfg.enabled and self._sandbox_cfg.auto_allow
        )
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(work_dir),
            rule_engine=RuleEngine(
                user_rules_path=home / ".codeplus" / "permissions.yaml",
                project_rules_path=Path(work_dir) / ".codeplus" / "permissions.yaml",
                local_rules_path=Path(work_dir) / ".codeplus" / "permissions.local.yaml",
            ),
            mode=self._initial_permission_mode,
            sandbox_enabled=sandbox_auto_allow,
        )

        # 如果配置启用了沙箱，为 Bash 工具挂载 OS 沙箱
        if self._sandbox_cfg.enabled:
            from codeplus.sandbox import SandboxConfig, create_sandbox
            os_sandbox = create_sandbox()
            if os_sandbox and os_sandbox.available():
                sandbox_config = SandboxConfig(
                    allow_write=[work_dir, "/tmp"],
                    deny_write=[
                        f"{work_dir}/.codeplus/config.yaml",
                        f"{work_dir}/.codeplus/permissions.local.yaml",
                    ],
                    network_enabled=self._sandbox_cfg.network_enabled,
                )
                bash_tool = self.registry.get("Bash")
                if bash_tool:
                    bash_tool.sandbox = os_sandbox
                    bash_tool.sandbox_config = sandbox_config

        self._instructions_content = load_instructions(work_dir)
        self.memory_manager = MemoryManager(work_dir)
        self.session_manager = SessionManager(work_dir)
        self.session_manager.cleanup()
        self.session = self.session_manager.create()

        from codeplus.filehistory import FileHistory
        self.file_history = FileHistory(work_dir, self.session.session_id)
        for tool in self.registry.list_tools():
            if hasattr(tool, "file_history"):
                tool.file_history = self.file_history

        load_skill_tool = LoadSkill()
        self.registry.register(load_skill_tool)
        self._load_skill_tool = load_skill_tool

        install_skill_tool = InstallSkillTool()
        self.registry.register(install_skill_tool)
        self._install_skill_tool = install_skill_tool

        self.registry.register(
            ToolSearchTool(self.registry, protocol=provider.protocol)
        )
        # mcp_call 必须在 MCP 连接之前就注册好。等连上再按加载模式决定注不注册，
        # 本身就是一次中途改动 tools[]，缓存前缀照样断。
        self.registry.register(McpCallTool(self.registry))
        self.registry.register(AskUserTool())

        from codeplus.tools.exit_plan_mode import ExitPlanModeTool
        self._exit_plan_tool = ExitPlanModeTool()
        self.registry.register(self._exit_plan_tool)

        self.agent = Agent(
            client=self.client,
            registry=self.registry,
            protocol=provider.protocol,
            work_dir=work_dir,
            permission_checker=checker,
            context_window=provider.get_context_window(),
            instructions_content=self._instructions_content,
            memory_manager=self.memory_manager,
            hook_engine=self.hook_engine,
        )
        self.agent.file_history = self.file_history
        self.agent.session_id = self.session.session_id

        self._exit_plan_tool._is_plan_mode = lambda: self.agent.plan_mode
        self._exit_plan_tool._plan_exists = lambda: self.agent._get_plan_path().exists()

        # Layer 2: 在后台异步拉取模型的 context window，不阻塞启动流程。
        # agent 已经有一个同步解析的窗口值（来自配置 / 映射表 / 默认值）；
        # 如果异步拉取成功，就原地升级为更准确的值。
        self.run_worker(
            self._resolve_context_window(provider), exclusive=False
        )

        self.skill_loader = SkillLoader(work_dir)
        self.skill_loader.load_all()

        load_skill_tool.set_loader(self.skill_loader)
        load_skill_tool.set_agent(self.agent)

        install_skill_tool.set_loader(self.skill_loader)

        self.skill_executor = SkillExecutor(
            agent=self.agent,
            client=self.client,
            protocol=provider.protocol,
        )
        load_skill_tool.set_executor(self.skill_executor)

        catalog = self.skill_loader.get_catalog()
        if catalog:
            lines = [
                "You can use the following Skills:",
                "",
            ]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append(
                "If the user's request matches a Skill, call LoadSkill to activate it."
            )
            self.agent.set_skill_catalog("\n".join(lines))

        register_skill_commands(
            self.command_registry, self.skill_loader, self.skill_executor
        )

        # 安装新 skill 后重新注册斜杠命令，让 /<new-skill> 立即可用
        def _on_skill_installed(name: str) -> None:
            register_skill_commands(
                self.command_registry, self.skill_loader, self.skill_executor
            )

        install_skill_tool.set_on_installed(_on_skill_installed)

        # --- Worktree 系统初始化 ---
        from codeplus.config import WorktreeConfig
        wt_cfg = self._worktree_config or WorktreeConfig()
        self.worktree_manager = WorktreeManager(
            repo_root=work_dir,
            symlink_directories=wt_cfg.symlink_directories,
        )
        restored = self.worktree_manager.restore_session()
        if restored:
            self.agent.work_dir = restored.worktree_path

        wt_command = create_worktree_command(self.worktree_manager)
        self.command_registry.register_sync(wt_command)

        from codeplus.tools.enter_worktree import EnterWorktreeTool
        from codeplus.tools.exit_worktree import ExitWorktreeTool
        self.registry.register(EnterWorktreeTool(worktree_manager=self.worktree_manager))
        self.registry.register(ExitWorktreeTool(worktree_manager=self.worktree_manager))

        self._stale_cleanup_task = asyncio.create_task(
            start_stale_cleanup_task(
                self.worktree_manager,
                wt_cfg.stale_cleanup_interval,
                wt_cfg.stale_cutoff_hours,
            )
        )

        # --- 子 agent 系统初始化 ---
        self.agent_loader = AgentLoader(
            work_dir, enable_verification=self._enable_verification_agent
        )
        self.agent_loader.load_all()

        # --- Agent 团队系统初始化 ---
        from codeplus.teams.manager import TeamManager
        from codeplus.tools.team_create import TeamCreateTool
        from codeplus.tools.team_delete import TeamDeleteTool

        self.team_manager = TeamManager(worktree_manager=self.worktree_manager, trace_manager=self.trace_manager)

        agent_tool = AgentTool(
            agent_loader=self.agent_loader,
            task_manager=self.task_manager,
            trace_manager=self.trace_manager,
            parent_agent=self.agent,
            enable_fork=self._enable_fork,
            provider_config=provider,
            worktree_manager=self.worktree_manager,
            team_manager=self.team_manager,
        )
        self.registry.register(agent_tool)

        team_create_tool = TeamCreateTool(
            team_manager=self.team_manager,
            parent_agent=self.agent,
            teammate_mode=self._teammate_mode,
            is_interactive=True,
            enable_coordinator_mode=self._enable_coordinator_mode,
        )
        self.registry.register(team_create_tool)

        team_delete_tool = TeamDeleteTool(
            team_manager=self.team_manager,
            parent_agent=self.agent,
        )
        self.registry.register(team_delete_tool)

        agent_catalog = self.agent_loader.list_agents()
        if agent_catalog:
            lines = [
                "## Available Sub-Agent Types",
                "",
                "Use the Agent tool with subagent_type parameter to delegate tasks:",
                "",
            ]
            for agent_type, when_to_use in agent_catalog:
                lines.append(f"- **{agent_type}**: {when_to_use}")
            if self._enable_fork:
                lines.append("")
                lines.append(
                    "Leave subagent_type empty to fork the current conversation "
                    "(inherits full dialog history)."
                )
            lines.append("")
            lines.append(
                "IMPORTANT: Sub-agents run in the background. "
                "After calling the Agent tool, you will get a task ID immediately. "
                "Do NOT wait, sleep, or poll for the result. "
                "Simply report the task ID to the user and end your turn. "
                "The system will automatically notify when the task completes."
            )
            self.agent.set_agent_catalog("\n".join(lines), catalog_list=agent_catalog)

        tasks_cmd = create_tasks_command(self.task_manager)
        self.command_registry.register_sync(tasks_cmd)

        from codeplus.commands.handlers.trace import create_trace_command
        trace_cmd = create_trace_command(self.trace_manager, self.agent.agent_id)
        self.command_registry.register_sync(trace_cmd)

        # --- 协调者模式初始化（工具已注册，激活推迟到 TeamCreate 时） ---
        from codeplus.tools.send_message import SendMessageTool
        from codeplus.tools.synthetic_output import SyntheticOutputTool
        from codeplus.tools.task_stop import TaskStopTool

        self.registry.register(SyntheticOutputTool())
        self.registry.register(TaskStopTool(team_manager=self.team_manager))
        # Lead 给队员派活、续写都走这个工具，团队要等 TeamCreate 才存在，
        # 所以这里不绑定团队，发信时再取当前团队
        self.registry.register(SendMessageTool(team_manager=self.team_manager))

        # coordinator 模式由配置决定，开了就从第一轮起收窄工具集
        if self._enable_coordinator_mode:
            from codeplus.agents.tool_filter import apply_coordinator_filter

            self.agent.enable_coordinator_mode = True
            self.agent.registry = apply_coordinator_filter(self.agent.registry)
        self.agent._team_manager = self.team_manager

        if self.hook_engine:
            asyncio.ensure_future(
                self.hook_engine.run_hooks(
                    "startup", HookContext(event_name="startup")
                )
            )

        if self._mcp_server_configs:
            self._mcp_init_task = asyncio.create_task(self._init_mcp())

        self.query_one("#model-label", Static).update(provider.model)
        work_dir = os.getcwd()
        self.query_one("#title-bar", Static).update(
            self._make_banner(provider.model, work_dir)
        )
        self._update_mode_label()

        select = self.query("#provider-select")
        if select:
            select.first().display = False
        self.query_one("#chat-area").display = True
        self.query_one("#input-area").display = True
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.placeholder = "Send a message..."
        chat_input.load_history(work_dir)
        chat_input.focus()

        self._notification_check_task = asyncio.create_task(
            self._start_notification_polling()
        )

    async def _resolve_context_window(self, provider: ProviderConfig) -> None:
        """Layer 2 后台 worker：异步拉取模型的 context window，
        拉到就原地升级 agent 的窗口值。

        尽力而为 — resolve_context_window 不会抛异常；如果拉不到，
        agent 继续使用同步解析得到的窗口值。
        """
        await resolve_context_window(provider)
        if self.agent is not None:
            self.agent.context_window = provider.get_context_window()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "provider-list":
            provider = self.providers[event.option_index]
            self._select_provider(provider)

    # -----------------------------------------------------------------
    # UIController 协议实现
    # -----------------------------------------------------------------

    def add_system_message(self, text: str) -> None:
        self._show_system_message(text)

    def send_user_message(self, text: str) -> None:
        if self._streaming or self.agent is None:
            return
        self._agent_task = asyncio.create_task(self._send_message(text))

    def set_plan_mode(self, enabled: bool) -> None:
        if self.agent is None:
            return
        if enabled:
            self._pre_plan_mode = self.agent.permission_mode
            self.agent.set_permission_mode(PermissionMode.PLAN)
        else:
            restore = getattr(self, "_pre_plan_mode", PermissionMode.DEFAULT)
            self.agent.set_permission_mode(restore)
        self._update_mode_label()

    def get_token_count(self) -> tuple[int, int]:
        if self.agent:
            return self.agent.total_input_tokens, self.agent.total_output_tokens
        return 0, 0

    def refresh_status(self) -> None:
        self._update_mode_label()

    # -----------------------------------------------------------------
    # 命令分发
    # -----------------------------------------------------------------


    def _build_command_context(self, args: str) -> CommandContext:
        return CommandContext(
            args=args,
            agent=self.agent,
            conversation=self.conversation,
            session=self.session,
            session_manager=self.session_manager,
            memory_manager=self.memory_manager,
            ui=self,
            config={
                "registry": self.command_registry,
                "set_session": self._set_session,
                "set_conversation": self._set_conversation,
                "clear_chat": self._clear_chat,
                "render_restored": self._render_restored_messages,
                "skill_loader": self.skill_loader,
                "skill_executor": self.skill_executor,
            },
        )

    def _set_session(self, session: Session) -> None:
        self.session = session
        if self.agent:
            self.agent.session_id = session.session_id

    def _persist_compact_boundary(self, notification: CompactNotification) -> None:
        """Layer-2 compact 后写入 compact_boundary 记录。

        将摘要 + 原样保留的尾部内联到一条记录中，resume 时只需这一条
        就能重建压缩后的状态。之前已写入磁盘的原始前缀不会被重放。
        没有活跃 session 或 compact 未产出 boundary 时直接跳过。
        """
        if not self.session or notification.boundary is None:
            return
        record = make_compact_boundary(
            notification.boundary.summary,
            notification.boundary.keep,
        )
        self.session.append_record(record)

    def _set_conversation(self, conv: ConversationManager) -> None:
        self.conversation = conv

    def _clear_chat(self) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        chat.remove_children()

    async def _dispatch_command(self, text: str) -> None:
        name, args, is_command = parse_command(text)

        if not is_command:
            if self._streaming or self.agent is None:
                return
            self._agent_task = asyncio.create_task(self._send_message(text))
            return

        if name == "":
            commands = self.command_registry.list_commands()
            lines = ["可用命令："]
            for cmd in commands:
                aliases_str = ", ".join(f"/{a}" for a in cmd.aliases)
                name_part = f"/{cmd.name}"
                if aliases_str:
                    name_part += f", {aliases_str}"
                lines.append(f"  {name_part:<24} {cmd.description}")
            self._show_system_message("\n".join(lines))
            return

        cmd = self.command_registry.find(name)
        if cmd is None:
            self._show_system_message(f"未知命令：/{name}，输入 /help 查看可用命令")
            return

        if not args and cmd.arg_prompt:
            self._show_system_message(cmd.arg_prompt)
            return

        ctx = self._build_command_context(args)
        try:
            await cmd.handler(ctx)
        except Exception as e:
            self._show_error(f"命令执行失败: {e}")

    # -----------------------------------------------------------------
    # 输入处理
    # -----------------------------------------------------------------

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        text = event.text.strip()
        if self._streaming and not text.startswith("/"):
            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
                try:
                    await self._agent_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._finish_streaming()
            self._show_system_message("(response interrupted)")
        await self._dispatch_command(text)

    def on_chat_input_tab_complete(self, event: ChatInput.TabComplete) -> None:
        matches = complete(self.command_registry, event.text)
        if not matches:
            return
        popup = self.query_one(CompletionPopup)
        if len(matches) == 1:
            input_widget = self.query_one("#chat-input", ChatInput)
            input_widget.clear()
            input_widget.insert(matches[0][1] + " ")
        else:
            popup.show_pairs(matches)

    def on_chat_input_slash_menu_update(self, event: ChatInput.SlashMenuUpdate) -> None:
        popup = self.query_one(CompletionPopup)
        if event.prefix is None:
            popup.hide()
            return
        matches = complete(self.command_registry, event.prefix)
        if not matches:
            popup.hide()
            return
        popup.show_pairs(matches)

    def on_chat_input_at_file_request(self, event: ChatInput.AtFileRequest) -> None:
        work_dir = self.agent.work_dir if self.agent else os.getcwd()
        matches = scan_files_for_at(event.prefix, work_dir)
        if matches:
            popup = self.query_one(CompletionPopup)
            popup.show([f"@{m}" for m in matches])

    def on_completion_popup_selected(self, event: CompletionPopup.Selected) -> None:
        input_widget = self.query_one("#chat-input", ChatInput)
        selected = event.value
        text = input_widget.text
        if selected.startswith("@"):
            at_idx = text.rfind("@")
            if at_idx >= 0:
                input_widget.clear()
                input_widget.insert(text[:at_idx] + selected + " ")
                input_widget.focus()
                return
        input_widget.clear()
        input_widget.insert(selected + " ")
        input_widget.focus()

    def action_cycle_mode(self) -> None:
        if self.agent is None:
            return
        current = self.agent.permission_mode
        try:
            idx = _MODE_CYCLE.index(current)
        except ValueError:
            idx = 0
        next_mode = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
        self.agent.set_permission_mode(next_mode)
        self._update_mode_label()

    def action_toggle_tool_blocks(self) -> None:
        for block in self.query(ToolCallBlock):
            if block._loading:
                continue
            block._collapsed = not block._collapsed
            if block._collapsed:
                block._render_collapsed()
            else:
                block._render_expanded()

        for summary in self.query(ToolGroupSummary):
            was_expanded = summary._expanded
            summary.toggle()
            parent = summary.parent
            if parent:
                for child in parent.children:
                    if isinstance(child, ToolCallBlock) and child.tool_name in COLLAPSIBLE_TOOLS:
                        child.display = summary._expanded

        for block in self.query(SubAgentBlock):
            if block._done:
                block._collapsed = not block._collapsed
                block._render_done()

    def action_cancel(self) -> None:
        popup = self.query_one(CompletionPopup)
        if popup.is_visible:
            popup.hide()
            self.query_one("#chat-input", ChatInput).focus()
            return
        if self._agent_task and not self._agent_task.done():
            if self._subagent_task and not self._subagent_task.done():
                task_id = self.task_manager.adopt_running(
                    self._subagent_task, "background task"
                ) if hasattr(self.task_manager, 'adopt_running') else None
                if task_id:
                    self._show_system_message(
                        f"Task moved to background (id: {task_id})"
                    )
                    return
            self._agent_task.cancel()

    async def _prefetch_relevant_memories(self, query: str) -> str:
        """Run the recall selector as a side-query with an 8s timeout.

        Creates a fresh LLM client so the selector's system prompt is
        independent of the main conversation's system prompt. Returns the
        rendered system-reminder body, or "" on any failure / timeout.
        """
        if self.memory_manager is None or self._selected_provider is None:
            return ""

        provider = self._selected_provider
        user_dir = self.memory_manager.user_mem_dir
        project_dir = self.memory_manager.project_mem_dir

        async def selector(system_prompt: str, user_message: str) -> str:
            from codeplus.tools.base import StreamEnd, TextDelta

            side_client = create_client(provider)
            mini_conv = ConversationManager()
            mini_conv.history = [Message(role="user", content=user_message)]
            collected = ""
            async for event in side_client.stream(mini_conv, system=system_prompt):
                if isinstance(event, TextDelta):
                    collected += event.text
                elif isinstance(event, StreamEnd):
                    pass
            return collected

        # 最近用过的工具和已注入过的记忆都挂在 agent 上，跨轮累积
        agent = self.agent
        recent_tools = list(agent.recent_tool_names) if agent else None
        already_surfaced = set(agent.surfaced_memory_paths) if agent else None

        try:
            results = await asyncio.wait_for(
                find_relevant_memories(
                    query=query,
                    user_mem_dir=user_dir,
                    project_mem_dir=project_dir,
                    recent_tools=recent_tools,
                    already_surfaced=already_surfaced,
                    selector=selector,
                ),
                timeout=8.0,
            )
            # 记下这轮注入了哪些，下一轮召回时先排除掉
            if agent is not None:
                for mem in results:
                    agent.surfaced_memory_paths.add(mem.path)
            return render_reminder(results)
        except (asyncio.TimeoutError, Exception):
            return ""

    def _refresh_skills_if_needed(self) -> None:
        """每轮对话前检查 skill 目录 modtime，有变化则自动 reload。"""
        if self.skill_loader is None or self.agent is None:
            return
        if not self.skill_loader.needs_reload():
            return
        self.skill_loader.reload()
        if self.command_registry is not None:
            from codeplus.commands.handlers.skill_register import register_skill_commands
            register_skill_commands(
                self.command_registry, self.skill_loader, self.skill_executor
            )
        catalog = self.skill_loader.get_catalog()
        if catalog:
            lines = ["You can use the following Skills:", ""]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append(
                "If the user's request matches a Skill, call LoadSkill to activate it."
            )
            self.agent.set_skill_catalog("\n".join(lines))
        else:
            self.agent.set_skill_catalog("")

    async def _send_message(self, text: str, is_notification: bool = False) -> None:
        assert self.agent is not None
        self._refresh_skills_if_needed()

        if self._mcp_init_task and not self._mcp_init_task.done():
            self._show_system_message("Waiting for MCP servers to connect...")
            await self._mcp_init_task

        self._streaming = True
        chat = self.query_one("#chat-area", VerticalScroll)
        input_widget = self.query_one("#chat-input", ChatInput)

        if text and "@" in text:
            text = expand_at_refs(text, self.agent.work_dir)

        # Start memory recall prefetch before UI work.
        prefetch_task = asyncio.create_task(
            self._prefetch_relevant_memories(text)
        ) if text else None

        if text:
            user_row = Vertical(classes="user-row")
            await chat.mount(user_row)
            from rich.text import Text as RichText
            user_rich = RichText()
            user_rich.append("❯ ", style="bold color(80)")
            user_rich.append(text, style="bold color(255)")
            user_bubble = Static(user_rich, classes="message user-message")
            await user_row.mount(user_bubble)
            self.call_after_refresh(chat.scroll_end, animate=False)

            self.conversation.add_user_message(text)
            if self.session:
                self.session.append(Message(role="user", content=text))

        if self._mcp_instructions and not self._mcp_instructions_ok:
            self.conversation.add_system_reminder(self._mcp_instructions)
            self._mcp_instructions_ok = True

        # 非阻塞 memory recall：传给 agent，工具执行后注入
        if prefetch_task is not None:
            self.agent.memory_recall_task = prefetch_task
            self.agent._memory_recall_consumed = False

        history_cursor = len(self.conversation.history)

        # 准备 AI 回复区域
        ai_row = Vertical(classes="ai-row")
        await chat.mount(ai_row)
        streaming_label = Static("", classes="message ai-message")
        await ai_row.mount(streaming_label)

        accumulated_text = ""
        tool_blocks: dict[str, ToolCallBlock] = {}

        # 在聊天区底部启动持续旋转的加载动画
        self._thinking_start = _time.monotonic()
        self._thinking_verb = random.choice(THINKING_VERBS)
        self._spinner_idx = 0
        self._spinner_label = Static(
            f"  {SPINNER_FRAMES[0]} {self._thinking_verb}…",
            id="spinner-live",
        )
        await chat.mount(self._spinner_label)

        # Mount teammate tree (initially hidden) below the spinner
        self._teammate_tree = TeammateTree(id="teammate-tree")
        self._teammate_tree.display = False
        await chat.mount(self._teammate_tree)
        self._start_teammate_polling()

        self.call_after_refresh(chat.scroll_end, animate=False)
        self._start_spinner()

        await asyncio.sleep(0)

        try:
            async for event in self.agent.run(self.conversation):
                if isinstance(event, ThinkingText):
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, StreamText):
                    if streaming_label is not None and not accumulated_text:
                        await streaming_label.remove()
                        streaming_label = Static("", classes="message ai-message")
                        await ai_row.mount(streaming_label)
                    accumulated_text += event.text
                    from rich.text import Text as RichText
                    t = RichText()
                    t.append("● ", style="bold color(99)")
                    t.append(accumulated_text)
                    streaming_label.update(t)
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, RetryEvent):
                    self._show_system_message(f"↻ Retrying: {event.reason}")

                elif isinstance(event, ToolUseEvent):
                    if accumulated_text:
                        if streaming_label is not None:
                            await streaming_label.remove()
                        from rich.text import Text as RichText
                        prefix = Static(RichText("●  ", style="bold color(99)"), classes="message")
                        await ai_row.mount(prefix)
                        md = Markdown(accumulated_text, classes="message ai-message")
                        await ai_row.mount(md)
                        streaming_label = None
                        accumulated_text = ""
                    elif streaming_label is not None:
                        await streaming_label.remove()
                        streaming_label = None

                    if _is_subagent_tool(event.tool_name):
                        agent_type = event.arguments.get("subagent_type", "")
                        desc = event.arguments.get("description", "")
                        block = SubAgentBlock(
                            agent_type or "agent",
                            desc,
                            classes="tool-block subagent-block",
                        )
                    else:
                        block = ToolCallBlock(
                            event.tool_name, event.arguments, classes="tool-block"
                        )
                    await ai_row.mount(block)
                    tool_blocks[event.tool_id] = block
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, PermissionRequest):
                    await self._handle_permission_request(event)

                elif isinstance(event, ToolResultEvent):
                    block = tool_blocks.get(event.tool_id)
                    if block:
                        block.set_result(event.output, event.is_error, event.elapsed)
                    self.call_after_refresh(chat.scroll_end, animate=False)

                    ask_tool = self.registry.get("AskUserQuestion")
                    if ask_tool and isinstance(ask_tool, AskUserTool) and ask_tool._pending_event:
                        await self._handle_askuser(ask_tool._pending_event)

                elif isinstance(event, TurnComplete):
                    if self.session:
                        for msg in self.conversation.history[history_cursor:]:
                            self.session.append(msg)
                        history_cursor = len(self.conversation.history)

                    collapsible = [
                        (tid, blk) for tid, blk in tool_blocks.items()
                        if isinstance(blk, ToolCallBlock)
                        and blk.tool_name in COLLAPSIBLE_TOOLS
                        and not blk._loading
                    ]
                    if len(collapsible) >= 2:
                        total_elapsed = sum(b._elapsed for _, b in collapsible)
                        summary = ToolGroupSummary(
                            len(collapsible), total_elapsed,
                            classes="tool-block tool-group-summary",
                        )
                        for _, blk in collapsible:
                            blk.display = False
                        await ai_row.mount(summary)

                    tool_blocks.clear()
                    ai_row = Vertical(classes="ai-row")
                    await chat.mount(ai_row)
                    streaming_label = Static("", classes="message ai-message")
                    await ai_row.mount(streaming_label)
                    accumulated_text = ""
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(event, UsageEvent):
                    pass  # token 展示已移除

                elif isinstance(event, HookEvent):
                    status = "✓" if event.success else "✗"
                    self._show_system_message(
                        f"Hook [{event.hook_id}] {status} {event.output}"
                    )

                elif isinstance(event, CompactNotification):
                    self._show_system_message(event.message)
                    # auto_compact 已重写 conversation.history（摘要 +
                    # boundary + 保留尾部）。先持久化 boundary 记录，然后
                    # 将游标推进到重建后的历史末尾，这样 TurnComplete/LoopComplete
                    # 刷盘时只追加 boundary 之后的新消息，不会把已压缩的
                    # 前缀作为普通记录重复写入。
                    self._persist_compact_boundary(event)
                    history_cursor = len(self.conversation.history)

                elif isinstance(event, ErrorEvent):
                    # 保留错误前已输出的流式文本
                    if accumulated_text and streaming_label is not None:
                        await streaming_label.remove()
                        md = Markdown(accumulated_text, classes="message ai-message")
                        await ai_row.mount(md)
                        streaming_label = None
                        accumulated_text = ""
                    self._show_error(event.message)

                elif isinstance(event, LoopComplete):
                    total_time = _time.monotonic() - self._thinking_start
                    done_label = Static(
                        f"✻ {_to_past_tense(self._thinking_verb)} for {total_time:.1f}s",
                        classes="message thinking-done",
                    )
                    await ai_row.mount(done_label)
                    if self.session:
                        for msg in self.conversation.history[history_cursor:]:
                            self.session.append(msg)
                        history_cursor = len(self.conversation.history)
                        self.session.meta.total_tokens = (
                            self.agent.total_input_tokens
                            + self.agent.total_output_tokens
                        )
                        asyncio.ensure_future(
                            self._update_session_summary()
                        )
                    if self.agent.plan_mode:
                        asyncio.ensure_future(
                            self._show_plan_approval()
                        )

            # 收尾：渲染剩余的累积文本
            if accumulated_text and streaming_label is not None:
                await streaming_label.remove()
                md = Markdown(accumulated_text, classes="message ai-message")
                await ai_row.mount(md)
            elif streaming_label is not None:
                await streaming_label.remove()

            self.call_after_refresh(chat.scroll_end, animate=False)

        except asyncio.CancelledError:
            if accumulated_text:
                if streaming_label is not None:
                    await streaming_label.remove()
                md = Markdown(
                    accumulated_text + "\n\n*[cancelled]*",
                    classes="message ai-message",
                )
                await ai_row.mount(md)
            self._show_system_message("Operation cancelled")
        except LLMError as e:
            self._show_error(str(e))
        finally:
            self._finish_streaming()
            input_widget.focus()

            await self._process_task_notifications()

    async def _process_task_notifications(self) -> None:
        raise NotImplementedError("Implementation pending")

    async def _start_notification_polling(self) -> None:
        while True:
            await asyncio.sleep(2)
            if not self._streaming and self.agent is not None:
                await self._process_task_notifications()
                await self._process_mailbox_notifications()

    async def _process_mailbox_notifications(self) -> None:
        if not hasattr(self, "team_manager") or self.team_manager is None:
            return
        if self._streaming or self.agent is None:
            return
        notes = self.team_manager.drain_lead_mailbox()
        if not notes:
            return
        for note in notes:
            self.conversation.add_system_reminder(note)
        self._agent_task = asyncio.create_task(
            self._send_message("", is_notification=True)
        )

    async def _show_plan_approval(self) -> None:
        from codeplus.plan_dialog import InlinePlanWidget

        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlinePlanWidget()
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_plan_widget_responded(
        self, event: "InlinePlanWidget.Responded"
    ) -> None:
        raise NotImplementedError("Implementation pending")

    async def _handle_askuser(self, event: AskUserEvent) -> None:
        from codeplus.askuser_dialog import InlineAskUserWidget

        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlineAskUserWidget(event.questions)
        self._pending_askuser_event = event
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_ask_user_widget_responded(
        self, event: "InlineAskUserWidget.Responded"
    ) -> None:
        raise NotImplementedError("Implementation pending")

    def _start_spinner(self) -> None:
        """启动 braille spinner 动画（每帧 80ms）。"""
        if self._spinner_timer is not None:
            return
        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)

    def _stop_spinner(self) -> None:
        """停止 spinner 动画。"""
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _finish_streaming(self) -> None:
        """清理所有 streaming 状态（取消或完成时调用）。"""
        self._streaming = False
        self._stop_spinner()
        self._stop_teammate_polling()
        self._agent_task = None
        if self._teammate_tree is not None:
            self._teammate_tree.remove()
            self._teammate_tree = None
        if self._spinner_label is not None:
            self._spinner_label.remove()
            self._spinner_label = None

    def _tick_spinner(self) -> None:
        raise NotImplementedError("Implementation pending")

    def _start_teammate_polling(self) -> None:
        """Start polling teammate progress every 0.5s."""
        if self._teammate_timer is not None:
            return
        self._teammate_timer = self.set_interval(0.5, self._tick_teammate_tree)

    def _stop_teammate_polling(self) -> None:
        """Stop the teammate progress polling timer."""
        if self._teammate_timer is not None:
            self._teammate_timer.stop()
            self._teammate_timer = None

    def _tick_teammate_tree(self) -> None:
        raise NotImplementedError("Implementation pending")

    def _update_teammates_label(self, count: int) -> None:
        """Update the teammates count in the status bar."""
        try:
            label = self.query_one("#teammates-label", Static)
            if count > 0:
                label.update(f"[cyan]● {count} teammate{'s' if count != 1 else ''}[/cyan]  ")
            else:
                label.update("")
        except Exception:
            pass

    async def _handle_permission_request(self, request: PermissionRequest) -> None:
        from codeplus.permission_dialog import InlinePermissionWidget

        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlinePermissionWidget(request.tool_name, request.description)
        self._pending_perm_request = request
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        # 权限提示弹窗期间禁用输入框
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_permission_widget_responded(
        self, event: "InlinePermissionWidget.Responded"
    ) -> None:
        raise NotImplementedError("Implementation pending")

    # -----------------------------------------------------------------
    # 恢复 session 的消息渲染
    # -----------------------------------------------------------------

    async def _render_restored_messages(self, messages: list[Message]) -> None:
        raise NotImplementedError("Implementation pending")

    # -----------------------------------------------------------------
    # Session 摘要（异步后台生成）
    # -----------------------------------------------------------------

    async def _update_session_summary(self) -> None:
        raise NotImplementedError("Implementation pending")

    # -----------------------------------------------------------------
    # MCP
    # -----------------------------------------------------------------

    async def _init_mcp(self) -> None:
        raise NotImplementedError("Implementation pending")

    async def _shutdown_mcp(self) -> None:
        if self._mcp_init_task is not None:
            self._mcp_init_task.cancel()
            try:
                await self._mcp_init_task
            except (asyncio.CancelledError, Exception):
                pass
            self._mcp_init_task = None
        if self.mcp_manager is not None:
            await self.mcp_manager.shutdown()
            self.mcp_manager = None

    # -----------------------------------------------------------------
    # 退出
    # -----------------------------------------------------------------

    async def action_handle_ctrl_c(self) -> None:
        raise NotImplementedError("Implementation pending")

    def _show_error(self, text: str) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        error_widget = Static(f"✖ {text}", classes="message error-message")
        chat.mount(error_widget)
        self.call_after_refresh(chat.scroll_end, animate=False)

    def _show_system_message(self, text: str) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        msg = Static(f"  {text}", classes="message system-message")
        chat.mount(msg)
        self.call_after_refresh(chat.scroll_end, animate=False)

    _MODE_DISPLAY = {
        PermissionMode.DEFAULT: "default",
        PermissionMode.ACCEPT_EDITS: "accept-edits",
        PermissionMode.PLAN: "plan",
        PermissionMode.BYPASS: "YOLO",
    }

    def _update_mode_label(self) -> None:
        raise NotImplementedError("Implementation pending")

    def _update_token_label(self, input_tokens: int, output_tokens: int) -> None:
        pass  # token 标签已从 UI 中移除
