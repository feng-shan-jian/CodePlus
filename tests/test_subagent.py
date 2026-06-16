"""SubAgent 系统的测试（第 12 章）。"""

from __future__ import annotations

import asyncio
import textwrap
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeplus.agents.parser import AgentDef, AgentParseError, parse_agent_file, parse_frontmatter
from codeplus.agents.loader import AgentLoader
from codeplus.agents.tool_filter import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    resolve_agent_tools,
)
from codeplus.agents.fork import (
    FORK_BOILERPLATE_TAG,
    ForkError,
    build_forked_messages,
)
from codeplus.agents.trace import TraceManager, TraceNode
from codeplus.agents.task_manager import BackgroundTask, TaskManager
from codeplus.agents.notification import format_task_notification, inject_task_notifications
from codeplus.conversation import ConversationManager, Message, ToolResultBlock, ToolUseBlock
from codeplus.tools import ToolRegistry
from codeplus.tools.base import Tool, ToolResult

# =====================================================================
# 辅助函数
# =====================================================================

class DummyTool(Tool):
    params_model = MagicMock

    def __init__(self, name: str, category: str = "read"):
        self.name = name
        self.description = f"Dummy {name}"
        self.category = category
        self.is_concurrency_safe = True

    def get_schema(self):
        return {"name": self.name, "description": self.description, "input_schema": {}}

    async def execute(self, params):
        return ToolResult(output=f"{self.name} executed")

