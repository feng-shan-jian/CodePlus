"""Hook 系统的测试 —— 涵盖事件、条件、执行器、引擎、加载器以及与 agent 的集成。"""
from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from codeplus.hooks import (
    Action,
    ActionResult,
    Condition,
    ConditionGroup,
    ConditionParseError,
    Hook,
    HookConfigError,
    HookContext,
    HookEngine,
    LifecycleEvent,
    ToolRejectedError,
    load_hooks,
    parse_condition,
)

# ---------------------------------------------------------------------------
# LifecycleEvent
# ---------------------------------------------------------------------------

class TestLifecycleEvent:
    def test_has_15_events(self):
        assert len(LifecycleEvent) == 15

    def test_string_comparison(self):
        assert LifecycleEvent.SESSION_START == "session_start"
        assert LifecycleEvent.PRE_TOOL_USE == "pre_tool_use"
        assert LifecycleEvent.SHUTDOWN == "shutdown"

    def test_all_values(self):
        expected = {
            "session_start", "session_end",
            "turn_start", "turn_end",
            "pre_tool_use", "post_tool_use",
            "pre_send", "post_receive",
            "startup", "shutdown", "error", "compact",
            "permission_request", "file_change", "command_execute",
        }
        assert {e.value for e in LifecycleEvent} == expected

# ---------------------------------------------------------------------------
# HookContext
# ---------------------------------------------------------------------------

class TestHookContext:

    def test_get_field_tool(self):
        ctx = HookContext(tool_name="Bash")
        assert ctx.get_field("tool") == "Bash"

    def test_get_field_event(self):
        ctx = HookContext(event_name="pre_tool_use")
        assert ctx.get_field("event") == "pre_tool_use"

    def test_get_field_args(self):
        ctx = HookContext(tool_args={"command": "ls -la", "path": "/tmp"})
        assert ctx.get_field("args.command") == "ls -la"
        assert ctx.get_field("args.path") == "/tmp"

    def test_get_field_unknown(self):
        ctx = HookContext()
        assert ctx.get_field("nonexistent") == ""
        assert ctx.get_field("args.missing") == ""

    def test_expand_all_variables(self):
        ctx = HookContext(
            event_name="post_tool_use",
            tool_name="WriteFile",
            tool_args={"file_path": "src/main.py"},
            file_path="src/main.py",
            message="done",
            error="",
        )
        template = "Event=$EVENT Tool=$TOOL_NAME File=$FILE_PATH Msg=$MESSAGE Err=$ERROR Arg=$TOOL_ARGS.file_path"
        result = ctx.expand(template)
        assert "Event=post_tool_use" in result
        assert "Tool=WriteFile" in result
        assert "File=src/main.py" in result
        assert "Msg=done" in result
        assert "Err=" in result
        assert "Arg=src/main.py" in result

    def test_expand_undefined_variable(self):
        ctx = HookContext()
        assert ctx.expand("hello $UNKNOWN world") == "hello $UNKNOWN world"
        assert ctx.expand("$FILE_PATH") == ""

# ---------------------------------------------------------------------------
# 条件解析
# ---------------------------------------------------------------------------

class TestParseCondition:
    def test_single_condition(self):
        group = parse_condition('tool == "Bash"')
        assert group is not None
        assert len(group.conditions) == 1
        assert group.conditions[0].field == "tool"
        assert group.conditions[0].operator == "=="
        assert group.conditions[0].value == "Bash"
        assert group.logic == "and"

    def test_and_combination(self):
        group = parse_condition('tool == "Bash" && args.command =~ /rm/')
        assert group is not None
        assert len(group.conditions) == 2
        assert group.logic == "and"

    def test_or_combination(self):
        group = parse_condition('tool == "Bash" || tool == "WriteFile"')
        assert group is not None
        assert len(group.conditions) == 2
        assert group.logic == "or"

    def test_mixed_operators_error(self):
        with pytest.raises(ConditionParseError, match="Cannot mix"):
            parse_condition('tool == "Bash" && args.x == "1" || args.y == "2"')

    def test_empty_condition(self):
        assert parse_condition("") is None
        assert parse_condition("   ") is None

    def test_regex_format(self):
        group = parse_condition('args.command =~ /rm\\s+-rf/')
        assert group is not None
        c = group.conditions[0]
        assert c.operator == "=~"
        assert c.value == "/rm\\s+-rf/"

    def test_no_valid_operator(self):
        with pytest.raises(ConditionParseError, match="No valid operator"):
            parse_condition("tool Bash")

