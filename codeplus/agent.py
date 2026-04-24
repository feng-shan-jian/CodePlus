from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from pydantic import ValidationError

from codeplus.client import LLMClient
from codeplus.context import (
    CompactBoundary,
    CompactCircuitBreaker,
    CompactEvent,
    RecoveryState,
    auto_compact,
    ensure_session_dir,
)
from codeplus.conversation import ConversationManager, ToolResultBlock, ToolUseBlock
from codeplus.conversation_pairing import REJECTED_TOOL_RESULT
from codeplus.conversation import ThinkingBlock as ConvThinkingBlock
from codeplus.memory.auto_memory import MemoryManager
from codeplus.permissions import (
    Decision,
    PermissionChecker,
    PermissionMode,
)
from codeplus.hooks import HookContext, HookEngine, ToolRejectedError
from codeplus.hooks.engine import HookNotification
from codeplus.prompts import build_environment_context, build_plan_mode_reminder, build_system_prompt
from codeplus.tools import ToolRegistry
from codeplus.tools.base import (
    MAX_OUTPUT_CHARS,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResult,
)

log = logging.getLogger(__name__)

MEMORY_EXTRACTION_INTERVAL = 1
# 传给记忆召回选择器的最近工具名上限，去重后保留最近这么多个
MAX_RECENT_TOOLS = 10
MAX_TOKENS_CEILING = 64000
MAX_OUTPUT_TOKENS_RECOVERIES = 3


# ---------------------------------------------------------------------------
# AgentEvent 事件类型
# ---------------------------------------------------------------------------

@dataclass
class StreamText:
    text: str


@dataclass
class ThinkingText:
    text: str


@dataclass
class RetryEvent:
    reason: str
    wait: float = 0.0


@dataclass
class ToolUseEvent:
    tool_name: str
    tool_id: str
    arguments: dict[str, Any]


@dataclass
class ToolResultEvent:
    tool_id: str
    tool_name: str
    output: str
    is_error: bool
    elapsed: float


@dataclass
class TurnComplete:
    turn: int


@dataclass
class LoopComplete:
    total_turns: int


@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int


@dataclass
class ErrorEvent:
    message: str


@dataclass
class CompactNotification:
    before_tokens: int
    message: str
    # 结构化 boundary（摘要 + 原文保留尾部），UI/session 层用它持久化 compact_boundary 记录。
    # 失败路径下为 None。
    boundary: "CompactBoundary | None" = None


@dataclass
class HookEvent:
    hook_id: str
    event: str
    output: str
    success: bool


class PermissionResponse(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ALLOW_ALWAYS = "allow_always"


@dataclass
class PermissionRequest:
    tool_name: str
    description: str
    future: asyncio.Future[PermissionResponse]


AgentEvent = (
    StreamText
    | ThinkingText
    | RetryEvent
    | ToolUseEvent
    | ToolResultEvent
    | TurnComplete
    | LoopComplete
    | UsageEvent
    | ErrorEvent
    | PermissionRequest
    | CompactNotification
    | HookEvent
)


# ---------------------------------------------------------------------------
# LLM 响应收集器
# ---------------------------------------------------------------------------

@dataclass
class ThinkingBlock:
    thinking: str
    signature: str


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCallComplete] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0


class StreamCollector:
    def __init__(self) -> None:
        self.response = LLMResponse()

    async def consume(
        self, stream: AsyncIterator[StreamEvent]
    ) -> AsyncIterator[AgentEvent]:
        async for event in stream:
            if isinstance(event, TextDelta):
                self.response.text += event.text
                yield StreamText(text=event.text)
            elif isinstance(event, ThinkingDelta):
                yield ThinkingText(text=event.text)
            elif isinstance(event, ThinkingComplete):
                self.response.thinking_blocks.append(
                    ThinkingBlock(thinking=event.thinking, signature=event.signature)
                )
            elif isinstance(event, ToolCallStart):
                pass
            elif isinstance(event, ToolCallDelta):
                pass
            elif isinstance(event, ToolCallComplete):
                self.response.tool_calls.append(event)
                yield ToolUseEvent(
                    tool_name=event.tool_name,
                    tool_id=event.tool_id,
                    arguments=event.arguments,
                )
            elif isinstance(event, StreamEnd):
                self.response.stop_reason = event.stop_reason
                self.response.input_tokens = event.input_tokens
                self.response.output_tokens = event.output_tokens
                self.response.cache_read = event.cache_read
                self.response.cache_creation = event.cache_creation


# ---------------------------------------------------------------------------
# tool 批量执行
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# 单条工具执行结果
# ---------------------------------------------------------------------------

@dataclass
class _ToolExecResult:
    tool_id: str
    tool_name: str
    result: ToolResult
    elapsed: float


# ---------------------------------------------------------------------------
# Agent 主循环
# ---------------------------------------------------------------------------

