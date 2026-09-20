"""Command recovery uses real SQLite state and the existing in-memory vector store."""

import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from codeplus.commands.handlers.knowledge import USAGE, handle_knowledge
from codeplus.commands.registry import CommandContext
from codeplus.knowledge.citations import KnowledgeContext
from test_knowledge_service import service  # Reuse the SQLite/MemoryStore fixture.


@pytest.fixture
def command(service):
    knowledge = KnowledgeContext(service.config)
    knowledge._service = service
    knowledge.bind({"kb_id": service.create("commands")["id"]})
    messages = []
    ctx = CommandContext("", None, None, None, None, None,
                         SimpleNamespace(add_system_message=messages.append),
                         {"knowledge": knowledge, "set_knowledge_binding": knowledge.bind})
    return ctx, messages


@pytest.mark.asyncio
async def test_batch_counts_and_unregistered_failure_recovery(command, service, tmp_path, monkeypatch):
    ctx, messages = command
    kb_id = service.status(ctx.config["knowledge"].binding["kb_id"])["id"]
    directory = tmp_path / "资料 with spaces"
    directory.mkdir()
    unchanged = directory / "unchanged.md"
    unchanged.write_text("Already imported.", encoding="utf-8")
    service.import_document(kb_id, unchanged)
    fresh = directory / "a new's file.MD"
    fresh.write_text("New document.", encoding="utf-8")
    empty = directory / "empty.md"
    empty.write_text("", encoding="utf-8")
    (directory / "ignored.txt").write_text("Unsupported.", encoding="utf-8")
    snapshots = []
    loop_thread = threading.get_ident()

    def progress(payload):
        assert threading.get_ident() == loop_thread
        snapshots.append(payload)

    original = service.import_document

    def checked_import(*args):
        assert threading.get_ident() != loop_thread
        return original(*args)

    monkeypatch.setattr(service, "import_document", checked_import)
    ctx.config["knowledge_progress"] = progress
    ctx.args = f'import "{directory}"'
    await handle_knowledge(ctx)

    assert [p["processed"] for p in snapshots] == [0, 1, 2, 3]
    assert all(p["processed"] == p["succeeded"] + p["unchanged"] + p["failed"] for p in snapshots)
    final = snapshots[-1]
    assert {key: final[key] for key in ("total", "succeeded", "unchanged", "failed")} == {
        "total": 3, "succeeded": 1, "unchanged": 1, "failed": 1,
    }
    assert final["kb_id"] == kb_id
    assert final["failures"] == [{"path": str(empty), "reason": "Markdown has no content", "kind": "import"}]
    assert snapshots[0]["failures"] == []
    assert sum(m.startswith("开始导入") for m in messages) == 1
    assert sum(m.startswith("导入完成") for m in messages) == 1
    assert "已处理 3/3，成功 1，未变化 1，失败 1" in messages[-1]
    assert f'/knowledge import "{empty}"' in messages[-1]
    assert "/knowledge retry" not in messages[-1]
    status = service.status(kb_id)
    assert status["state"] == "READY" and len(status["documents"]) == 2

    empty.write_text("Recovered by importing the source.", encoding="utf-8")
    ctx.args = f'import "{empty}"'
    await handle_knowledge(ctx)
    assert snapshots[-1]["succeeded"] == 1 and snapshots[-1]["failed"] == 0
    assert len(service.status(kb_id)["documents"]) == 3


