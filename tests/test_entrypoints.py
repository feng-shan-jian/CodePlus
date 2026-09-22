"""Ordinary entry points and persisted sessions work without a retrieval engine."""

import json
from unittest.mock import AsyncMock

import pytest
from textual import events
from textual.widgets import Markdown

from codeplus.__main__ import _run_prompt
from codeplus.app import ChatInput, CodePlusApp
from codeplus.config import AppConfig, ProviderConfig, load_config
from codeplus.conversation import Message
from codeplus.memory.session import SessionManager
from codeplus.permissions import PermissionMode
from codeplus.remote import RemoteServer
from codeplus.tools.base import StreamEnd, TextDelta
from test_agent import MockLLMClient


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = ProviderConfig("test", "openai", "http://127.0.0.1:1", "test", "test")
    client = MockLLMClient([[TextDelta("ordinary answer"), StreamEnd("end_turn", 1, 1)]])
    for module in ("codeplus.client", "codeplus.app", "codeplus.remote"):
        monkeypatch.setattr(f"{module}.create_client", lambda *_: client)
        monkeypatch.setattr(f"{module}.resolve_context_window", AsyncMock())
    return AppConfig(providers=[provider], enable_fork=False)


@pytest.mark.parametrize("binding", [None, {"kb_id": "removed", "top_k": 5}, "obsolete"])
def test_resume_ignores_retired_metadata_without_losing_messages(tmp_path, binding):
    manager = SessionManager(str(tmp_path))
    session = manager.create()
    session.append(Message(role="user", content="Keep this question"))
    session.append(Message(role="assistant", content="Historical text [K:old:chunk]"))
    session.close()
    meta_path = tmp_path / ".codeplus/sessions" / f"{session.session_id}.meta"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata.update(knowledge_binding=binding, knowledge={"enabled": True})
    meta_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert manager.list()[0].id == session.session_id
    resumed = manager.resume(session.session_id)
    assert resumed is not None
    assert [m.content for m in resumed.messages] == ["Keep this question", "Historical text [K:old:chunk]"]
    assert not hasattr(resumed.session.meta, "knowledge_binding")
    resumed.session.append(Message(role="user", content="Continue normally"))
    resumed.session.close()
    assert "knowledge_binding" not in json.loads(meta_path.read_text(encoding="utf-8"))


def test_old_config_does_not_require_retired_services(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("providers:\n  - {name: test, protocol: openai, base_url: 'http://127.0.0.1:1', model: test}\n"
                    "knowledge: {enabled: true, managed_local: true, data_dir: 'missing'}\n", encoding="utf-8")
    config = load_config(path)
    assert config.providers[0].model == "test"
    assert not hasattr(config, "knowledge")


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "stream-json"])
async def test_prompt_outputs_without_retrieval(environment, output_format, capsys):
    await _run_prompt(environment, PermissionMode.DEFAULT, None, "hello", output_format)
    output = capsys.readouterr().out
    if output_format == "text":
        assert output == "ordinary answer"
    else:
        records = [json.loads(line) for line in output.splitlines()]
        assert records[-1]["type"] == "result"
        assert records[-1]["result"] == "ordinary answer"
        assert records[-1]["tool_calls"] == []


@pytest.mark.asyncio
async def test_chinese_input_and_paste_submit_once(environment, monkeypatch):
    app = CodePlusApp(environment.providers, enable_fork=False)
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
    app.session.close()


@pytest.mark.asyncio
async def test_tui_session_resume_and_command_menu(environment):
    app = CodePlusApp(environment.providers, enable_fork=False)
    async with app.run_test(size=(120, 40)) as pilot:
        archived = app.session_manager.create()
        archived.append(Message(role="assistant", content="Saved answer [K:old:chunk]"))
        archived.close()
        await app._dispatch_command(f"/session resume {archived.session_id}")
        await pilot.pause()
        assert app.session.session_id == archived.session_id
        assert app.conversation.history[-1].content == "Saved answer [K:old:chunk]"
        assert app.query(Markdown)
        assert app.command_registry.find("knowledge").handler.__module__ == 'codeplus.commands.handlers.knowledge'
        widget = app.query_one(ChatInput)
        widget.load_text("/hel")
        await pilot.press("tab")
        assert widget.text == "/help "
    app.session.close()


@pytest.mark.asyncio
async def test_remote_stream_and_replay_keep_plain_message_contract(environment, monkeypatch):
    server = RemoteServer(environment.providers, config=environment)
    server._init_agent()
    broadcast = AsyncMock()
    monkeypatch.setattr(server, "_broadcast", broadcast)
    await server._handle_user_message("hello")
    messages = [call.args[0] for call in broadcast.await_args_list]
    assert {"type": "stream_end", "data": {"text": "ordinary answer"}} in messages
    assert not any(msg["type"] == "error" for msg in messages)
    await server._render_restored_messages([Message(role="assistant", content="Old answer [K:old:chunk]")])
    assert broadcast.await_args.args[0] == {"type": "replay_assistant", "data": {"content": "Old answer [K:old:chunk]"}}
    assert server.command_registry.find("knowledge").handler.__module__ == 'codeplus.commands.handlers.knowledge'
    assert server.knowledge_feature_available is False
    server.session.close()