def make_registry(*tool_names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for name in tool_names:
        reg.register(DummyTool(name))
    return reg

def make_agent_md(
    name: str = "test-agent",
    description: str = "A test agent",
    body: str = "You are a test agent.",
    **extra_fields: str,
) -> str:
    lines = [f"name: {name}", f"description: {description}"]
    for k, v in extra_fields.items():
        lines.append(f"{k}: {v}")
    frontmatter = "\n".join(lines)
    return f"---\n{frontmatter}\n---\n\n{body}"

# =====================================================================
# 1. Agent 定义解析
# =====================================================================

class TestAgentParser:
    def test_parse_valid_agent(self, tmp_path: Path):
        md = make_agent_md(
            name="security-reviewer",
            description="Security review agent",
            model="haiku",
            maxTurns="20",
        )
        f = tmp_path / "security-reviewer.md"
        f.write_text(md)
        agent_def = parse_agent_file(f)
        assert agent_def.agent_type == "security-reviewer"
        assert agent_def.when_to_use == "Security review agent"
        assert agent_def.model == "haiku"
        assert agent_def.max_turns == 20
        assert agent_def.system_prompt == "You are a test agent."

    def test_parse_missing_name(self, tmp_path: Path):
        f = tmp_path / "bad.md"
        f.write_text("---\ndescription: test\n---\nbody")
        with pytest.raises(AgentParseError, match="name"):
            parse_agent_file(f)

    def test_parse_missing_description(self, tmp_path: Path):
        f = tmp_path / "bad.md"
        f.write_text("---\nname: test\n---\nbody")
        with pytest.raises(AgentParseError, match="description"):
            parse_agent_file(f)

    def test_parse_any_model_accepted(self, tmp_path: Path):
        # 不限制 model 白名单，第三方模型名称由宿主 ModelResolver 校验
        f = tmp_path / "any_model.md"
        f.write_text("---\nname: t\ndescription: t\nmodel: gpt-4\n---\nbody")
        agent_def = parse_agent_file(f)
        assert agent_def.model == "gpt-4"

    def test_parse_invalid_permission_mode(self, tmp_path: Path):
        f = tmp_path / "bad.md"
        f.write_text("---\nname: t\ndescription: t\npermissionMode: yolo\n---\nbody")
        with pytest.raises(AgentParseError, match="permissionMode"):
            parse_agent_file(f)

    def test_parse_invalid_max_turns(self, tmp_path: Path):
        f = tmp_path / "bad.md"
        f.write_text("---\nname: t\ndescription: t\nmaxTurns: -5\n---\nbody")
        with pytest.raises(AgentParseError, match="maxTurns"):
            parse_agent_file(f)

    def test_parse_bad_yaml(self, tmp_path: Path):
        f = tmp_path / "bad.md"
        f.write_text("---\n: :\n---\nbody")
        with pytest.raises(AgentParseError):
            parse_agent_file(f)

    def test_parse_no_frontmatter(self, tmp_path: Path):
        f = tmp_path / "bad.md"
        f.write_text("just text no frontmatter")
        with pytest.raises(AgentParseError, match="frontmatter"):
            parse_agent_file(f)

    def test_parse_disallowed_tools(self, tmp_path: Path):
        md = textwrap.dedent("""\
        ---
        name: reader
        description: Read-only agent
        disallowedTools:
          - EditFile
          - WriteFile
          - Bash
        ---
        Read only.
        """)
        f = tmp_path / "reader.md"
        f.write_text(md)
        agent_def = parse_agent_file(f)
        assert agent_def.disallowed_tools == ["EditFile", "WriteFile", "Bash"]

    def test_parse_default_values(self, tmp_path: Path):
        md = make_agent_md()
        f = tmp_path / "default.md"
        f.write_text(md)
        agent_def = parse_agent_file(f)
        assert agent_def.model == "inherit"
        assert agent_def.max_turns == 200  # 未指定时的默认值
        assert agent_def.permission_mode == "default"
        assert agent_def.background is False
        assert agent_def.tools == []
        assert agent_def.disallowed_tools == []

    def test_frontmatter_parse(self):
        raw = "---\nname: x\ndescription: y\n---\nbody text"
        meta, body = parse_frontmatter(raw)
        assert meta == {"name": "x", "description": "y"}
        assert body == "body text"

    def test_valid_permission_modes(self, tmp_path: Path):
        for mode in ("default", "acceptEdits", "bypassPermissions"):
            f = tmp_path / f"{mode}.md"
            f.write_text(f"---\nname: t\ndescription: t\npermissionMode: {mode}\n---\nbody")
            agent_def = parse_agent_file(f)
            assert agent_def.permission_mode == mode

    def test_valid_models(self, tmp_path: Path):
        for model in ("inherit", "haiku", "sonnet", "opus"):
            f = tmp_path / f"{model}.md"
            f.write_text(f"---\nname: t\ndescription: t\nmodel: {model}\n---\nbody")
            agent_def = parse_agent_file(f)
            assert agent_def.model == model

# =====================================================================
# 2. Agent 加载器
# =====================================================================

class TestAgentLoader:
    def test_load_builtins(self, tmp_path: Path):
        loader = AgentLoader(str(tmp_path))
        agents = loader.load_all()
        assert "Explore" in agents
        assert "Plan" in agents
        assert "general-purpose" in agents
        assert agents["Explore"].model == "haiku"
        assert agents["Explore"].max_turns == 200  # 未指定时的默认值

    def test_verification_disabled_by_default(self, tmp_path: Path):
        loader = AgentLoader(str(tmp_path), enable_verification=False)
        agents = loader.load_all()
        assert "Verification" not in agents

    def test_verification_enabled(self, tmp_path: Path):
        loader = AgentLoader(str(tmp_path), enable_verification=True)
        agents = loader.load_all()
        assert "Verification" in agents

    def test_project_overrides_builtin(self, tmp_path: Path):
        agents_dir = tmp_path / ".codeplus" / "agents"
        agents_dir.mkdir(parents=True)
        custom_md = make_agent_md(
            name="Explore",
            description="Custom Explore",
            body="Custom system prompt.",
        )
        (agents_dir / "explore.md").write_text(custom_md)

        loader = AgentLoader(str(tmp_path))
        agents = loader.load_all()
        assert agents["Explore"].when_to_use == "Custom Explore"
        assert agents["Explore"].source == "project"

    def test_get_returns_agent(self, tmp_path: Path):
        loader = AgentLoader(str(tmp_path))
        loader.load_all()
        explore = loader.get("Explore")
        assert explore is not None
        assert explore.agent_type == "Explore"

    def test_get_unknown_returns_none(self, tmp_path: Path):
        loader = AgentLoader(str(tmp_path))
        loader.load_all()
        assert loader.get("nonexistent") is None

    def test_list_agents(self, tmp_path: Path):
        loader = AgentLoader(str(tmp_path))
        loader.load_all()
        agent_list = loader.list_agents()
        names = [name for name, _ in agent_list]
        assert "Explore" in names
        assert "Plan" in names
        assert "general-purpose" in names

    def test_hot_reload(self, tmp_path: Path):
        agents_dir = tmp_path / ".codeplus" / "agents"
        agents_dir.mkdir(parents=True)
        f = agents_dir / "custom.md"
        f.write_text(make_agent_md(name="custom", description="v1"))

        loader = AgentLoader(str(tmp_path))
        loader.load_all()
        assert loader.get("custom").when_to_use == "v1"

        f.write_text(make_agent_md(name="custom", description="v2"))
        assert loader.get("custom").when_to_use == "v2"

    def test_bad_file_skipped(self, tmp_path: Path):
        agents_dir = tmp_path / ".codeplus" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "bad.md").write_text("no frontmatter")
        (agents_dir / "good.md").write_text(
            make_agent_md(name="good", description="ok")
        )

        loader = AgentLoader(str(tmp_path))
        agents = loader.load_all()
        assert "good" in agents
        assert "bad" not in agents