# ---------------------------------------------------------------------------
# 条件求值
# ---------------------------------------------------------------------------

class TestConditionEvaluate:
    def test_eq(self):
        ctx = HookContext(tool_name="Bash")
        c = Condition(field="tool", operator="==", value="Bash")
        assert c.evaluate(ctx) is True
        c2 = Condition(field="tool", operator="==", value="WriteFile")
        assert c2.evaluate(ctx) is False

    def test_neq(self):
        ctx = HookContext(tool_name="Bash")
        c = Condition(field="tool", operator="!=", value="ReadFile")
        assert c.evaluate(ctx) is True
        c2 = Condition(field="tool", operator="!=", value="Bash")
        assert c2.evaluate(ctx) is False

    def test_regex(self):
        ctx = HookContext(tool_args={"command": "rm  -rf /"})
        c = Condition(field="args.command", operator="=~", value="/rm\\s+-rf/")
        assert c.evaluate(ctx) is True

    def test_glob(self):
        ctx = HookContext(tool_args={"path": "src/main.py"})
        c = Condition(field="args.path", operator="~=", value="*.py")
        assert c.evaluate(ctx) is True
        c2 = Condition(field="args.path", operator="~=", value="*.go")
        assert c2.evaluate(ctx) is False

class TestConditionGroupEvaluate:
    def test_and_all_pass(self):
        ctx = HookContext(tool_name="WriteFile", tool_args={"path": "src/app.py"})
        group = ConditionGroup(
            conditions=[
                Condition("tool", "==", "WriteFile"),
                Condition("args.path", "~=", "*.py"),
            ],
            logic="and",
        )
        assert group.evaluate(ctx) is True

    def test_and_partial_fail(self):
        ctx = HookContext(tool_name="WriteFile", tool_args={"path": "src/app.go"})
        group = ConditionGroup(
            conditions=[
                Condition("tool", "==", "WriteFile"),
                Condition("args.path", "~=", "*.py"),
            ],
            logic="and",
        )
        assert group.evaluate(ctx) is False

    def test_or_any_pass(self):
        ctx = HookContext(tool_name="Bash")
        group = ConditionGroup(
            conditions=[
                Condition("tool", "==", "Bash"),
                Condition("tool", "==", "WriteFile"),
            ],
            logic="or",
        )
        assert group.evaluate(ctx) is True

    def test_or_all_fail(self):
        ctx = HookContext(tool_name="ReadFile")
        group = ConditionGroup(
            conditions=[
                Condition("tool", "==", "Bash"),
                Condition("tool", "==", "WriteFile"),
            ],
            logic="or",
        )
        assert group.evaluate(ctx) is False

    def test_empty_group(self):
        ctx = HookContext()
        group = ConditionGroup(conditions=[], logic="and")
        assert group.evaluate(ctx) is True

# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

class TestCommandExecutor:
    @pytest.mark.asyncio
    async def test_normal_execution(self):
        from codeplus.hooks.executors import execute_command

        action = Action(type="command", command="echo hello")
        ctx = HookContext()
        result = await execute_command(action, ctx)
        assert result.success is True
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_variable_substitution(self):
        from codeplus.hooks.executors import execute_command

        action = Action(type="command", command="echo $FILE_PATH")
        ctx = HookContext(file_path="src/main.py")
        result = await execute_command(action, ctx)
        assert "src/main.py" in result.output

    @pytest.mark.asyncio
    async def test_timeout(self):
        from codeplus.hooks.executors import execute_command

        cmd = "ping -n 11 127.0.0.1 >nul" if os.name == "nt" else "sleep 10"
        action = Action(type="command", command=cmd, timeout=1)
        ctx = HookContext()
        result = await execute_command(action, ctx)
        assert result.success is False
        assert "timed out" in result.output

class TestPromptExecutor:
    @pytest.mark.asyncio
    async def test_returns_message(self):
        from codeplus.hooks.executors import execute_prompt

        action = Action(type="prompt", message="Hello $TOOL_NAME")
        ctx = HookContext(tool_name="WriteFile")
        result = await execute_prompt(action, ctx)
        assert result.success is True
        assert result.output == "Hello WriteFile"

