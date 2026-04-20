from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from codeplus.conversation import (
    ConversationManager,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)
from codeplus.memory.auto_memory import MemoryManager
from codeplus.memory.instructions import (
    MAX_INCLUDE_DEPTH,
    load_instructions,
    process_includes,
)
from codeplus.memory.session import (
    ResumeResult,
    Session,
    SessionManager,
    SessionMeta,
    SessionRecord,
    make_compact_boundary,
    parse_compact_boundary,
    records_to_messages,
)

# =========================================================================
# A. 指令文件（LICENSE）
# =========================================================================

class TestProcessIncludes:
    def test_no_includes(self, tmp_path: Path) -> None:
        content = "line1\nline2\nline3"
        result = process_includes(content, tmp_path, tmp_path)
        assert result == content

    def test_basic_include(self, tmp_path: Path) -> None:
        child = tmp_path / "child.md"
        child.write_text("included content", encoding="utf-8")
        content = "before\n@./child.md\nafter"
        result = process_includes(content, tmp_path, tmp_path)
        assert "included content" in result
        assert "before" in result
        assert "after" in result

    def test_recursive_include(self, tmp_path: Path) -> None:
        grandchild = tmp_path / "grandchild.md"
        grandchild.write_text("deep content", encoding="utf-8")
        child = tmp_path / "child.md"
        child.write_text("@./grandchild.md", encoding="utf-8")
        content = "@./child.md"
        result = process_includes(content, tmp_path, tmp_path)
        assert "deep content" in result

    def test_depth_limit(self, tmp_path: Path) -> None:
        content = "should stop"
        result = process_includes(content, tmp_path, tmp_path, depth=MAX_INCLUDE_DEPTH)
        assert result == content

    def test_path_outside_project_not_found(self, tmp_path: Path) -> None:
        """项目外路径不做限制，不存在的文件显示 file not found。"""
        content = "@../../etc/passwd"
        result = process_includes(content, tmp_path, tmp_path)
        assert "skipped: file not found" in result

    def test_file_not_found(self, tmp_path: Path) -> None:
        content = "@./nonexistent.md"
        result = process_includes(content, tmp_path, tmp_path)
        assert "skipped: file not found" in result

    def test_cycle_detection(self, tmp_path: Path) -> None:
        """循环检测：A→B→A 不会无限递归。"""
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("start\n@./b.md\nend-a", encoding="utf-8")
        b.write_text("middle\n@./a.md\nend-b", encoding="utf-8")
        result = process_includes(
            "@./a.md", tmp_path, tmp_path
        )
        # a.md 被展开，b.md 也被展开，但 b 中再次 @./a.md 时被跳过
        assert "start" in result
        assert "middle" in result
        assert "end-b" in result
        # a.md 不会被第二次展开（循环检测生效）
        assert result.count("start") == 1

    def test_code_block_skip(self, tmp_path: Path) -> None:
        """代码块内的 @ 引用不展开。"""
        child = tmp_path / "child.md"
        child.write_text("should not appear", encoding="utf-8")
        content = "before\n```\n@./child.md\n```\nafter"
        result = process_includes(content, tmp_path, tmp_path)
        assert "should not appear" not in result
        assert "@./child.md" in result
        assert "before" in result
        assert "after" in result

    def test_new_at_syntax(self, tmp_path: Path) -> None:
        """新格式 @./path 语法。"""
        child = tmp_path / "child.md"
        child.write_text("new syntax content", encoding="utf-8")
        content = "before\n@./child.md\nafter"
        result = process_includes(content, tmp_path, tmp_path)
        assert "new syntax content" in result

