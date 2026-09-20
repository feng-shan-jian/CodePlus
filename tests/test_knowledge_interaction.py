"""Real Textual input/popup flows with local metadata and deterministic model/store fixtures."""

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest
from textual import events
from textual.widgets import Static

from codeplus.app import ChatInput, CodePlusApp
from codeplus.commands.completion import CompletionPopup
from codeplus.config import ProviderConfig
from test_knowledge_service import service


@pytest.fixture
def app(service, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = ProviderConfig("test", "openai-compat", "http://127.0.0.1:1", "test", "test")
    app = CodePlusApp([provider], enable_fork=False, knowledge_config=service.config)
    app.knowledge._service = service
    return app


async def type_command(app, pilot, text, *, execute=True):
    widget = app.query_one(ChatInput)
    widget.focus()
    widget.load_text(text)
    widget.move_cursor(widget.document.end)
    await pilot.pause()
    if execute:
        await pilot.press("enter")
        await pilot.pause()
        if app._knowledge_task:
            await app._knowledge_task
        await pilot.pause()


@pytest.mark.asyncio
async def test_menu_create_select_cancel_and_current_input_race(app, service):
    old = service.create("原有库")
    async with app.run_test(size=(120, 40)) as pilot:
        widget, popup = app.query_one(ChatInput), app.query_one(CompletionPopup)
        await type_command(app, pilot, "/knowledge")
        assert popup.kind == "knowledge" and widget.text == "/knowledge "
        await pilot.press("enter")  # Create only opens the name prompt.
        assert widget.text == "/knowledge create " and app.knowledge.binding is None
        await pilot.press("新", "库", "enter")
        await pilot.pause()
        await app._knowledge_task
        created = app.knowledge.binding["kb_id"]
        assert service.status(created)["name"] == "新库"
        assert "新库" in str(app.query_one("#knowledge-label", Static).render())

        await type_command(app, pilot, "/knowledge use")
        await pilot.press("down", "up")
        assert app.knowledge.binding["kb_id"] == created
        await pilot.press("escape", "escape")
        assert not popup.is_visible and app.knowledge.binding["kb_id"] == created
        assert app.focused is widget
        await type_command(app, pilot, "/knowledge use 原有", execute=False)
        assert popup.get_selected() == "/knowledge use " + old["id"]
        await pilot.press("tab")
        assert widget.text.strip() == "/knowledge use " + old["id"]
        assert app.knowledge.binding["kb_id"] == created
        await pilot.press("enter")
        await pilot.pause()
        await app._knowledge_task
        assert app.knowledge.binding["kb_id"] == old["id"]

        await type_command(app, pilot, "/knowledge use 新", execute=False)
        assert popup.get_selected() == "/knowledge use " + created
        # Deliberately do not yield to Changed/SlashMenuUpdate between edit and Enter.
        widget.load_text("/knowledge off")
        widget.action_submit()
        await pilot.pause()
        await app._knowledge_task
        assert app.knowledge.binding is None
        assert widget._history[-1] == "/knowledge off"
        assert app.focused is widget

        await type_command(app, pilot, "/know", execute=False)
        widget.load_text("/help")
        widget.action_submit()
        await pilot.pause()
        assert widget._history[-1] == "/help" and not popup.is_visible


@pytest.mark.asyncio
async def test_path_completion_sources_actions_and_timestamps(app, service, tmp_path):
    folder = tmp_path / "资料 folder"
    folder.mkdir()
    source = folder / "政策 [v1].md"
    source.write_text("First policy.", encoding="utf-8")
    async with app.run_test(size=(140, 42)) as pilot:
        widget, popup = app.query_one(ChatInput), app.query_one(CompletionPopup)
        await type_command(app, pilot, "/knowledge create 资料库")
        kb = app.knowledge.binding["kb_id"]
        await type_command(app, pilot, f'/knowledge import "{folder}\\政', execute=False)
        await app.workers.wait_for_complete()
        assert popup.get_selected() == f'/knowledge import "{source}"'
        await pilot.press("tab")
        assert widget.text.strip() == f'/knowledge import "{source}"'
        assert not service.status(kb)["documents"]
        await pilot.press("enter")
        await pilot.pause()
        await app._knowledge_task
        document = service.status(kb)["documents"][0]
        assert document["updated_at"] is not None
        # Historical rows have no invented clock value.
        with service.metadata.connect() as db:
            db.execute("UPDATE documents SET updated_at=NULL WHERE id=?", (document["id"],))
        await app._load_knowledge_choices()
        await type_command(app, pilot, "/knowledge sources")
        assert "政策 [v1].md" in str(popup.render()) and "未记录" in str(popup.render())
        widget.load_text("/knowledge status")
        widget.action_submit()  # No Changed event processing between this edit and Enter.
        await pilot.pause()
        await app._knowledge_task
        assert widget._history[-1] == "/knowledge status" and not popup.is_visible
        await type_command(app, pilot, "/knowledge sources")
        await pilot.press("enter")
        assert popup.kind == "knowledge-actions"
        assert popup.get_selected() == "/knowledge reimport " + document["id"]
        await pilot.press("escape")
        assert popup.kind == "knowledge-source"
        source.write_text("Revised policy.", encoding="utf-8")
        await pilot.press("enter", "enter")
        await pilot.pause()
        await app._knowledge_task
        assert service.status(kb)["documents"][0]["updated_at"] is not None
        await type_command(app, pilot, "/knowledge sources")
        await pilot.press("enter", "down", "enter")
        await pilot.pause()
        await app._knowledge_task
        assert service.status(kb)["documents"][0]["removed"]
        assert source.exists() and app.knowledge.binding["kb_id"] == kb
        await type_command(app, pilot, "/knowledge sources")
        await pilot.press("enter")
        assert popup._values == ["/knowledge reimport " + document["id"]]

        # Quoted and unquoted absolute Windows paths with spaces are one argument.
        await type_command(app, pilot, f"/knowledge import {source}")
        assert not service.status(kb)["documents"][0]["removed"]


@pytest.mark.asyncio
async def test_import_stays_alive_after_escape_and_busy_binding_is_protected(app, service, tmp_path, monkeypatch):
    source = tmp_path / "slow.md"
    source.write_text("Background import.", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    original = service.import_document
    def slow_import(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(service, "import_document", slow_import)
    async with app.run_test(size=(120, 40)) as pilot:
        try:
            await type_command(app, pilot, "/knowledge create busy")
            kb = app.knowledge.binding["kb_id"]
            await type_command(app, pilot, f'/knowledge import "{source}"', execute=False)
            await pilot.press("enter")
            assert await asyncio.to_thread(entered.wait, 5)
            assert "0/1" in str(app.query_one("#knowledge-label", Static).render())
            await type_command(app, pilot, "/knowledge ", execute=False)
            await pilot.press("escape")
            assert not app._knowledge_task.done()
            await pilot.press("a", "b", "c")
            assert app.query_one(ChatInput).text == "abc"
            await type_command(app, pilot, "/knowledge off", execute=False)
            await pilot.press("enter")
            assert app.knowledge.binding["kb_id"] == kb
            assert not app._knowledge_task.done()
        finally:
            release.set()
        await app._knowledge_task
        assert len(service.status(kb)["documents"]) == 1
        assert "成功 1" in str(app.query_one("#knowledge-label", Static).render())


@pytest.mark.asyncio
async def test_chinese_key_and_paste_are_text_single_enter_submits(app, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(app, "_send_message", send)
    async with app.run_test(size=(120, 40)) as pilot:
        widget = app.query_one(ChatInput)
        await pilot.press("中", "文")
        assert widget.text == "中文" and not send.called
        app.post_message(events.Paste("输入\n下一行"))
        await pilot.pause()
        assert widget.text == "中文输入\n下一行" and not send.called
        await pilot.press("enter")
        await pilot.pause()
        send.assert_awaited_once_with("中文输入\n下一行")
        assert widget.text == ""
        await pilot.press("a", "shift+enter", "b", "ctrl+j", "c")
        assert widget.text == "a\nb\nc" and send.await_count == 1


@pytest.mark.asyncio
async def test_failure_recovery_actions_and_delayed_choices(app, service, tmp_path, monkeypatch):
    source = tmp_path / "recover.md"
    source.write_text("Recovered document.", encoding="utf-8")
    async with app.run_test(size=(120, 40)) as pilot:
        widget, popup = app.query_one(ChatInput), app.query_one(CompletionPopup)
        prepare = service.prepare
        def offline(*args):
            raise OSError("service offline")
        monkeypatch.setattr(service, "prepare", offline)
        await type_command(app, pilot, "/knowledge")
        assert popup.is_visible and "/knowledge prepare" in popup._values
        assert app.knowledge.binding is None
        monkeypatch.setattr(service, "prepare", prepare)
        await type_command(app, pilot, "/knowledge create recovery")
        kb = app.knowledge.binding["kb_id"]

        original = service.import_document
        def parse_failure(*args):
            raise ValueError("parse [failed]")
        monkeypatch.setattr(service, "import_document", parse_failure)
        await type_command(app, pilot, f'/knowledge import "{source}"')
        assert not service.status(kb)["documents"]
        assert app._knowledge_progress["failures"][0]["kind"] == "import"
        await type_command(app, pilot, "/knowledge")
        assert popup._values[-1] == f'/knowledge import "{source}"'
        monkeypatch.setattr(service, "import_document", original)
        await pilot.press(*(["down"] * (len(popup._values) - 1)))
        assert "parse [failed]" in str(popup.render())
        await pilot.press("enter")
        await pilot.pause()
        await app._knowledge_task
        assert len(service.status(kb)["documents"]) == 1

        # A registered pending write has a different recovery command.
        source.write_text("Pending update.", encoding="utf-8")
        upsert = service.store.upsert_chunks
        def write_failure(*args):
            raise OSError("store unavailable")
        monkeypatch.setattr(service.store, "upsert_chunks", write_failure)
        await type_command(app, pilot, f'/knowledge import "{source}"')
        assert app._knowledge_progress["failures"][0]["kind"] == "retry"
        await type_command(app, pilot, "/knowledge sources")
        await pilot.press("enter")
        assert popup.get_selected() == "/knowledge retry"
        monkeypatch.setattr(service.store, "upsert_chunks", upsert)
        await pilot.press("enter")
        await pilot.pause()
        await app._knowledge_task
        assert service.status(kb)["state"] == "READY"

        # Loading a selector cannot freeze typing or reopen it over a newer command.
        entered, release = threading.Event(), threading.Event()
        list_libraries = service.list_libraries
        def delayed_list():
            result = list_libraries()
            entered.set()
            assert release.wait(10)
            return result
        monkeypatch.setattr(service, "list_libraries", delayed_list)
        app._knowledge_choices_loaded = False
        try:
            await type_command(app, pilot, "/knowledge use ", execute=False)
            assert await asyncio.to_thread(entered.wait, 5)
            widget.load_text("/help")
            widget.action_submit()
            await pilot.pause()
            assert widget._history[-1] == "/help"
            assert app.knowledge.binding["kb_id"] == kb
            monkeypatch.setattr(service, "list_libraries", list_libraries)
            newer = await asyncio.to_thread(service.create, "newer")
            await app._load_knowledge_choices()
        finally:
            release.set()
        await app.workers.wait_for_complete()
        assert newer["id"] in [item["id"] for item in app._knowledge_libraries]
        assert widget.text == "" and not popup.is_visible and app.focused is widget
        monkeypatch.setattr(service, "list_libraries", offline)
        await app._load_knowledge_choices()
        await type_command(app, pilot, "/knowledge use")
        assert "读取知识库列表失败" in str(popup.render())
        assert "没有匹配" not in str(popup.render())


@pytest.mark.asyncio
async def test_batch_failed_paths_survive_other_imports_and_library_switch(app, service, tmp_path, monkeypatch):
    folder = tmp_path / "failed sources"
    folder.mkdir()
    paths = [folder / "first.md", folder / "second.md"]
    for path in paths:
        path.write_text("Retry this source.", encoding="utf-8")
    async with app.run_test(size=(120, 40)) as pilot:
        popup = app.query_one(CompletionPopup)
        await type_command(app, pilot, "/knowledge create failures")
        kb = app.knowledge.binding["kb_id"]
        original = service.import_document
        def parse_failure(*args):
            raise ValueError("parse failed")
        monkeypatch.setattr(service, "import_document", parse_failure)
        await type_command(app, pilot, f'/knowledge import "{folder}\\"')
        assert len(app._knowledge_failures[kb]) == 2
        monkeypatch.setattr(service, "import_document", original)
        await type_command(app, pilot, f'/knowledge import "{paths[0]}"')
        await type_command(app, pilot, "/knowledge")
        assert f'/knowledge import "{paths[1]}"' in popup._values
        assert f'/knowledge import "{paths[0]}"' not in popup._values
        await type_command(app, pilot, "/knowledge create other")
        await type_command(app, pilot, "/knowledge")
        assert f'/knowledge import "{paths[1]}"' not in popup._values
        await type_command(app, pilot, "/knowledge use " + kb)
        await type_command(app, pilot, "/knowledge")
        assert f'/knowledge import "{paths[1]}"' in popup._values
        await type_command(app, pilot, f'/knowledge import "{paths[1]}"')
        assert not app._knowledge_failures[kb]