class TestHttpExecutor:
    @pytest.mark.asyncio
    async def test_mock_request(self):
        from codeplus.hooks.executors import execute_http

        action = Action(type="http", url="https://httpbin.org/post", body='{"test": true}')
        ctx = HookContext()
        # 用 mock 避免发起真实的网络请求
        with patch("codeplus.hooks.executors.urlopen") as mock_urlopen:
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"ok": true}'
            result = await execute_http(action, ctx)
            assert result.success is True
            assert "200" in result.output

class TestAgentExecutor:
    @pytest.mark.asyncio
    async def test_stub(self):
        from codeplus.hooks.executors import execute_agent

        action = Action(type="agent", prompt="Check $FILE_PATH")
        ctx = HookContext(file_path="test.py")
        result = await execute_agent(action, ctx)
        assert result.success is True
        assert "not yet implemented" in result.output

class TestExecuteAction:
    @pytest.mark.asyncio
    async def test_dispatch(self):
        from codeplus.hooks.executors import execute_action

        action = Action(type="command", command="echo dispatch_test")
        ctx = HookContext()
        result = await execute_action(action, ctx)
        assert "dispatch_test" in result.output

    @pytest.mark.asyncio
    async def test_unknown_type(self):
        from codeplus.hooks.executors import execute_action

        action = Action(type="unknown")
        ctx = HookContext()
        result = await execute_action(action, ctx)
        assert result.success is False

# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class TestLoadHooks:
    def test_full_config(self):
        raw = [
            {
                "id": "auto-format",
                "event": "post_tool_use",
                "if": 'tool == "WriteFile"',
                "action": {"type": "command", "command": "echo formatted"},
            }
        ]
        hooks = load_hooks(raw)
        assert len(hooks) == 1
        assert hooks[0].id == "auto-format"
        assert hooks[0].event == "post_tool_use"
        assert hooks[0].condition is not None

    def test_auto_id(self):
        raw = [
            {"event": "session_start", "action": {"type": "prompt", "message": "hello"}}
        ]
        hooks = load_hooks(raw)
        assert hooks[0].id == "session_start_0"

    def test_empty(self):
        assert load_hooks(None) == []
        assert load_hooks([]) == []

    def test_invalid_event(self):
        with pytest.raises(HookConfigError, match="invalid event"):
            load_hooks([{"event": "bad_event", "action": {"type": "command", "command": "x"}}])

    def test_invalid_action_type(self):
        with pytest.raises(HookConfigError, match="invalid action type"):
            load_hooks([{"event": "startup", "action": {"type": "bad"}}])

    def test_reject_on_non_pre_tool_use(self):
        with pytest.raises(HookConfigError, match="reject.*pre_tool_use"):
            load_hooks([{
                "event": "post_tool_use",
                "action": {"type": "command", "command": "x"},
                "reject": True,
            }])

    def test_async_on_pre_tool_use(self):
        with pytest.raises(HookConfigError, match="async.*pre_tool_use"):
            load_hooks([{
                "event": "pre_tool_use",
                "action": {"type": "command", "command": "x"},
                "async": True,
            }])

    def test_missing_required_field(self):
        with pytest.raises(HookConfigError, match="requires.*command"):
            load_hooks([{"event": "startup", "action": {"type": "command"}}])

        with pytest.raises(HookConfigError, match="requires.*url"):
            load_hooks([{"event": "startup", "action": {"type": "http"}}])

        with pytest.raises(HookConfigError, match="requires.*message"):
            load_hooks([{"event": "startup", "action": {"type": "prompt"}}])

        with pytest.raises(HookConfigError, match="requires.*prompt"):
            load_hooks([{"event": "startup", "action": {"type": "agent"}}])

# ---------------------------------------------------------------------------
# HookEngine
# ---------------------------------------------------------------------------