# =====================================================================
# 3. 工具过滤
# =====================================================================

class TestToolFilter:

    def test_global_disallowed(self):
        reg = make_registry("ReadFile", "Agent", "Bash", "AskUserQuestion")
        definition = AgentDef(
            agent_type="test", when_to_use="test", source="builtin"
        )
        filtered = resolve_agent_tools(reg, definition)
        names = {t.name for t in filtered.list_tools()}
        assert "Agent" not in names
        assert "AskUserQuestion" not in names
        assert "ReadFile" in names
        assert "Bash" in names

    def test_disallowed_tools_in_definition(self):
        reg = make_registry("ReadFile", "EditFile", "WriteFile", "Bash", "Grep")
        definition = AgentDef(
            agent_type="test",
            when_to_use="test",
            disallowed_tools=["EditFile", "WriteFile", "Bash"],
            source="builtin",
        )
        filtered = resolve_agent_tools(reg, definition)
        names = {t.name for t in filtered.list_tools()}
        assert names == {"ReadFile", "Grep"}

    def test_tools_whitelist(self):
        reg = make_registry("ReadFile", "EditFile", "WriteFile", "Bash", "Grep")
        definition = AgentDef(
            agent_type="test",
            when_to_use="test",
            tools=["ReadFile", "Grep"],
            source="builtin",
        )
        filtered = resolve_agent_tools(reg, definition)
        names = {t.name for t in filtered.list_tools()}
        assert names == {"ReadFile", "Grep"}

    def test_background_whitelist(self):
        reg = make_registry("ReadFile", "EditFile", "WriteFile", "Bash", "Grep", "Agent", "SomeOtherTool")
        definition = AgentDef(
            agent_type="test", when_to_use="test", source="builtin"
        )
        filtered = resolve_agent_tools(reg, definition, is_background=True)
        names = {t.name for t in filtered.list_tools()}
        assert "Agent" not in names
        assert "SomeOtherTool" not in names
        for name in names:
            assert name in ASYNC_AGENT_ALLOWED_TOOLS

    def test_combined_whitelist_and_blacklist(self):
        reg = make_registry("ReadFile", "EditFile", "WriteFile", "Bash", "Grep")
        definition = AgentDef(
            agent_type="test",
            when_to_use="test",
            tools=["ReadFile", "EditFile", "Grep"],
            disallowed_tools=["EditFile"],
            source="builtin",
        )
        filtered = resolve_agent_tools(reg, definition)
        names = {t.name for t in filtered.list_tools()}
        assert names == {"ReadFile", "Grep"}



# =====================================================================
# 4. Fork 模式
# =====================================================================

class TestForkMode:
    pass





# =====================================================================
# 5. Trace 管理器
# =====================================================================

class TestTraceManager:
    pass









# =====================================================================
# 6. 任务管理器
# =====================================================================

class TestTaskManager:
    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent.total_input_tokens = 100
        agent.total_output_tokens = 50
        agent.run_to_completion = AsyncMock(return_value="task done")
        # 普通（非团队）subagent：team_name 为空，否则 _run_background 会进入
        # 团队空闲循环（每秒一轮、最多 60 次），后台任务永远走不到 finally 的
        # notify_queue.put，poll_completed 便收不到完成通知。
        agent.team_name = ""
        agent._team_manager = None
        return agent







# =====================================================================
# 7. 通知
# =====================================================================

class TestNotification:
    pass



# =====================================================================
# 8. 配置
# =====================================================================

class TestConfig:
    pass


# =====================================================================
# 9. 权限模式
# =====================================================================

class TestPermissionMode:
    pass

# =====================================================================
# 10. AgentTool 参数
# =====================================================================

class TestAgentToolParams:
    pass


# =====================================================================
# 11. Agent（run_to_completion 基础功能、agent_id、trace_id）
# =====================================================================

class TestAgentExtensions:
    pass



