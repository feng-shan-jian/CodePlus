"""Hook 系统的测试 —— 涵盖事件、条件、执行器、引擎、加载器以及与 agent 的集成。"""
from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator
from unittest.mock import patch

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
    pass








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









# ---------------------------------------------------------------------------
# Agent 循环集成
# ---------------------------------------------------------------------------

class TestAgentHookIntegration:
    """验证 pre_tool_use 拒绝会导致工具调用被跳过。"""