class TestHookEngine:

    def _make_hook(self, **kwargs) -> Hook:
        defaults = {
            "id": "test",
            "event": "post_tool_use",
            "action": Action(type="command", command="echo test"),
        }
        defaults.update(kwargs)
        return Hook(**defaults)

    def test_find_matching_hooks(self):
        h1 = self._make_hook(id="h1", event="post_tool_use")
        h2 = self._make_hook(id="h2", event="pre_tool_use")
        engine = HookEngine([h1, h2])
        ctx = HookContext(event_name="post_tool_use")
        matched = engine.find_matching_hooks("post_tool_use", ctx)
        assert len(matched) == 1
        assert matched[0].id == "h1"

    def test_find_with_condition_filter(self):
        h = self._make_hook(
            id="h1",
            event="post_tool_use",
            condition=ConditionGroup(
                conditions=[Condition("tool", "==", "WriteFile")],
                logic="and",
            ),
        )
        engine = HookEngine([h])

        ctx_match = HookContext(event_name="post_tool_use", tool_name="WriteFile")
        assert len(engine.find_matching_hooks("post_tool_use", ctx_match)) == 1

        ctx_no_match = HookContext(event_name="post_tool_use", tool_name="Bash")
        assert len(engine.find_matching_hooks("post_tool_use", ctx_no_match)) == 0

    def test_once_filter(self):
        h = self._make_hook(id="h1", once=True)
        engine = HookEngine([h])
        ctx = HookContext(event_name="post_tool_use")

        assert len(engine.find_matching_hooks("post_tool_use", ctx)) == 1
        h.mark_executed()
        assert len(engine.find_matching_hooks("post_tool_use", ctx)) == 0

    @pytest.mark.asyncio
    async def test_run_pre_tool_hooks_reject(self):
        h = self._make_hook(
            id="block-vendor",
            event="pre_tool_use",
            action=Action(type="command", command="echo rejected"),
            reject=True,
        )
        engine = HookEngine([h])
        ctx = HookContext(event_name="pre_tool_use", tool_name="WriteFile")
        result = await engine.run_pre_tool_hooks(ctx)
        assert result is not None
        assert isinstance(result, ToolRejectedError)
        assert "rejected" in result.reason

    @pytest.mark.asyncio
    async def test_run_pre_tool_hooks_no_reject(self):
        h = self._make_hook(
            id="log-only",
            event="pre_tool_use",
            action=Action(type="command", command="echo ok"),
            reject=False,
        )
        engine = HookEngine([h])
        ctx = HookContext(event_name="pre_tool_use", tool_name="WriteFile")
        result = await engine.run_pre_tool_hooks(ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_prompt_message_collection(self):
        h = self._make_hook(
            id="inject",
            event="session_start",
            action=Action(type="prompt", message="Project info here"),
        )
        engine = HookEngine([h])
        ctx = HookContext(event_name="session_start")
        await engine.run_hooks("session_start", ctx)
        messages = engine.get_prompt_messages()
        assert len(messages) == 1
        assert "Project info" in messages[0]
        assert engine.get_prompt_messages() == []

    @pytest.mark.asyncio
    async def test_error_does_not_raise(self):
        h = self._make_hook(
            id="bad",
            event="post_tool_use",
            action=Action(type="command", command="exit 1"),
        )
        engine = HookEngine([h])
        ctx = HookContext(event_name="post_tool_use")
        await engine.run_hooks("post_tool_use", ctx)

    @pytest.mark.asyncio
    async def test_async_hook_does_not_block(self):
        # 使用短命令替代 sleep 5，避免孤立子进程导致 pytest 退出时挂起
        h = self._make_hook(
            id="slow",
            event="post_tool_use",
            action=Action(type="command", command="echo async_done"),
            async_exec=True,
        )
        engine = HookEngine([h])
        ctx = HookContext(event_name="post_tool_use")
        await engine.run_hooks("post_tool_use", ctx)
        # 给异步任务一点时间完成
        await asyncio.sleep(0.1)

# ---------------------------------------------------------------------------
# Agent 循环集成
# ---------------------------------------------------------------------------

class TestAgentHookIntegration:
    """验证 pre_tool_use 拒绝会导致工具调用被跳过。"""

    @pytest.mark.asyncio
    async def test_pre_tool_use_reject_skips_tool(self):
        # vibe coding：用安全命令和执行替身核验拒绝，所有平台都必须验证工具未执行。
        from codeplus.agent import Agent, ToolResultEvent
        from codeplus.client import LLMClient
        from codeplus.conversation import ConversationManager
        from codeplus.tools import create_default_registry
        from codeplus.tools.base import StreamEnd, TextDelta, ToolCallComplete, ToolResult

        class MockClient(LLMClient):
            def __init__(self):
                self._call = 0

            async def stream(self, conversation, system="", tools=None):
                self._call += 1
                if self._call == 1:
                    yield ToolCallComplete(
                        tool_id="t1",
                        tool_name="Bash",
                        arguments={"command": "echo hook_probe"},
                    )
                    yield StreamEnd(stop_reason="tool_use", input_tokens=10, output_tokens=5)
                else:
                    yield TextDelta(text="I understand, I won't do that.")
                    yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5)

        hook = Hook(
            id="block-probe",
            event="pre_tool_use",
            action=Action(type="prompt", message="command blocked"),
            condition=parse_condition('tool == "Bash" && args.command =~ /echo\\s+hook_probe/'),
            reject=True,
        )
        engine = HookEngine([hook])

        client = MockClient()
        registry = create_default_registry()
        conv = ConversationManager()
        conv.add_user_message("run the probe")

        agent = Agent(
            client=client,
            registry=registry,
            protocol="anthropic",
            hook_engine=engine,
        )

        events = []
        execute = AsyncMock(return_value=ToolResult("probe executed"))
        with patch.object(registry.get("Bash"), "execute", execute):
            async for event in agent.run(conv):
                events.append(event)

        tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(tool_results) >= 1
        rejected = tool_results[0]
        assert rejected.is_error is True
        assert "Hook rejected" in rejected.output
        execute.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("interactive", [True, False])
    @pytest.mark.parametrize("concurrent", [False, True])
    @pytest.mark.parametrize("outcome", ["reject", "allow", "execution_error", "validation_error"])
    async def test_tool_hook_lifecycle(self, tmp_path, interactive, concurrent, outcome):
        """vibe coding：串行、并发和非交互路径都遵守前置拒绝与后置通知契约。"""
        from pydantic import BaseModel
        from codeplus.agent import Agent, HookEvent, ToolResultEvent
        from codeplus.client import LLMClient
        from codeplus.conversation import ConversationManager
        from codeplus.tools import ToolRegistry
        from codeplus.tools.base import StreamEnd, Tool, ToolCallComplete, ToolResult

        class Params(BaseModel):
            file_path: str

        executed = []

        class Probe(Tool):
            name = "Probe"
            description = "Safe lifecycle probe"
            params_model = Params
            category = "read" if concurrent else "write"
            is_concurrency_safe = concurrent

            async def execute(self, params):
                """记录实际执行，模拟成功或工具内部异常。"""
                executed.append(params.file_path)
                if outcome == "execution_error":
                    raise RuntimeError("probe failed")
                return ToolResult("probe executed")

        paths = [42 if outcome == "validation_error" else "blocked.txt", "allowed.txt"]

        class MockClient(LLMClient):
            async def stream(self, conversation, system="", tools=None):
                """单轮发出两次调用，验证条件匹配不会误拒绝其他调用。"""
                for i, path in enumerate(paths):
                    yield ToolCallComplete(f"t{i}", "Probe", {"file_path": path})
                yield StreamEnd("tool_use")

        engine = HookEngine([
            Hook(id="pre", event="pre_tool_use", action=Action(type="prompt", message="before $FILE_PATH")),
            Hook(id="reject", event="pre_tool_use", action=Action(type="prompt", message="blocked"),
                 condition=parse_condition('args.file_path == "blocked.txt"'), reject=outcome == "reject"),
            Hook(id="post", event="post_tool_use", action=Action(type="prompt", message="after $FILE_PATH")),
        ])
        registry = ToolRegistry()
        registry.register(Probe())
        agent = Agent(MockClient(), registry, "anthropic", work_dir=str(tmp_path),
                      hook_engine=engine, max_iterations=1)
        conv = ConversationManager()
        conv.add_user_message("probe hooks")
        events = []
        if interactive:
            async for event in agent.run(conv):
                events.append(event)
        else:
            await agent.run_to_completion("", conversation=conv)

        results = [r for message in conv.history for r in message.tool_results]
        assert [r.tool_use_id for r in results] == ["t0", "t1"]
        assert [r.is_error for r in results] == [outcome != "allow", outcome == "execution_error"]
        if outcome == "reject":
            assert "Hook rejected: blocked" in results[0].content
        assert executed == (["allowed.txt"] if outcome in ("reject", "validation_error") else paths)
        notes = [e for e in events if isinstance(e, HookEvent)] if interactive else engine.drain_notifications()
        assert [n.output for n in notes if n.event == "pre_tool_use" and n.hook_id == "pre"] == [
            f"before {path}" for path in paths
        ]
        assert sorted(n.output for n in notes if n.event == "post_tool_use") == sorted(
            f"after {path}" for path in (paths[1:] if outcome == "reject" else paths)
        )
        if interactive:
            assert [e.tool_id for e in events if isinstance(e, ToolResultEvent)] == ["t0", "t1"]