# 延迟工具清单提醒的固定开头。用它在历史里回认这条提醒还在不在：compact 把历史压
# 成摘要之后原来那条就没了，得重发一遍
DEFERRED_REMINDER_MARKER = (
    "The following deferred tools are available via ToolSearch."
)


class Agent:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        protocol: str,
        work_dir: str = ".",
        max_iterations: int = 0,
        permission_checker: PermissionChecker | None = None,
        context_window: int = 200_000,
        instructions_content: str = "",
        memory_manager: MemoryManager | None = None,
        hook_engine: HookEngine | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.protocol = protocol
        self.work_dir = work_dir
        self.max_iterations = max_iterations
        self.permission_checker = permission_checker
        self.permission_mode: PermissionMode = (
            permission_checker.mode if permission_checker else PermissionMode.DEFAULT
        )
        self.context_window = context_window
        self.compact_breaker = CompactCircuitBreaker()
        # 保存重建工作上下文所需的快照，在 Layer 2 压缩对话后使用：
        # 最近的文件读取和 skill 调用。每次 ReadFile / skill 调用时记录，
        # auto_compact 触发阈值时消费。
        self.recovery_state: RecoveryState = RecoveryState()
        # 最近调用过的工具名，去重后按调用顺序保留。传给记忆召回的选择器，
        # 让它跳过这些工具的用法说明类记忆（正在用的东西不需要说明书），
        # 但涉及坑和警告的记忆仍然要选出来。
        self.recent_tool_names: list[str] = []
        # 本次会话已经注入过的记忆文件路径。召回前做预过滤，避免同一条记忆
        # 每轮重新占用选择器的名额，也避免同样的正文反复进上下文。
        self.surfaced_memory_paths: set[str] = set()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.instructions_content = instructions_content
        self.memory_manager = memory_manager
        self.hook_engine = hook_engine
        self._loop_count = 0
        # 上一次告诉模型的延迟工具清单，按字典序。跟当前清单一比就知道工具池有没有
        # 变，没变就不重发那条提醒
        self._announced_deferred: list[str] = []
        # 记忆提取合并策略：_extracting 期间触发新请求会标记 _pending_extraction，
        # _extracting: 标记是否有提取正在进行
        # _pending_extraction: 提取期间又触发了新请求，标记需要尾随提取
        self._extracting = False
        self._pending_extraction = False
        self._consolidator: MemoryConsolidator | None = None
        if memory_manager is not None:
            from codeplus.memory.consolidation import MemoryConsolidator
            self._consolidator = MemoryConsolidator(work_dir)
        self.session_id: str = ""
        self.active_skills: dict[str, str] = {}
        self._skill_catalog: str = ""
        self._agent_catalog: str = ""
        self._agent_catalog_list: list[tuple[str, str]] = []
        self.agent_id: str = uuid.uuid4().hex[:12]
        self.parent_id: str | None = None
        self.trace_id: str | None = None
        self.team_name: str = ""
        self._team_manager: Any = None
        # coordinator 模式的开关，由配置显式打开
        self.enable_coordinator_mode: bool = False
        self.notification_fn: Callable[[], list[str]] | None = None
        self.file_history: Any = None

        # 非阻塞 memory recall：prefetch task 与主 LLM 调用并行，工具执行后注入
        self.memory_recall_task: Any | None = None
        self._memory_recall_consumed: bool = False

    def _announce_deferred_tools(self, conversation: ConversationManager) -> None:
        return

    @property
    def session_dir(self) -> Path:
        # 溢写目录跟随当前会话 id（resume 换会话后自动指向新目录）
        return ensure_session_dir(self.work_dir, self.session_id)

    @property
    def _transcript_path(self) -> str:
        if self.session_id:
            return str(Path(self.work_dir) / ".codeplus" / "sessions" / f"{self.session_id}.jsonl")
        return ""

    @property
    def plan_mode(self) -> bool:
        return self.permission_mode == PermissionMode.PLAN


    @property
    def coordinator_mode(self) -> bool:
        """coordinator 模式是否生效，只看配置开关。

        不看团队是否存在：模式在会话中途切换会留下麻烦，已经发出去的调度指引
        留在对话历史里撤不回来，模型会照着过期的约束继续做事。
        配置说了算，从第一轮到最后一轮都是同一套规则。
        """
        return self.enable_coordinator_mode

    _plan_path_cache: Path | None = None

    def _get_plan_path(self) -> Path:
        if self._plan_path_cache is not None:
            return self._plan_path_cache
        import random
        import datetime
        _ADJECTIVES = ["bold", "bright", "calm", "cool", "deep", "fair", "fast", "fine",
                       "glad", "keen", "kind", "lean", "mild", "neat", "pure", "safe",
                       "slim", "soft", "tall", "warm", "wise", "grand", "swift", "vivid"]
        _NOUNS = ["sketch", "draft", "spark", "bloom", "trail", "ridge", "creek", "grove",
                  "cliff", "cloud", "field", "forge", "frost", "haven", "pearl", "stone",
                  "storm", "river", "tower", "delta", "flame", "orbit", "pulse", "shore"]
        plans_dir = Path(self.work_dir) / ".codeplus" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%m%d-%H%M")
        slug = f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}-{ts}"
        self._plan_path_cache = plans_dir / f"{slug}.md"
        return self._plan_path_cache

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.permission_mode = mode
        if self.permission_checker:
            self.permission_checker.mode = mode

    def activate_skill(self, name: str, prompt_body: str) -> None:
        self.active_skills[name] = prompt_body

    def clear_active_skills(self) -> None:
        self.active_skills.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        self._skill_catalog = catalog


    def set_agent_catalog(self, catalog: str, catalog_list: list[tuple[str, str]] | None = None) -> None:
        self._agent_catalog = catalog
        if catalog_list is not None:
            self._agent_catalog_list = catalog_list

    def _build_hook_context(self, event: str, **kwargs: str | dict) -> HookContext:
        return HookContext(
            event_name=event,
            tool_name=str(kwargs.get("tool_name", "")),
            tool_args=kwargs.get("tool_args", {}),
            file_path=str(kwargs.get("file_path", "")),
            message=str(kwargs.get("message", "")),
            error=str(kwargs.get("error", "")),
        )

    def _infer_file_path(self, args: dict) -> str:
        return str(args.get("file_path", args.get("path", "")))

    def _drain_hook_events(self) -> list[HookEvent]:
        if not self.hook_engine:
            return []
        return [
            HookEvent(
                hook_id=n.hook_id,
                event=n.event,
                output=n.output,
                success=n.success,
            )
            for n in self.hook_engine.drain_notifications()
        ]

    async def run(self, conversation: ConversationManager) -> AsyncIterator[AgentEvent]:
        if False:
            yield None
        raise NotImplementedError("Implementation pending")


    def _consume_mailbox(self, conversation: ConversationManager) -> None:
        raise NotImplementedError("Implementation pending")

    def _build_permission_description(self, tc: ToolCallComplete) -> str:
        """为 HITL 权限确认生成人类可读的操作描述。"""
        return PermissionChecker.describe_tool_action(tc.tool_name, tc.arguments)

    async def _execute_ordered_tools(
        self, calls: list[ToolCallComplete]
    ) -> AsyncIterator[_ToolExecResult | PermissionRequest]:
        for tc in calls:
            async for item in self._execute_tool(tc):
                if isinstance(item, PermissionRequest):
                    yield item
                else:
                    result, elapsed = item
                    yield _ToolExecResult(tc.tool_id, tc.tool_name, result, elapsed)




    async def _execute_tool(
        self, tc: ToolCallComplete
    ) -> AsyncIterator[tuple[ToolResult, float] | PermissionRequest]:
        if False:
            yield None
        raise NotImplementedError("Implementation pending")

    def _record_recent_tool(self, name: str) -> None:
        """记下刚执行完的工具名，供记忆召回的选择器参考。

        重复调用同一个工具时把它移到末尾，这样列表反映的是最近使用顺序
        而不是首次使用顺序。
        """
        if not name:
            return
        if name in self.recent_tool_names:
            self.recent_tool_names.remove(name)
        self.recent_tool_names.append(name)
        if len(self.recent_tool_names) > MAX_RECENT_TOOLS:
            del self.recent_tool_names[0]

    def _snapshot_for_recovery(
        self, tc: ToolCallComplete, result: ToolResult
    ) -> None:
        raise NotImplementedError("Implementation pending")

    async def _extract_memories(
        self, conversation: ConversationManager
    ) -> None:
        raise NotImplementedError("Implementation pending")

    async def manual_compact(
        self, conversation: ConversationManager
    ) -> CompactNotification | ErrorEvent:
        # auto_compact 会用摘要替换 conversation.history，所有 tool-result 内容
        # （原始或已替换的）都将被丢弃。这里跳过 apply_tool_result_budget —
        # 它在主循环中的唯一目的是为 LLM 调用生成 api_conv，而本路径不需要
        # 发起看到替换结果的 LLM 调用（auto_compact 内部的摘要调用操作的是原始对话）。
        raise NotImplementedError("Implementation pending")

    async def run_to_completion(
        self, task: str, conversation: ConversationManager | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        raise NotImplementedError("Implementation pending")

    async def _execute_tool_noninteractive(
        self, tc: ToolCallComplete
    ) -> ToolResult:
        raise NotImplementedError("Implementation pending")

    def _maybe_persist_or_truncate(
        self, tool_use_id: str, text: str, exempt_ids: set[str] | None = None
    ) -> str:
        return text
