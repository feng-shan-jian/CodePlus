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
            ToolCallComplete("t1", "ReadFile", {"file_path": "LICENSE"}),
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
    conv.add_user_message("Read LICENSE")

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
            ToolCallComplete(f"t{i}", "ReadFile", {"file_path": "LICENSE"}),
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
            yield ToolCallComplete(f"t{self._call_count}", "ReadFile", {"file_path": "LICENSE"})
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