@pytest.mark.asyncio
async def test_registered_failure_retries_only_pending_write(command, service, tmp_path, monkeypatch):
    ctx, messages = command
    kb_id = ctx.config["knowledge"].binding["kb_id"]
    directory = tmp_path / "pending batch"
    directory.mkdir()
    first, blocked = directory / "a.md", directory / "b.md"
    first.write_text("Pending document.", encoding="utf-8")
    blocked.write_text("Not registered.", encoding="utf-8")
    snapshots = []
    ctx.config["knowledge_progress"] = snapshots.append

    def unavailable(*args):
        raise RuntimeError("vector write unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(service.store, "upsert_chunks", unavailable)
        ctx.args = f'import "{directory}"'
        await handle_knowledge(ctx)
    failures = snapshots[-1]["failures"]
    assert snapshots[-1]["processed"] == snapshots[-1]["failed"] == 2
    assert failures[0] == {"path": str(first), "reason": "vector write unavailable", "kind": "retry"}
    assert failures[1]["path"] == str(blocked) and failures[1]["kind"] == "import"
    assert "先 /knowledge retry" in failures[1]["reason"]
    pending = service.status(kb_id)["documents"]
    assert len(pending) == 1 and pending[0]["pending_operation"] == "import"
    ctx.args = f'reimport {pending[0]["id"]}'
    with pytest.raises(ValueError, match="/knowledge retry"):
        await handle_knowledge(ctx)
    ctx.args = "retry"
    await handle_knowledge(ctx)
    assert json.loads(messages[-1])["id"] == pending[0]["id"]
    assert service.status(kb_id)["state"] == "READY"
    ctx.args = f'import "{blocked}"'
    await handle_knowledge(ctx)
    assert len(service.status(kb_id)["documents"]) == 2


@pytest.mark.asyncio
async def test_reimport_reads_original_uri_and_preserves_old_commands(command, service, tmp_path):
    ctx, messages = command
    kb_id = ctx.config["knowledge"].binding["kb_id"]
    source = tmp_path / "资料 #100% owner's file.md"
    source.write_text("Old source.", encoding="utf-8")
    # Full remainder parsing also preserves an embedded apostrophe within single quotes.
    ctx.args = f"import '{source}'"
    await handle_knowledge(ctx)
    doc = service.status(kb_id)["documents"][0]
    assert Path(doc["original_path"]).read_text(encoding="utf-8") == "Old source."
    source.write_text("Updated from the original source.", encoding="utf-8")
    ctx.args = f'reimport {doc["id"]}'
    await handle_knowledge(ctx)
    current = service.status(kb_id)["documents"][0]
    assert current["id"] == doc["id"] and current["generation_id"] != doc["generation_id"]
    assert "Updated from the original" in service.search(kb_id, "source").hits[0].text
    assert "成功 1，未变化 0，失败 0" in messages[-1]

    for sub in ("sources", "status"):
        ctx.args = sub
        await handle_knowledge(ctx)
        result = json.loads(messages[-1])
        assert result == ([current] if sub == "sources" else service.status(kb_id))
    ctx.args = f'remove {doc["id"]}'
    await handle_knowledge(ctx)
    assert json.loads(messages[-1])["removed"] == 1
    ctx.args = f'reimport {doc["id"]}'
    await handle_knowledge(ctx)
    assert not service.status(kb_id)["documents"][0]["removed"]
    ctx.args = "off"
    await handle_knowledge(ctx)
    assert ctx.config["knowledge"].binding is None
    ctx.args = f"use {kb_id}"
    await handle_knowledge(ctx)
    assert ctx.config["knowledge"].binding["kb_id"] == kb_id


@pytest.mark.asyncio
async def test_reimport_validates_scope_and_does_not_substitute_missing_source(command, service, tmp_path):
    ctx, messages = command
    source = tmp_path / "removed source.md"
    source.write_text("Saved copy is not the live source.", encoding="utf-8")
    other_kb = service.create("other")["id"]
    doc = service.import_document(other_kb, source)
    ctx.args = f'reimport {doc["id"]}'
    with pytest.raises(ValueError, match="当前知识库中没有此文档"):
        await handle_knowledge(ctx)
    ctx.config["knowledge"].bind({"kb_id": other_kb})
    source.unlink()
    await handle_knowledge(ctx)
    assert "成功 0，未变化 0，失败 1" in messages[-1]
    recovery = messages[-1].rsplit('\n恢复：/knowledge import "', 1)[1]
    assert Path(recovery[:-1]) == source  # Windows source_uri uses normcase.
    assert "/knowledge retry" not in messages[-1]
    assert service.status(other_kb)["state"] == "READY"
    assert service.status(other_kb)["documents"][0]["pending_operation"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("with_menu", [False, True])
async def test_empty_command_menu_and_prepare_failure_remain_discoverable(command, service, monkeypatch, with_menu):
    ctx, messages = command
    menu_calls = []
    progress = []
    ctx.config["knowledge_progress"] = progress.append
    if with_menu:
        ctx.config["show_knowledge_menu"] = lambda: menu_calls.append(threading.get_ident())
    await handle_knowledge(ctx)
    assert menu_calls == ([threading.get_ident()] if with_menu else [])
    assert (USAGE in messages) is (not with_menu)

    def unavailable(*args):
        raise RuntimeError("service unavailable")

    store = service.store
    monkeypatch.setattr(store, "check_health", unavailable)
    messages.clear()
    with pytest.raises(RuntimeError, match="/knowledge prepare"):
        await handle_knowledge(ctx)
    assert len(menu_calls) == (2 if with_menu else 0)
    assert USAGE not in messages  # Remote retains the preparation error without extra usage output.
    service._store = store
    ctx.args = 'import "not imported.md"'
    with pytest.raises(RuntimeError, match="/knowledge prepare"):
        await handle_knowledge(ctx)
    assert not any(m.startswith("开始导入") or m.startswith("导入完成") for m in messages)
    assert progress == []  # An environment failure never becomes a made-up file failure.
    assert service.status(ctx.config["knowledge"].binding["kb_id"])["documents"] == []


@pytest.mark.asyncio
async def test_import_keeps_event_loop_responsive(command, service, tmp_path, monkeypatch):
    ctx, messages = command
    source = tmp_path / "slow import.md"
    source.write_text("Background import.", encoding="utf-8")
    started, release = threading.Event(), threading.Event()
    original = service.import_document

    def slow_import(*args):
        started.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(service, "import_document", slow_import)
    ctx.args = f'import "{source}"'
    task = asyncio.create_task(handle_knowledge(ctx))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        await asyncio.sleep(0)
        assert not task.done()
        assert any(m.startswith("开始导入") for m in messages)
    finally:
        release.set()
        await task
    assert "已处理 1/1，成功 1，未变化 0，失败 0" in messages[-1]
