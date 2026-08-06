"""Agent Loop 的集成测试 —— 以编程方式逐项验证 checklist。"""
from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator

import pytest

from codeplus.agent import (
    Agent,
    ErrorEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    StreamText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
    partition_tool_calls,
)
from codeplus.prompts import build_environment_context, build_plan_mode_reminder, build_system_prompt
from codeplus.client import LLMClient
from codeplus.conversation import ConversationManager
from codeplus.serialization import build_anthropic_messages
from codeplus.tools import create_default_registry
from codeplus.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCallComplete,
)

# ---------------------------------------------------------------------------
# 返回预设脚本响应的 mock LLM 客户端
# ---------------------------------------------------------------------------

class MockLLMClient(LLMClient):
    def __init__(self, responses: list[list[StreamEvent]], yield_control: bool = False) -> None:
        self._responses = list(responses)
        self._call_index = 0
        self._yield_control = yield_control

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if self._call_index >= len(self._responses):
            yield TextDelta(text="No more responses")
            yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)
            return
        events = self._responses[self._call_index]
        self._call_index += 1
        for e in events:
            if self._yield_control:
                await asyncio.sleep(0)
            yield e

def _collect(events: list) -> dict[str, list]:
    result: dict[str, list] = {
        "text": [], "tool_use": [], "tool_result": [],
        "turn": [], "loop": [], "usage": [], "error": [],
    }
    for e in events:
        if isinstance(e, StreamText):
            result["text"].append(e.text)
        elif isinstance(e, ToolUseEvent):
            result["tool_use"].append(e)
        elif isinstance(e, ToolResultEvent):
            result["tool_result"].append(e)
        elif isinstance(e, TurnComplete):
            result["turn"].append(e)
        elif isinstance(e, LoopComplete):
            result["loop"].append(e)
        elif isinstance(e, UsageEvent):
            result["usage"].append(e)
        elif isinstance(e, ErrorEvent):
            result["error"].append(e)
    return result

# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_step_tool_call():
    """Agent 调用一次 ReadFile，拿到结果后停止。"""
    client = MockLLMClient([
        # 第 1 轮：模型调用 ReadFile
        [
            TextDelta("Let me read the file."),
            ToolCallComplete("t1", "ReadFile", {"file_path": "CODEPLUS.md"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=20),
        ],
        # 第 2 轮：模型给出最终答案
        [
            TextDelta("The file contains project info."),
            StreamEnd("end_turn", input_tokens=30, output_tokens=15),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", work_dir=".")
    conv = ConversationManager()
    conv.add_user_message("Read CODEPLUS.md")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["tool_use"]) == 1
    assert c["tool_use"][0].tool_name == "ReadFile"
    assert len(c["tool_result"]) == 1
    assert len(c["turn"]) == 1
    assert len(c["loop"]) == 1
    assert c["loop"][0].total_turns == 2

@pytest.mark.asyncio
async def test_multi_step_autonomous():
    """Agent 先 WriteFile 再 ReadFile 然后停止 —— 端到端的多步流程。"""
    # 清理残留文件，避免 read-before-edit 拦截新文件创建
    test_file = "/tmp/codeplus_test_hello.txt"
    if os.path.exists(test_file):
        os.remove(test_file)
    client = MockLLMClient([
        # 第 1 轮：WriteFile
        [
            TextDelta("Creating file."),
            ToolCallComplete("t1", "WriteFile", {"file_path": "/tmp/codeplus_test_hello.txt", "content": "Hello World"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=20),
        ],
        # 第 2 轮：ReadFile 进行验证
        [
            TextDelta("Verifying content."),
            ToolCallComplete("t2", "ReadFile", {"file_path": "/tmp/codeplus_test_hello.txt"}),
            StreamEnd("end_turn", input_tokens=40, output_tokens=25),
        ],
        # 第 3 轮：最终答案
        [
            TextDelta("File created and verified. Content is correct."),
            StreamEnd("end_turn", input_tokens=60, output_tokens=30),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", work_dir="/tmp")
    conv = ConversationManager()
    conv.add_user_message("Create hello.txt with Hello World, then verify")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["tool_use"]) == 2
    assert c["tool_use"][0].tool_name == "WriteFile"
    assert c["tool_use"][1].tool_name == "ReadFile"
    assert len(c["turn"]) == 2
    assert len(c["loop"]) == 1
    assert c["loop"][0].total_turns == 3
    # 验证文件确实被创建了
    assert not c["tool_result"][0].is_error
    assert not c["tool_result"][1].is_error

@pytest.mark.asyncio
async def test_stop_end_turn():
    """模型以 end_turn 自然停止。"""
    client = MockLLMClient([
        [
            TextDelta("Hello! How can I help?"),
            StreamEnd("end_turn", input_tokens=5, output_tokens=10),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic")
    conv = ConversationManager()
    conv.add_user_message("Hi")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["loop"]) == 1
    assert c["loop"][0].total_turns == 1
    assert len(c["error"]) == 0

@pytest.mark.asyncio
async def test_stop_max_iterations():
    """Agent 在达到 max_iterations 后停止。"""
    # 每个响应都带有工具调用，因此循环永远不会自然结束
    responses = []
    for i in range(5):
        responses.append([
            TextDelta(f"Step {i}"),
            ToolCallComplete(f"t{i}", "ReadFile", {"file_path": "CODEPLUS.md"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=10),
        ])

    client = MockLLMClient(responses)
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", max_iterations=2)
    conv = ConversationManager()
    conv.add_user_message("Do something")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["error"]) == 1
    assert "maximum iterations" in c["error"][0].message

@pytest.mark.asyncio
async def test_stop_cancel():
    """Agent 在收到 CancelledError 时干净地停止。"""

    class SlowMockClient(LLMClient):
        """在事件之间 sleep 的 mock 客户端，以便留出取消的时机。"""
        def __init__(self) -> None:
            self._call_count = 0

        async def stream(
            self,
            conversation: ConversationManager,
            system: str = "",
            tools: list[dict[str, Any]] | None = None,
        ) -> AsyncIterator[StreamEvent]:
            self._call_count += 1
            await asyncio.sleep(0.01)
            yield TextDelta(f"Step {self._call_count}")
            await asyncio.sleep(0.01)
            yield ToolCallComplete(f"t{self._call_count}", "ReadFile", {"file_path": "CODEPLUS.md"})
            await asyncio.sleep(0.01)
            yield StreamEnd("end_turn", input_tokens=10, output_tokens=10)

    client = SlowMockClient()
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic")
    conv = ConversationManager()
    conv.add_user_message("Do something")

    events: list = []
    cancelled = False

    async def run_agent():
        async for e in agent.run(conv):
            events.append(e)

    task = asyncio.create_task(run_agent())
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        cancelled = True

    assert cancelled
    c = _collect(events)
    assert len(c["turn"]) < 50

@pytest.mark.asyncio
async def test_unknown_tool_returns_error_result():
    """调用未知工具只回一条错误结果，循环继续，由模型自己决定何时收尾。"""
    responses = []
    for i in range(3):
        responses.append([
            TextDelta(f"Trying tool {i}"),
            ToolCallComplete(f"t{i}", "NonExistentTool", {"arg": "val"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=10),
        ])
    # 模型放弃猜工具名，改成纯文本回复，循环正常结束
    responses.append([
        TextDelta("这个工具不存在，我换个方式。"),
        StreamEnd("end_turn", input_tokens=10, output_tokens=10),
    ])

    client = MockLLMClient(responses)
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic")
    conv = ConversationManager()
    conv.add_user_message("Do something")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["error"]) == 0
    assert len(c["tool_result"]) == 3
    assert all(tr.is_error and "unknown tool" in tr.output for tr in c["tool_result"])
    assert len(c["loop"]) == 1

@pytest.mark.asyncio
async def test_message_splicing():
    """assistant 消息包含 text + 多个 tool_use；对应的 tool_result 被打包在一起。"""
    client = MockLLMClient([
        # 第 1 轮：一个响应里包含两次工具调用
        [
            TextDelta("Reading two files."),
            ToolCallComplete("t1", "ReadFile", {"file_path": "CODEPLUS.md"}),
            ToolCallComplete("t2", "ReadFile", {"file_path": "pyproject.toml"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=20),
        ],
        # 第 2 轮：最终响应
        [
            TextDelta("Done."),
            StreamEnd("end_turn", input_tokens=30, output_tokens=10),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", work_dir=".")
    conv = ConversationManager()
    conv.add_user_message("Read both files")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    # 检查对话历史
    msgs = build_anthropic_messages(conv.get_messages())
    # env_context(user) 和 user_message 被合并为一条 → merged_user + assistant(text+2 个 tool_use) + user(2 个 tool_result) + assistant(最终响应)
    assert len(msgs) == 4
    assistant_msg = msgs[1]
    assert assistant_msg["role"] == "assistant"
    assert len(assistant_msg["content"]) == 3  # text + 2 个 tool_use
    tool_results_msg = msgs[2]
    assert tool_results_msg["role"] == "user"
    assert len(tool_results_msg["content"]) == 2  # 2 个 tool_result
    assert tool_results_msg["content"][0]["tool_use_id"] == "t1"
    assert tool_results_msg["content"][1]["tool_use_id"] == "t2"

@pytest.mark.asyncio
async def test_concurrent_batch_execution():
    """多个 ReadFile 调用并发执行（属于同一批次）。"""
    client = MockLLMClient([
        [
            ToolCallComplete("t1", "ReadFile", {"file_path": "CODEPLUS.md"}),
            ToolCallComplete("t2", "ReadFile", {"file_path": "pyproject.toml"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=20),
        ],
        [
            TextDelta("Both files read."),
            StreamEnd("end_turn", input_tokens=30, output_tokens=10),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", work_dir=".")
    conv = ConversationManager()
    conv.add_user_message("Read both")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["tool_result"]) == 2
    # 两个都应成功（这些文件在项目根目录下存在）
    assert all(not r.is_error for r in c["tool_result"])

@pytest.mark.asyncio
async def test_token_usage_accumulates():
    """Usage 事件展示的是累计的 token 数量。"""
    client = MockLLMClient([
        [
            TextDelta("Step 1"),
            ToolCallComplete("t1", "ReadFile", {"file_path": "CODEPLUS.md"}),
            StreamEnd("end_turn", input_tokens=100, output_tokens=50),
        ],
        [
            TextDelta("Step 2"),
            ToolCallComplete("t2", "ReadFile", {"file_path": "CODEPLUS.md"}),
            StreamEnd("end_turn", input_tokens=200, output_tokens=80),
        ],
        [
            TextDelta("Done."),
            StreamEnd("end_turn", input_tokens=300, output_tokens=100),
        ],
    ])
    registry = create_default_registry()
    agent = Agent(client, registry, "anthropic", work_dir=".")
    conv = ConversationManager()
    conv.add_user_message("Test")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    assert len(c["usage"]) == 3
    assert c["usage"][0].input_tokens == 100
    assert c["usage"][0].output_tokens == 50
    assert c["usage"][1].input_tokens == 300
    assert c["usage"][1].output_tokens == 130
    assert c["usage"][2].input_tokens == 600
    assert c["usage"][2].output_tokens == 230

@pytest.mark.asyncio
async def test_plan_mode():
    """通过 permission_mode 切换 plan 模式。"""
    from codeplus.permissions import PermissionMode

    registry = create_default_registry()
    agent = Agent(MockLLMClient([]), registry, "anthropic")

    agent.set_permission_mode(PermissionMode.PLAN)
    assert agent.plan_mode is True

    agent.set_permission_mode(PermissionMode.DEFAULT)
    assert agent.plan_mode is False
    schemas = registry.get_all_schemas()
    names = [s["name"] for s in schemas]
    assert "WriteFile" in names
    assert "EditFile" in names
    assert "Bash" in names

@pytest.mark.asyncio
async def test_plan_mode_denied_tool_returns_error():
    """在 plan 模式下，写入类工具需要审批（effect=ask）；当用户
    拒绝时，工具返回一个错误结果，而不会真正执行。"""
    from codeplus.permissions import (
        DangerousCommandDetector,
        PathSandbox,
        PermissionChecker,
        PermissionMode,
        RuleEngine,
    )

    client = MockLLMClient([
        [
            TextDelta("Let me write..."),
            ToolCallComplete("t1", "WriteFile", {"file_path": "x.txt", "content": "hi"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=20),
        ],
        [
            TextDelta("OK, I can't write in plan mode."),
            StreamEnd("end_turn", input_tokens=30, output_tokens=15),
        ],
    ])
    registry = create_default_registry()
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox("."),
        rule_engine=RuleEngine(),
        mode=PermissionMode.PLAN,
    )
    agent = Agent(client, registry, "anthropic", permission_checker=checker)
    agent.set_permission_mode(PermissionMode.PLAN)
    conv = ConversationManager()
    conv.add_user_message("Write a file")

    events = []
    async for e in agent.run(conv):
        events.append(e)
        # plan 模式在写入前会询问；这里模拟用户拒绝。
        if isinstance(e, PermissionRequest):
            e.future.set_result(PermissionResponse.DENY)

    c = _collect(events)
    assert len(c["tool_result"]) == 1
    assert c["tool_result"][0].is_error
    out = c["tool_result"][0].output.lower()
    assert "rejected" in out or "denied" in out or "拒绝" in c["tool_result"][0].output
    assert len(c["error"]) == 0

def test_partition_tool_calls():
    """分批逻辑会把可并发执行的调用归到同一组。"""
    from codeplus.tools.base import ToolCallComplete

    calls = [
        ToolCallComplete("1", "ReadFile", {}),
        ToolCallComplete("2", "ReadFile", {}),
        ToolCallComplete("3", "EditFile", {}),
        ToolCallComplete("4", "ReadFile", {}),
        ToolCallComplete("5", "ReadFile", {}),
    ]
    registry = create_default_registry()
    batches = partition_tool_calls(calls, registry)
    assert len(batches) == 3
    assert batches[0].concurrent and len(batches[0].calls) == 2
    assert not batches[1].concurrent and len(batches[1].calls) == 1
    assert batches[2].concurrent and len(batches[2].calls) == 2

def test_system_prompt_normal():
    sp = build_system_prompt()
    assert "CodePlus" in sp
    assert "Plan mode" not in sp

def test_system_prompt_plan():
    reminder = build_plan_mode_reminder("/tmp/plan.md", False, 1)
    assert "Plan mode" in reminder
    assert "MUST NOT" in reminder

def test_plan_mode_sparse_reminder():
    reminder = build_plan_mode_reminder("/tmp/plan.md", True, 8)
    assert "Plan mode still active" in reminder

def test_environment_context():
    # 工作目录和操作系统归 System Prompt 的环境段，这里只负责每轮会变的时间
    ctx = build_environment_context("/home/user/project")
    assert "Current time" in ctx
    assert "/home/user/project" not in ctx
    assert "Operating system" not in ctx


def test_system_prompt_carries_work_dir():
    sp = build_system_prompt(work_dir="/home/user/project")
    assert "/home/user/project" in sp
    assert "Platform" in sp


@pytest.mark.asyncio
async def test_tools_wait_for_complete_response():
    """模型响应收齐前不执行工具，防止半条响应产生副作用。"""
    execution_log: list[tuple[str, float]] = []
    original_execute = None

    # 给事件循环执行机会，确认工具没有在流结束前偷偷启动。
    class SlowMockClient(MockLLMClient):
        async def stream(self, conversation, system="", tools=None):
            events = self._responses[self._call_index]
            self._call_index += 1
            for e in events:
                if isinstance(e, StreamEnd) and self._call_index == 1:
                    await asyncio.sleep(0)
                    assert execution_log == []
                yield e
                await asyncio.sleep(0)

    client = SlowMockClient([
        [
            ToolCallComplete("t1", "Glob", {"pattern": "*.py"}),
            ToolCallComplete("t2", "Glob", {"pattern": "*.toml"}),
            StreamEnd("end_turn", input_tokens=10, output_tokens=20),
        ],
        [
            TextDelta("Done."),
            StreamEnd("end_turn", input_tokens=30, output_tokens=10),
        ],
    ])
    registry = create_default_registry()

    # 记录工具执行时间
    glob_tool = registry.get("Glob")
    original_execute = glob_tool.execute

    async def patched_execute(params):
        import time
        execution_log.append(("start", time.monotonic()))
        result = await original_execute(params)
        execution_log.append(("end", time.monotonic()))
        return result

    glob_tool.execute = patched_execute

    agent = Agent(client, registry, "anthropic", work_dir=".")
    conv = ConversationManager()
    conv.add_user_message("Find files")

    events = []
    async for e in agent.run(conv):
        events.append(e)

    c = _collect(events)
    # 两个 Glob 调用都应产出结果
    assert len(c["tool_result"]) == 2
    assert len(execution_log) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("approval", [PermissionResponse.ALLOW, PermissionResponse.DENY])
async def test_same_response_write_then_read_waits_for_permission(tmp_path, approval):
    from codeplus.permissions import (
        DangerousCommandDetector, PathSandbox, PermissionChecker, RuleEngine,
    )

    target = tmp_path / "created.txt"
    calls = [
        ToolCallComplete("write", "WriteFile", {"file_path": str(target), "content": "new value"}),
        ToolCallComplete("read", "ReadFile", {"file_path": str(target)}),
    ]
    checker = PermissionChecker(DangerousCommandDetector(), PathSandbox(str(tmp_path)), RuleEngine())
    agent = Agent(
        MockLLMClient([[*calls, StreamEnd("tool_use")]]),
        create_default_registry(), "anthropic", work_dir=str(tmp_path),
        permission_checker=checker, max_iterations=1,
    )
    conv = ConversationManager()
    conv.add_user_message("Create and then read the file")
    events = []
    async for event in agent.run(conv):
        events.append(event)
        if isinstance(event, PermissionRequest):
            assert not target.exists()
            assert not any(isinstance(e, ToolResultEvent) for e in events)
            event.future.set_result(approval)

    assert len([e for e in events if isinstance(e, PermissionRequest)]) == 1
    results = [r for message in conv.history for r in message.tool_results]
    assert [r.tool_use_id for r in results] == ["write", "read"]
    assert [e.tool_id for e in events if isinstance(e, ToolResultEvent)] == ["write", "read"]
    if approval == PermissionResponse.ALLOW:
        assert target.read_text(encoding="utf-8") == "new value"
        assert "new value" in results[1].content
        assert not any(r.is_error for r in results)
    else:
        assert not target.exists()
        assert all(r.is_error for r in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("interactive", [True, False])
@pytest.mark.parametrize("barrier_category", ["write", "command"])
async def test_adjacent_reads_overlap_without_crossing_mutations(tmp_path, interactive, barrier_category):
    from pydantic import BaseModel
    from codeplus.tools import ToolRegistry
    from codeplus.tools.base import Tool, ToolResult

    class Params(BaseModel):
        label: str

    started = {label: asyncio.Event() for label in ("a", "b", "c", "d")}
    finished: set[str] = set()
    state = "old"

    class ReadState(Tool):
        name = "ReadState"
        params_model = Params
        description = "Read shared state"
        is_concurrency_safe = True

        async def execute(self, params):
            label = params.label
            started[label].set()
            peer = {"a": "b", "b": "a", "c": "d", "d": "c"}[label]
            await asyncio.wait_for(started[peer].wait(), timeout=2)
            # Event rendezvous proves actual overlap without wall-clock assertions.
            assert state == ("old" if label in ("a", "b") else "new")
            finished.add(label)
            return ToolResult(state)

    class ChangeState(Tool):
        name = "ChangeState"
        params_model = Params
        description = "Change shared state"
        category = barrier_category
        # Some stateful tools declare this flag; category must still form a barrier.
        is_concurrency_safe = True

        async def execute(self, params):
            nonlocal state
            assert finished == {"a", "b"}
            assert not started["c"].is_set() and not started["d"].is_set()
            await asyncio.sleep(0)
            state = "new"
            return ToolResult("changed")

    registry = ToolRegistry()
    registry.register(ReadState())
    registry.register(ChangeState())
    calls = [ToolCallComplete(label, "ChangeState" if label == "write" else "ReadState", {"label": label})
             for label in ("a", "b", "write", "c", "d")]
    agent = Agent(MockLLMClient([[*calls, StreamEnd("tool_use")]]), registry,
                  "anthropic", work_dir=str(tmp_path), max_iterations=1)
    conv = ConversationManager()
    conv.add_user_message("Read, change, then read again")
    if interactive:
        async for _ in agent.run(conv):
            pass
    else:
        await agent.run_to_completion("Read, change, then read again", conversation=conv)
    results = [r for message in conv.history for r in message.tool_results]
    assert [r.tool_use_id for r in results] == ["a", "b", "write", "c", "d"]
    assert [r.content for r in results] == ["old", "old", "changed", "new", "new"]
    assert not any(r.is_error for r in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("interactive", [True, False])
async def test_cancelling_read_batch_stops_all_tools(tmp_path, interactive):
    from pydantic import BaseModel
    from codeplus.tools import ToolRegistry
    from codeplus.tools.base import Tool, ToolResult

    class Params(BaseModel):
        label: str

    started = {name: asyncio.Event() for name in ("a", "b")}
    stopped: set[str] = set()
    writes = []

    class WaitRead(Tool):
        name = "WaitRead"
        params_model = Params
        description = "Wait until cancelled"
        is_concurrency_safe = True

        async def execute(self, params):
            started[params.label].set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.add(params.label)

    class WriteAfter(Tool):
        name = "WriteAfter"
        params_model = Params
        description = "Must not run after cancellation"
        category = "write"

        async def execute(self, params):
            writes.append(params.label)
            return ToolResult("written")

    registry = ToolRegistry()
    registry.register(WaitRead())
    registry.register(WriteAfter())
    client = MockLLMClient([[
        ToolCallComplete("a", "WaitRead", {"label": "a"}),
        ToolCallComplete("b", "WaitRead", {"label": "b"}),
        ToolCallComplete("write", "WriteAfter", {"label": "write"}),
        StreamEnd("tool_use"),
    ]])
    agent = Agent(client, registry, "anthropic", work_dir=str(tmp_path), max_iterations=1)
    conv = ConversationManager()
    conv.add_user_message("Start reads")

    async def run():
        if interactive:
            async for _ in agent.run(conv):
                pass
        else:
            await agent.run_to_completion("Start reads", conversation=conv)

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stopped == {"a", "b"}
    assert writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("interactive", [True, False])
async def test_read_batch_respects_permission_and_disabled_tools(tmp_path, interactive):
    from pydantic import BaseModel
    from codeplus.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, RuleEngine
    from codeplus.tools import ToolRegistry
    from codeplus.tools.base import Tool, ToolResult

    class Params(BaseModel):
        pass

    executed = []

    class Read(Tool):
        description = "Read"
        params_model = Params
        is_concurrency_safe = True

        def __init__(self, name):
            self.name = name

        async def execute(self, params):
            executed.append(self.name)
            return ToolResult(self.name)

    names = ["Protected", "Allowed", "Disabled", "Unknown", "Denied"]
    registry = ToolRegistry()
    for name in names:
        if name != "Unknown":
            registry.register(Read(name))
    registry.disable("Disabled")
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        '- rule: "Protected(*)"\n  effect: ask\n- rule: "Denied(*)"\n  effect: deny\n',
        encoding="utf-8",
    )
    checker = PermissionChecker(DangerousCommandDetector(), PathSandbox(str(tmp_path)),
                                RuleEngine(local_rules_path=rules_path))
    calls = [ToolCallComplete(name, name, {}) for name in names]
    agent = Agent(MockLLMClient([[*calls, StreamEnd("tool_use")]]), registry,
                  "anthropic", work_dir=str(tmp_path), max_iterations=1, permission_checker=checker)
    conv = ConversationManager()
    if interactive:
        async for event in agent.run(conv):
            if isinstance(event, PermissionRequest):
                assert executed == []
                event.future.set_result(PermissionResponse.DENY)
    else:
        await agent.run_to_completion("Read", conversation=conv)
    results = [r for message in conv.history for r in message.tool_results]
    assert [r.tool_use_id for r in results] == names
    assert [r.is_error for r in results] == [True, False, True, True, True]
    assert executed == ["Allowed"]