class TestLoadInstructions:
    def test_single_layer(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        codeplus_md = tmp_path / "LICENSE"
        codeplus_md.write_text("project instructions", encoding="utf-8")
        result = load_instructions(str(tmp_path))
        assert "project instructions" in result

    def test_multi_layer_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """同一目录下 LICENSE 在前，.codeplus/LICENSE 在后（优先级更高）。"""
        root_md = tmp_path / "LICENSE"
        root_md.write_text("root level", encoding="utf-8")
        dotdir = tmp_path / ".codeplus"
        dotdir.mkdir()
        dot_md = dotdir / "LICENSE"
        dot_md.write_text("dotdir level", encoding="utf-8")
        result = load_instructions(str(tmp_path))
        assert result.index("root level") < result.index("dotdir level")
        assert "---" in result

    def test_dotdir_walks_up(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """.codeplus/LICENSE 参与逐级遍历，深层目录的排在后面。"""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        sub = tmp_path / "pkg" / "deep"
        sub.mkdir(parents=True)
        (tmp_path / ".codeplus").mkdir(exist_ok=True)
        (tmp_path / ".codeplus" / "LICENSE").write_text("dotdir root", encoding="utf-8")
        (sub / ".codeplus").mkdir()
        (sub / ".codeplus" / "LICENSE").write_text("dotdir leaf", encoding="utf-8")
        result = load_instructions(str(sub))
        assert result.index("dotdir root") < result.index("dotdir leaf")

    def test_no_files_returns_empty(self, tmp_path: Path) -> None:
        result = load_instructions(str(tmp_path))
        assert result == ""

# =========================================================================
# B. 会话记录 SessionRecord
# =========================================================================

class TestSessionRecord:
    def test_user_message_roundtrip(self) -> None:
        msg = Message(role="user", content="hello world")
        records = SessionRecord.from_message(msg)
        assert len(records) == 1
        assert records[0].role == "user"
        assert records[0].type is None
        assert records[0].content == "hello world"

        line = records[0].to_jsonl()
        restored = SessionRecord.from_jsonl(line)
        assert restored is not None
        assert restored.role == "user"
        assert restored.content == "hello world"

    def test_assistant_with_tool_uses_roundtrip(self) -> None:
        msg = Message(
            role="assistant",
            content="Let me check",
            tool_uses=[
                ToolUseBlock(tool_use_id="t1", tool_name="ReadFile", arguments={"path": "/a"})
            ],
        )
        records = SessionRecord.from_message(msg)
        assert len(records) == 1
        # 工具块以中立命名内联在同一条记录里，正文仍在 content
        assert records[0].content == "Let me check"
        assert records[0].tool_uses[0]["tool_use_id"] == "t1"
        assert records[0].tool_uses[0]["tool_name"] == "ReadFile"
        assert records[0].tool_uses[0]["arguments"] == {"path": "/a"}

        restored = SessionRecord.from_jsonl(records[0].to_jsonl())
        got = restored.to_message()
        assert got.tool_uses[0].tool_name == "ReadFile"
        assert got.tool_uses[0].arguments == {"path": "/a"}

    def test_tool_results_inline_in_single_record(self) -> None:
        msg = Message(
            role="user",
            content="",
            tool_results=[
                ToolResultBlock(tool_use_id="t1", content="result1"),
                ToolResultBlock(tool_use_id="t2", content="result2", is_error=True),
            ],
        )
        records = SessionRecord.from_message(msg)
        assert len(records) == 1
        assert len(records[0].tool_results) == 2
        assert records[0].tool_results[0]["tool_use_id"] == "t1"
        assert records[0].tool_results[1]["is_error"] is True

    def test_malformed_jsonl_returns_none(self) -> None:
        assert SessionRecord.from_jsonl("{bad json") is None
        # 缺少 role 字段的行（含旧格式记录）安全跳过
        assert SessionRecord.from_jsonl('{"type":"assistant","content":"x"}') is None

    def test_plain_assistant_message(self) -> None:
        msg = Message(role="assistant", content="done")
        records = SessionRecord.from_message(msg)
        assert len(records) == 1
        assert records[0].content == "done"

# =========================================================================
# C. 会话 Session 与会话管理器 SessionManager
# =========================================================================

class TestSession:
    def test_append_writes_jsonl_and_updates_meta(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / ".codeplus" / "sessions"
        sessions_dir.mkdir(parents=True)
        meta = SessionMeta(id="test_session")
        meta.save(sessions_dir / "test_session.meta")
        jsonl_path = sessions_dir / "test_session.jsonl"

        with open(jsonl_path, "a", encoding="utf-8") as f:
            session = Session("test_session", f, meta, sessions_dir)
            session.append(Message(role="user", content="hello"))
            session.append(Message(role="assistant", content="hi"))

        lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert meta.message_count == 2
        assert meta.title == "hello"

    def test_title_set_from_first_user_message(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / ".codeplus" / "sessions"
        sessions_dir.mkdir(parents=True)
        meta = SessionMeta(id="test_session")
        jsonl_path = sessions_dir / "test_session.jsonl"

        with open(jsonl_path, "a", encoding="utf-8") as f:
            session = Session("test_session", f, meta, sessions_dir)
            session.append(Message(role="assistant", content="welcome"))
            assert meta.title == ""
            session.append(Message(role="user", content="my first question"))
            assert meta.title == "my first question"

class TestSessionManager:

    def test_create_and_list(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s1 = mgr.create()
        s1.append(Message(role="user", content="test"))
        s1.close()

        s2 = mgr.create()
        s2.append(Message(role="user", content="test2"))
        s2.close()

        metas = mgr.list()
        assert len(metas) == 2
        assert metas[0].last_active >= metas[1].last_active

    def test_delete(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        sid = s.session_id
        s.close()

        assert mgr.delete(sid) is True
        assert mgr.delete(sid) is False
        assert len(mgr.list()) == 0

    def test_cleanup_removes_old_sessions(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        s.meta.last_active = datetime.now(timezone.utc) - timedelta(days=31)
        s.meta.save(mgr._sessions_dir / f"{s.session_id}.meta")
        s.close()

        removed = mgr.cleanup(max_age_days=30)
        assert removed == 1
        assert len(mgr.list()) == 0

    def test_create_generates_valid_id(self, tmp_path: Path) -> None:
        mgr = SessionManager(str(tmp_path))
        s = mgr.create()
        assert s.session_id.startswith("session_")
        assert len(s.session_id.split("_")) == 4
        s.close()

# =========================================================================
# D. 会话恢复
# =========================================================================

def _rec(role: str, content: str = "", **kw: Any) -> SessionRecord:
    return SessionRecord(role=role, content=content, timestamp=datetime.now(timezone.utc), **kw)


class TestRecordsToMessages:
    def test_basic_roundtrip(self) -> None:
        records = [_rec("user", "hello"), _rec("assistant", "world")]
        messages = records_to_messages(records)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"



class TestSessionResume:
    pass



# =========================================================================
# D2. 压缩边界的持久化 + 恢复时重新加载压缩后的状态
# =========================================================================

class TestCompactBoundaryRoundTrip:
    pass







# =========================================================================
# F. 会话元数据 SessionMeta
# =========================================================================

class TestSessionMeta:
    pass


# =========================================================================
# G. 记忆管理器 MemoryManager
# =========================================================================

class TestMemoryManager:
    """Manager：独立 .md 文件 + frontmatter + MEMORY.md 索引格式。"""







# =========================================================================
# H. 会话注入长期记忆 inject_long_term_memory
# =========================================================================

class TestConversationInjection:
    pass








# =========================================================================
# I. 记忆抽取 prompt 的构造
# =========================================================================

class TestMemoryExtraction:
    pass
