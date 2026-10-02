"""Real file changes and command entry; controlled embedding/index transports."""

import asyncio
from pathlib import Path
import runpy
import threading
import time
from uuid import UUID

import pytest

from agentic_rag.ingestion.watching import SourceWatcher, subscriptions, changed_paths, _Notifications

H=runpy.run_path(str(Path(__file__).with_name('test_user_commands.py')))
user=H['user']


def test_watch_sync_commands_hash_baseline_failures_and_unwatch(user):
    async def run():
        kb=(await user.command('create watched'))['data']['kb_id']
        folder=user.root/'sources';folder.mkdir()
        source=folder/'guide.md';source.write_text('# Guide\nOriginal telescope content.\n',encoding='utf-8')
        (folder/'ignored.bin').write_bytes(b'not a supported source')
        watched=await user.command(f'watch "{folder}"')
        assert watched['status']=='completed'
        first=await user.command('sync')
        assert first['data']['summary']['published_new']==1
        calls=len(user.model.calls)
        assert (await user.command('sync'))['stop_reason']=='no_change'
        assert len(user.model.calls)==calls
        source.write_text('# Guide\nReject updated telescope content.\n',encoding='utf-8')
        user.model.fail_text='Reject'
        failed=await user.command('sync')
        assert failed['status']=='partial'
        assert changed_paths(user.catalog,UUID(kb),[str(folder)])[0]==[str(source)]
        user.model.fail_text=None
        updated=await user.command('sync')
        assert updated['data']['summary']['published_updated']==1
        assert updated['data']['metrics']['written_vectors']==1
        added=folder/'new.txt';added.write_text('A new telescope document.',encoding='utf-8')
        assert (await user.command('sync'))['data']['summary']['published_new']==1
        source.unlink()
        assert (await user.command('sync'))['stop_reason']=='no_change'
        assert len((await user.command('sources'))['data'])==2
        assert len((await user.command('status'))['data']['watched_sources'])==1
        assert (await user.command(f'unwatch "{folder}"'))['data']['watched_sources']==[]
        assert subscriptions(user.catalog,UUID(kb))==[]
    asyncio.run(run())


def test_background_change_reconciliation_restart_and_stop(user):
    async def prepare():
        kb=(await user.command('create monitored'))['data']['kb_id']
        source=user.root/'live.md';source.write_text('# Live\nFirst telescope update.\n',encoding='utf-8')
        await user.command(f'watch "{source}"')
        return UUID(kb),source
    kb,source=asyncio.run(prepare())
    notices=[];published=threading.Event()
    def notify(result):
        notices.append(result)
        if result.get('data',{}).get('summary',{}).get('published_revision_id'):
            published.set()
    watcher=SourceWatcher(user.settings,notify=notify,interval=0.3,debounce=0.1).start()
    try:
        assert published.wait(15), (watcher.last_error,notices)
        first=user.catalog.get_library(kb).current_revision_id
        published.clear()
        # Multiple saves converge on the final bytes; unchanged scans stay quiet.
        source.write_text('# Live\nIntermediate telescope update.\n',encoding='utf-8')
        source.write_text('# Live\nFinal telescope update.\n',encoding='utf-8')
        assert published.wait(15)
        assert user.catalog.get_library(kb).current_revision_id!=first
    finally:
        watcher.stop();watcher.join(15)
    assert not watcher.thread.is_alive()
    count=len(user.model.calls)
    published.clear()
    restarted=SourceWatcher(user.settings,notify=notify,interval=0.2,debounce=0).start()
    try:
        assert not published.wait(0.5)
        assert len(user.model.calls)==count
    finally:
        restarted.stop();restarted.join(15)
    assert not restarted.thread.is_alive()


@pytest.mark.skipif(__import__('os').name!='nt',reason='Windows native notifications')
def test_native_notification_detects_save_and_atomic_replacement(tmp_path):
    source=tmp_path/'watched.md';source.write_text('original',encoding='utf-8')
    watcher=_Notifications()
    try:
        assert watcher.poll([str(source)])==set()
        for atomic in (False,True):
            if atomic:
                replacement=tmp_path/'temp.md';replacement.write_text('replacement',encoding='utf-8')
                replacement.replace(source)
            else:
                source.write_text('changed',encoding='utf-8')
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                if str(source) in watcher.poll([str(source)]):
                    break
                time.sleep(0.01)
            else:
                pytest.fail('native change notification was not delivered')
    finally:
        watcher.close()


def test_watch_rejects_feedback_into_knowledge_storage(user):
    async def run():
        await user.command('create no feedback')
        result=await user.command(f'watch "{user.root}"')
        assert result['status']=='failed'
    asyncio.run(run())


def test_reconciliation_continues_during_frequent_directory_notifications(user, monkeypatch):
    async def prepare():
        await user.command('create busy directory')
        folder = user.root / 'busy'; folder.mkdir()
        await user.command(f'watch "{folder}"')
    asyncio.run(prepare())
    scanned = threading.Event()
    monkeypatch.setattr(_Notifications, 'poll', lambda self, paths: set(paths))
    def reconcile(*args, **kwargs):
        scanned.set()
        return {'action': 'sync', 'status': 'completed', 'data': {}}
    monkeypatch.setattr('agentic_rag.ingestion.watching.synchronize', reconcile)
    watcher = SourceWatcher(user.settings, interval=0.3, debounce=0.6).start()
    try:
        assert scanned.wait(2), 'continuous directory events must not starve reconciliation'
    finally:
        watcher.stop(); watcher.join(5)
    assert not watcher.thread.is_alive()


def test_remote_run_synchronizes_and_drains_watcher_on_shutdown(user, monkeypatch):
    from codeplus.config import AppConfig, ProviderConfig
    from codeplus.remote import RemoteServer

    async def run():
        monkeypatch.chdir(user.root)
        kb = UUID((await user.command('create remote watched'))['data']['kb_id'])
        source = user.root / 'remote.md'
        source.write_text('# Remote\nOriginal telescope approval.\n', encoding='utf-8')
        await user.command(f'watch "{source}"')
        provider = ProviderConfig('fixture', 'openai-compat', 'https://fixture.invalid', 'fixture', 'synthetic')
        server = RemoteServer([provider], addr='127.0.0.1', port=0,
            config=AppConfig(providers=[provider], enable_fork=False, knowledge_development_config=str(user.path)))
        task = asyncio.create_task(server.run())
        watcher = None
        try:
            async with asyncio.timeout(20):
                while not getattr(server, 'last_knowledge_sync', None):
                    if task.done():
                        await task
                    await asyncio.sleep(0.05)
            watcher = server._knowledge_watcher
            assert server.last_knowledge_sync['status'] == 'completed'
            assert user.catalog.get_library(kb).current_revision_id is not None
        finally:
            task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 20)
            if server.agent:
                await server.agent.client._client.close()
        assert watcher is not None and not watcher.thread.is_alive()
        assert server._knowledge_watcher is None
    asyncio.run(run())
