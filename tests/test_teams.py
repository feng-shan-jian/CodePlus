"""Agent Team（智能体团队）系统的测试（第 14 章）。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeplus.teams.models import (
    AgentTeam,
    BackendType,
    TeammateInfo,
    resolve_team_dir,
    unique_team_name,
)
from codeplus.teams.shared_task import SharedTask, SharedTaskStore
from codeplus.teams.mailbox import Mailbox, MailboxMessage, create_message
from codeplus.teams.registry import AgentNameRegistry
from codeplus.teams.backend_detect import (
    BackendDetectionError,
    detect_backend,
    detect_backend_from_env,
    detect_pane_backend,
)
from codeplus.teams.coordinator import (
    get_coordinator_system_prompt,
    get_coordinator_user_context,
)
from codeplus.agents.tool_filter import (
    COORDINATOR_MODE_ALLOWED_TOOLS,
    IN_PROCESS_TEAMMATE_ALLOWED_TOOLS,
    TEAMMATE_COORDINATION_TOOLS,
    build_teammate_tools,
    apply_coordinator_filter,
)
from codeplus.tools import ToolRegistry
from codeplus.tools.base import Tool, ToolResult

# =====================================================================
# 辅助工具
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

@pytest.fixture(autouse=True)
def _reset_registry():
    AgentNameRegistry.reset()
    yield
    AgentNameRegistry.reset()

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)

# =====================================================================
# 1. AgentTeam / TeammateInfo
# =====================================================================

class TestModels:
    def test_teammate_info_roundtrip(self):
        info = TeammateInfo(
            name="alice",
            agent_id="abc123",
            agent_type="worker",
            model="sonnet",
            worktree_path="/tmp/wt",
            backend_type="tmux",
            is_active=True,
        )
        d = info.to_dict()
        restored = TeammateInfo.from_dict(d)
        assert restored.name == "alice"
        assert restored.agent_id == "abc123"
        assert restored.is_active is True

    def test_agent_team_save_load(self, tmp_dir):
        config_path = str(Path(tmp_dir) / "team" / "config.json")
        team = AgentTeam(
            name="test-team",
            lead_agent_id="lead-001",
            config_path=config_path,
            description="Test team",
        )
        team.add_member(TeammateInfo(
            name="alice", agent_id="a1", agent_type="worker",
            model="sonnet", worktree_path="/tmp/wt1", backend_type="tmux",
        ))
        team.save()

        loaded = AgentTeam.load(config_path)
        assert loaded.name == "test-team"
        assert loaded.lead_agent_id == "lead-001"
        assert len(loaded.members) == 1
        assert loaded.members[0].name == "alice"

    def test_get_member(self):
        team = AgentTeam(name="t", lead_agent_id="l")
        team.add_member(TeammateInfo(
            name="bob", agent_id="b1", agent_type="w",
            model="", worktree_path="", backend_type="in-process",
        ))
        assert team.get_member("bob") is not None
        assert team.get_member("b1") is not None
        assert team.get_member("nonexistent") is None

    def test_remove_member(self):
        team = AgentTeam(name="t", lead_agent_id="l")
        team.add_member(TeammateInfo(
            name="bob", agent_id="b1", agent_type="w",
            model="", worktree_path="", backend_type="in-process",
        ))
        assert team.remove_member("bob") is True
        assert len(team.members) == 0
        assert team.remove_member("bob") is False

    def test_set_member_active(self):
        team = AgentTeam(name="t", lead_agent_id="l")
        team.add_member(TeammateInfo(
            name="alice", agent_id="a1", agent_type="w",
            model="", worktree_path="", backend_type="in-process",
            is_active=True,
        ))
        team.set_member_active("alice", False)
        assert team.members[0].is_active is False
        assert team.all_idle() is True

    def test_all_idle(self):
        team = AgentTeam(name="t", lead_agent_id="l")
        team.add_member(TeammateInfo(
            name="alice", agent_id="a1", agent_type="w",
            model="", worktree_path="", backend_type="in-process",
            is_active=False,
        ))
        team.add_member(TeammateInfo(
            name="bob", agent_id="b1", agent_type="w",
            model="", worktree_path="", backend_type="in-process",
            is_active=True,
        ))
        assert team.all_idle() is False

    def test_unique_team_name(self, tmp_dir):
        with patch("codeplus.teams.models.Path.home", return_value=Path(tmp_dir)):
            name1 = unique_team_name("my-team")
            assert name1 == "my-team"
            (Path(tmp_dir) / ".codeplus" / "teams" / "my-team").mkdir(parents=True)
            name2 = unique_team_name("my-team")
            assert name2 == "my-team-2"

# =====================================================================
# 2. SharedTaskStore
# =====================================================================

class TestSharedTaskStore:
    def test_create_and_get(self, tmp_dir):
        store = SharedTaskStore(Path(tmp_dir) / "tasks.json")
        store.init_empty()
        task = store.create(title="Do something", description="Details", assignee="alice")
        assert task.id == "1"
        assert task.title == "Do something"

        fetched = store.get("1")
        assert fetched is not None
        assert fetched.assignee == "alice"

    def test_auto_increment_id(self, tmp_dir):
        store = SharedTaskStore(Path(tmp_dir) / "tasks.json")
        store.init_empty()
        t1 = store.create(title="First")
        t2 = store.create(title="Second")
        assert t1.id == "1"
        assert t2.id == "2"

    def test_list_with_filters(self, tmp_dir):
        store = SharedTaskStore(Path(tmp_dir) / "tasks.json")
        store.init_empty()
        store.create(title="A", assignee="alice")
        t2 = store.create(title="B", assignee="bob")
        store.update(t2.id, status="in_progress")

        all_tasks = store.list_tasks()
        assert len(all_tasks) == 2

        pending = store.list_tasks(status="pending")
        assert len(pending) == 1
        assert pending[0].title == "A"

        bobs = store.list_tasks(assignee="bob")
        assert len(bobs) == 1

    def test_update_with_dependencies(self, tmp_dir):
        store = SharedTaskStore(Path(tmp_dir) / "tasks.json")
        store.init_empty()
        store.create(title="Task A")
        store.create(title="Task B")

        updated = store.update("2", add_blocked_by=["1"])
        assert updated is not None
        assert "1" in updated.blocked_by

        updated = store.update("1", add_blocks=["2"])
        assert "2" in updated.blocks

    def test_update_nonexistent_returns_none(self, tmp_dir):
        store = SharedTaskStore(Path(tmp_dir) / "tasks.json")
        store.init_empty()
        assert store.update("999") is None

    def test_persistence(self, tmp_dir):
        path = Path(tmp_dir) / "tasks.json"
        store1 = SharedTaskStore(path)
        store1.init_empty()
        store1.create(title="Persisted task")

        store2 = SharedTaskStore(path)
        tasks = store2.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "Persisted task"

# =====================================================================
# 3. Mailbox
# =====================================================================

class TestMailbox:
    def test_write_and_consume(self, tmp_dir):
        mailbox = Mailbox(tmp_dir)
        msg = create_message("alice", "Hello bob")
        mailbox.write("bob-agent-id", msg)

        messages = mailbox.consume("bob-agent-id")
        assert len(messages) == 1
        assert messages[0].text == "Hello bob"
        assert messages[0].from_agent == "alice"

        # 已被消费 —— 此时应该为空
        messages2 = mailbox.consume("bob-agent-id")
        assert len(messages2) == 0

    def test_read_without_consume(self, tmp_dir):
        mailbox = Mailbox(tmp_dir)
        msg = create_message("alice", "Peek")
        mailbox.write("bob-id", msg)

        messages = mailbox.read("bob-id")
        assert len(messages) == 1

        # 仍然存在
        messages2 = mailbox.read("bob-id")
        assert len(messages2) == 1

    def test_broadcast(self, tmp_dir):
        mailbox = Mailbox(tmp_dir)
        msg = create_message("alice", "Team update")
        mailbox.broadcast(["bob-id", "charlie-id", "alice-id"], msg, exclude="alice-id")

        bob_msgs = mailbox.consume("bob-id")
        charlie_msgs = mailbox.consume("charlie-id")
        alice_msgs = mailbox.consume("alice-id")

        assert len(bob_msgs) == 1
        assert len(charlie_msgs) == 1
        assert len(alice_msgs) == 0

    def test_cleanup(self, tmp_dir):
        mailbox = Mailbox(tmp_dir)
        msg = create_message("a", "test")
        mailbox.write("agent-1", msg)
        mailbox.cleanup("agent-1")
        assert len(mailbox.read("agent-1")) == 0

    def test_empty_mailbox(self, tmp_dir):
        mailbox = Mailbox(tmp_dir)
        assert mailbox.consume("nonexistent") == []
        assert mailbox.read("nonexistent") == []

    def test_concurrent_writes_keep_every_message(self, tmp_dir):
        """并发写同一个收件箱时不能丢消息，写失败也必须抛出来而不是静默吞掉。"""
        import threading

        mailbox = Mailbox(tmp_dir)
        n = 20
        errors: list[Exception] = []

        def send(i: int) -> None:
            try:
                mailbox.write("dest", create_message("sender", f"msg-{i}"))
            except Exception as e:  # noqa: BLE001 — 收集起来在主线程断言
                errors.append(e)

        threads = [threading.Thread(target=send, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(mailbox.read("dest")) == n

# =====================================================================
# 4. AgentNameRegistry
# =====================================================================

class TestAgentNameRegistry:

    def test_register_and_resolve(self):
        reg = AgentNameRegistry.instance()
        reg.register("alice", "agent-abc")
        assert reg.resolve("alice") == "agent-abc"
        assert reg.resolve("agent-abc") == "agent-abc"  # 直接按 ID 查找
        assert reg.resolve("nonexistent") is None

    def test_unregister(self):
        reg = AgentNameRegistry.instance()
        reg.register("bob", "agent-xyz")
        reg.unregister("bob")
        assert reg.resolve("bob") is None

    def test_singleton(self):
        r1 = AgentNameRegistry.instance()
        r2 = AgentNameRegistry.instance()
        assert r1 is r2

# =====================================================================
# 5. Backend Detection（后端探测）
# =====================================================================

class TestBackendDetect:
    def _clear_env(self) -> dict[str, str]:
        # 清掉 tmux / iTerm2 相关环境变量，构造“不在任何会话里”的干净环境
        env = {k: v for k, v in os.environ.items()}
        env.pop("TMUX", None)
        env.pop("ITERM_SESSION_ID", None)
        return env

    def test_in_process_mode(self):
        # 显式要求 in-process 时恒回退进程内
        result = detect_backend(teammate_mode="in-process")
        assert result == BackendType.IN_PROCESS











# =====================================================================
# 6. Tool Filtering（工具过滤）
# =====================================================================

class TestToolFilter:
    pass





# =====================================================================
# 7. Coordinator Mode（协调者模式）
# =====================================================================

class TestCoordinatorMode:
    def _agent_with_teams(self, enabled: bool, team_count: int):
        from unittest.mock import MagicMock

        agent = MagicMock()
        agent.enable_coordinator_mode = enabled
        agent._team_manager = MagicMock()
        agent._team_manager.list_teams.return_value = ["squad"] * team_count
        # 用真实 property 求值，不走 MagicMock 的自动属性
        from codeplus.agent import Agent
        return Agent.coordinator_mode.fget(agent)









# =====================================================================
# 8. Config Extensions（配置项扩展）
# =====================================================================

class TestConfigExtensions:
    pass



# =====================================================================
# 9. Transcript Persistence（会话记录持久化）
# =====================================================================

class TestTranscript:

    pass


# =====================================================================
# 10. Agent build_system_prompt 集成测试
# =====================================================================

class TestAgentCoordinatorIntegration:
    pass




class TestTaskStopTool:
    """TaskStop 让 Lead 在派错方向时及时止损。"""

    def _mgr_with_member(self, backend="in-process", active=True):
        from unittest.mock import MagicMock
        from codeplus.teams.manager import TeamManager

        mgr = TeamManager()
        team = mgr.create_team("squad", "lead-1", description="t")
        member = TeammateInfo(
            name="scout", agent_id="a1", agent_type="general-purpose",
            model="", worktree_path="", backend_type=backend, is_active=active,
        )
        team.add_member(member)
        return mgr, team, member






class TestCoordinatorReminderShape:
    pass



class TestTeamFileSchema:
    """config.json 的字段格式：键名一律 camelCase。"""



# =====================================================================
# 后台任务的 idle 回传
# =====================================================================

class TestBackgroundTaskIdleNotification:
    """in-process 队友由 TaskManager 拉起，跑完要把 idle 通知投进 lead 的信箱。

    lead 读信箱用的是 team.lead_agent_id，投递方用别的键写进去，
    消息会落在一个 lead 不读的收件箱里，派活链路就断了。
    """

