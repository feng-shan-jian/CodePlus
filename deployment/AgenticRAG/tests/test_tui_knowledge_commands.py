"""Real Textual dispatch and cancellation over the actual management worker."""
import asyncio
from pathlib import Path
import runpy
import threading
from unittest.mock import AsyncMock, Mock, call
from uuid import UUID

from codeplus.app import CodePlusApp
from codeplus.config import ProviderConfig
from agentic_rag.adapters.codeplus import management as C

U = runpy.run_path(str(Path(__file__).with_name('test_user_commands.py')))
user = U['user']


def test_tui_cancel_remains_busy_and_restores_selection(user, monkeypatch):
    async def run():
        monkeypatch.chdir(user.root)
        monkeypatch.setattr('codeplus.app.resolve_context_window', AsyncMock())
        provider = ProviderConfig('fixture', 'openai-compat', 'https://fixture.invalid', 'fixture', 'synthetic')
        app = CodePlusApp([provider], enable_fork=False, knowledge_development_config=str(user.path))
        reached, release = threading.Event(), threading.Event()
        build = C.build_changes
        def controlled(*args, **kwargs):
            def observe(stage, value):
                if stage == 'validated':
                    reached.set(); assert release.wait(15)
            return build(*args, **kwargs, observer=observe)
        monkeypatch.setattr(C, 'build_changes', controlled)
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await app._dispatch_command('/knowledge create "TUI 中文库"')
                await app._agent_task
                selected = app.knowledge_library
                session_id = app.session.session_id
                source = user.root/'TUI 文档.md'; source.write_text('# Input\nTelescope certificate.\n')
                await app._dispatch_command(f'/knowledge import "{source}"')
                task = app._agent_task
                assert await asyncio.to_thread(reached.wait, 10)
                try:
                    await app.action_handle_ctrl_c()
                    await pilot.pause()
                    assert not task.done() and app._knowledge_active
                    await app._dispatch_command('/session new')
                    assert app.session.session_id == session_id
                finally:
                    release.set()
                await asyncio.wait_for(task, 10)
                assert app.last_knowledge_command['status'] == 'cancelled'
                assert not app._knowledge_active
                await app._dispatch_command('/session new')
                assert app.knowledge_library is None
                await app._dispatch_command('/session resume '+session_id)
                assert app.knowledge_library == selected and app.agent.execution_policy is None
                await pilot.pause()
        finally:
            release.set()
            app.session.close()
            await app.client._client.close()
    asyncio.run(run())


def test_tui_watch_publishes_file_edits_and_stops_on_exit(user, monkeypatch):
    async def run():
        monkeypatch.chdir(user.root)
        monkeypatch.setattr('codeplus.app.resolve_context_window', AsyncMock())
        provider = ProviderConfig('fixture', 'openai-compat', 'https://fixture.invalid', 'fixture', 'synthetic')
        app = CodePlusApp([provider], enable_fork=False, knowledge_development_config=str(user.path))
        source = user.root/'自动更新.md'
        messages = Mock(wraps=app.add_system_message)
        monkeypatch.setattr(app, 'add_system_message', messages)
        source.write_text('# Guide\nOriginal telescope approval.\n', encoding='utf-8')
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                watcher = app._knowledge_watcher
                await app._dispatch_command('/knowledge create 自动更新库')
                await app._agent_task
                kb = UUID(str(app.knowledge_library))
                await app._dispatch_command(f'/knowledge watch "{source}"')
                await app._agent_task
                async def published_after(previous):
                    async with asyncio.timeout(20):
                        while True:
                            revision = user.catalog.get_library(kb).current_revision_id
                            if revision is not None and revision != previous:
                                return revision
                            await pilot.pause(0.05)
                first = await published_after(None)
                source.write_text('# Guide\nUpdated telescope approval.\n', encoding='utf-8')
                await published_after(first)
                async with asyncio.timeout(20):
                    while call(f'Knowledge auto-sync: 0 new, 1 updated [{kb}].') not in messages.call_args_list:
                        await pilot.pause(0.05)
            assert not watcher.thread.is_alive()
            assert app._knowledge_watcher is None
        finally:
            app.session.close()
            await app.client._client.close()
    asyncio.run(run())
