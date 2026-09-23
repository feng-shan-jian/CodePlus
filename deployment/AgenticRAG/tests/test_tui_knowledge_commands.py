"""Real Textual dispatch and cancellation over the actual management worker."""
import asyncio
from pathlib import Path
import runpy
import threading
from unittest.mock import AsyncMock

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
