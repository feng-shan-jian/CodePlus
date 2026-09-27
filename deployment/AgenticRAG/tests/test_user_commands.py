"""R21 real command handlers, SQLite/archives/owners; controlled model/index IO."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import runpy
import threading
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from codeplus.commands.handlers.knowledge import handle_knowledge, parse_knowledge, exit_status
from codeplus.memory.session import SessionManager
from agentic_rag.adapters.codeplus import management as C
from agentic_rag.domain import RagError, ErrorCode
from agentic_rag.models import FrozenTokenizer
from agentic_rag.storage import Catalog

H = runpy.run_path(str(Path(__file__).with_name('test_mutations.py')))
M = runpy.run_path(str(Path(__file__).with_name('test_run_modes.py')))
S = runpy.run_path(str(Path(__file__).with_name('test_model_switch.py')))


@pytest.fixture
def user(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path/'data')
    config = H['HELPER']['configuration'](tmp_path/'data')
    settings = M['settings'](config, tmp_path).model_copy(update={'knowledge': config})
    path = tmp_path/'development.json'
    path.write_text(settings.model_dump_json(), encoding='utf-8')
    model, backend = H['Model'](), H['backend'](catalog, config)
    monkeypatch.setattr(C, 'Catalog', lambda *_: catalog)
    closed = []
    @contextmanager
    def runtime(cat, conf, frozen):
        assert cat is catalog
        try:
            yield FrozenTokenizer(frozen.embedding, H['HELPER']['HELPER']['cache']()), model, backend
        finally:
            closed.append(True)
    monkeypatch.setattr(C, 'mutation_runtime', runtime)
    messages = []
    ui = SimpleNamespace(knowledge_feature_available=True, knowledge_library=None,
        knowledge_development_config=str(path), last_knowledge_outcome=None,
        last_knowledge_run_id=None, add_system_message=messages.append,
        session=SessionManager(str(tmp_path)).create())
    async def command(text):
        await handle_knowledge(SimpleNamespace(args=text, ui=ui))
        return ui.last_knowledge_command
    value = SimpleNamespace(root=tmp_path, catalog=catalog, config=config, settings=settings, path=path,
                            model=model, backend=backend, ui=ui, command=command, closed=closed)
    yield value
    ui.session.close()
    backend.close()


def test_full_commands_two_libraries_identity_retry_recovery_and_history(user):
    async def run():
        a = await user.command('create "中文 library A"'); one = a['data']['kb_id']
        two = (await user.command('create B'))['data']['kb_id']
        assert one != two and user.ui.knowledge_library == two
        await user.command('use '+one)
        good, bad = user.root/'中文 source.md', user.root/'failed.md'
        good.write_text('# A\nOriginal blue telescope certificate.\n', encoding='utf-8')
        bad.write_text('# B\nReject this initial file.\n', encoding='utf-8')
        user.model.fail_text = 'Reject'
        result = await user.command(f'import "{good}" "{bad}"')
        assert result['status'] == 'partial'
        initial = result['data']['receipt']['revision_id']
        failed_batch = result['data']['summary']['batch_id']
        sources = (await user.command('sources'))['data']
        assert len(sources) == 1
        doc = sources[0]['document_id']; first_version = sources[0]['document_version_id']
        moved = user.root/'renamed 中文.md'
        moved.write_text('# A\nChanged red telescope certificate.\n', encoding='utf-8')
        updated = await user.command(f'reimport {doc} "{moved}"')
        assert updated['data']['summary']['published_updated'] == 1
        assert (await user.command('sources'))['data'][0]['document_id'] == doc
        bad.write_text('# B\nNow valid input.\n', encoding='utf-8')
        user.model.fail_text = None
        retry = await user.command('retry '+failed_batch)
        assert retry['status'] == 'failed' and retry['data']['error']['code'] == 'SOURCE_CHANGED'
        pending = str(user.catalog.get_library(UUID(one)).pending_mutation_id)
        wait = await user.command('recover '+pending)
        assert wait['status'] == 'waiting_confirmation' and wait['data']['expected']['batch_id'] == pending
        assert (await user.command(f'import "{moved}"'))['stop_reason'] == 'pending_recovery'
        # A different library remains usable while one has pending work.
        await user.command('use '+two)
        assert (await user.command(f'import "{moved}"'))['status'] == 'completed'
        assert (await user.command('abandon '+pending))['status'] == 'failed'
        await user.command('use '+one)
        assert (await user.command('abandon '+pending))['data']['state'] == 'ABANDONED'
        assert (await user.command('retry '+failed_batch+' --accept-input-changes'))['status'] == 'completed'
        removed = await user.command('remove '+doc)
        assert removed['data']['summary']['published_deleted'] == 1
        assert all(v['document_id'] != doc for v in (await user.command('sources'))['data'])
        historical = (await user.command('sources --revision '+initial))['data']
        assert historical[0]['document_version_id'] == first_version
        assert b'Original blue' in user.catalog.archives.read(user.catalog.get_version(UUID(first_version)).raw_hash)
        await user.command('off')
        assert user.ui.session.meta.rag_library_id is None
        listed = await user.command('status')
        assert len(listed['data']['libraries']) == 2
    asyncio.run(run())


def test_model_choices_need_explicit_matching_proposal_and_keep_original(user):
    async def run():
        kb = (await user.command('create model'))['data']['kb_id']
        path = user.root/'model.md'; path.write_text('# Model\nReject after original publication.\n')
        original = (await user.command(f'import "{path}"'))['data']['receipt']['revision_id']
        target = S['changed'](user.config)
        user.path.write_text(user.settings.model_copy(update={'knowledge': target}).model_dump_json())
        plan = (await user.command('model'))['data']; proposal = plan['proposal_id']
        assert plan['state'] == 'WAITING_CONFIRMATION'
        calls = len(user.model.calls)
        for text in ('model', 'model '+proposal, 'model '+proposal+' --choice retry'):
            assert (await user.command(text))['data']['state'] == 'WAITING_CONFIRMATION'
        assert len(user.model.calls) == calls
        user.model.fail_text = 'Reject'
        result = await user.command('model '+proposal+' --choice confirm')
        assert result['data']['state'] == 'WAITING_RECOVERY'
        calls = len(user.model.calls)
        assert (await user.command('model '+proposal+' --choice confirm'))['data']['attempts'] == 1
        assert len(user.model.calls) == calls
        assert (await user.command('model '+proposal+' --choice retry'))['data']['attempts'] == 2
        kept = await user.command('model '+proposal+' --choice keep_original')
        assert kept['data']['state'] == 'KEPT_ORIGINAL'
        assert str(user.catalog.get_library(UUID(kb)).current_revision_id) == original
        assert user.catalog.get_library(UUID(kb)).pending_mutation_id is None
        assert (await user.command('model '+proposal+' --choice confirm'))['data']['state'] == 'KEPT_ORIGINAL'
        # Keeping the current model permits normal imports using that identity.
        user.model.fail_text = None
        path.write_text('# Changed\nOriginal encoding remains in use.\n')
        assert (await user.command(f'import "{path}"'))['status'] == 'completed'
        status = (await user.command('status'))['data']
        assert status['model']['actual']['document_encoding_fingerprint'] != status['model']['desired']['document_encoding_fingerprint']
    asyncio.run(run())


@pytest.mark.parametrize('after_commit', [False, True])
def test_cancellation_drains_real_owner_and_preserves_publication_fact(user, monkeypatch, after_commit):
    reached, release = threading.Event(), threading.Event()
    original = C.build_changes
    def pause(*args, **kwargs):
        if not after_commit:
            def observer(stage, value):
                if stage == 'validated':
                    reached.set(); assert release.wait(10)
            kwargs['observer'] = observer
        result = original(*args, **kwargs)
        if after_commit:
            reached.set(); assert release.wait(10)
        return result
    monkeypatch.setattr(C, 'build_changes', pause)
    async def run():
        kb = UUID((await user.command('create cancel'))['data']['kb_id'])
        path = user.root/'cancel.md'; path.write_text('# Cancel\nTelescope certificate.\n')
        task = asyncio.create_task(user.command(f'import "{path}"'))
        assert await asyncio.to_thread(reached.wait, 10)
        try:
            task.cancel(); await asyncio.sleep(.03); task.cancel(); await asyncio.sleep(.03)
            assert not task.done() and user.ui._knowledge_active and not user.closed
            if not after_commit:
                with pytest.raises(RagError) as error: user.catalog.identify_interrupted(kb)
                assert error.value.error.code == ErrorCode.LIBRARY_BUSY
                competing = await C.execute_management(str(user.path), 'import', str(kb), arguments=(str(path),))
                assert competing['status'] == 'failed' and competing['stop_reason'] == 'library_busy'
                assert competing['data']['pending']['active'] is True
        finally:
            release.set()
        result = await asyncio.wait_for(task, 10)
        assert result['status'] == 'cancelled' and not user.ui._knowledge_active and user.closed
        library = user.catalog.get_library(kb)
        if after_commit:
            assert str(library.current_revision_id) == result['data']['receipt']['revision_id']
            assert library.pending_mutation_id is None and result['operation_status'] == 'completed'
        else:
            assert library.current_revision_id is None and library.pending_mutation_id
            assert result['data']['summary']['state'] == 'WAITING_RECOVERY'
            # Snapshot recovery is separate from research continuation.
            recovered = await user.command('recover '+str(library.pending_mutation_id)+' --choice continue')
            assert recovered['status'] == 'completed'
    asyncio.run(run())


@pytest.mark.parametrize('text', ['use wrong', 'remove', 'import', 'retry', 'recover --choice continue',
    'model --choice confirm', 'model '+str(uuid4())+' --choice yes', 'report task', 'ask --mode nope q', 'sources --revision bad'])
def test_invalid_selection_never_starts_work(user, text):
    result = asyncio.run(user.command(text))
    assert result['status'] == 'failed' and not user.model.calls and not user.catalog.list_libraries()


def test_windows_paths_and_exit_states():
    assert parse_knowledge('import "D:\\中文 目录\\a.md"')[1] == ('D:\\中文 目录\\a.md',)
    assert [exit_status(v) for v in ('completed', 'partial', 'incomplete', 'failed', 'cancelled', 'waiting_confirmation')] == [0, 2, 2, 1, 130, 3]


@pytest.mark.parametrize('choice', ['confirm', 'retry'])
def test_cancel_strict_rebuild_and_recovery_retains_original(user, monkeypatch, choice):
    async def run():
        kb = UUID((await user.command('create strict'))['data']['kb_id'])
        source = user.root/'strict.md'; source.write_text('# Strict\nBlock this model input.\n')
        original = (await user.command(f'import "{source}"'))['data']['receipt']['revision_id']
        target = S['changed'](user.config)
        user.path.write_text(user.settings.model_copy(update={'knowledge': target}).model_dump_json())
        proposal = (await user.command('model'))['data']['proposal_id']
        if choice == 'retry':
            user.model.fail_text = 'Block'
            assert (await user.command('model '+proposal+' --choice confirm'))['data']['state'] == 'WAITING_RECOVERY'
            user.model.fail_text = None
        reached, release = threading.Event(), threading.Event()
        embed = user.model.embed_documents
        def blocked(*args):
            reached.set(); assert release.wait(10)
            return embed(*args)
        monkeypatch.setattr(user.model, 'embed_documents', blocked)
        task = asyncio.create_task(user.command('model '+proposal+' --choice '+choice))
        assert await asyncio.to_thread(reached.wait, 10)
        try:
            busy = await C.execute_management(str(user.path), 'model', str(kb))
            assert busy['stop_reason'] == 'library_busy' and busy['data']['state'] == 'BUILDING'
            assert busy['data']['choices'] == []
            task.cancel(); await asyncio.sleep(.03)
            assert user.ui._knowledge_active and not task.done()
        finally:
            release.set()
        result = await task
        assert result['status'] == 'cancelled' and result['data']['state'] == 'WAITING_RECOVERY'
        assert str(user.catalog.get_library(kb).current_revision_id) == original
        assert (await user.command('model '+proposal+' --choice keep_original'))['data']['state'] == 'KEPT_ORIGINAL'
    asyncio.run(run())


@pytest.mark.parametrize('action', ['ask', 'report', 'continue'])
@pytest.mark.parametrize('body', ['/knowledge status', '/session new'])
def test_prompt_body_is_data_in_actual_main(tmp_path, monkeypatch, capsys, action, body):
    from codeplus.__main__ import _run_prompt
    from codeplus.config import AppConfig, ProviderConfig
    from codeplus.permissions import PermissionMode
    from codeplus.agent import StreamText, LoopComplete
    from codeplus.run_policy import RunOutcome
    from agentic_rag.adapters.codeplus import policy as P
    observed=[]
    async def run(self, conversation):
        observed.append(conversation.history[-1].content)
        self.last_run_outcome=RunOutcome('completed','finished',run_id=str(uuid4()))
        yield StreamText('plain answer')
        yield LoopComplete(1)
    monkeypatch.setattr('codeplus.agent.Agent.run',run)
    monkeypatch.setattr(P,'load_policy',lambda *args,**kwargs:object())
    monkeypatch.chdir(tmp_path)
    provider=ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic')
    provider.context_window=100000
    config=AppConfig(providers=[provider],enable_fork=False,knowledge_development_config=str(tmp_path/'config.json'))
    options = ('--output report.md ' if action=='report' else
               '--run '+str(uuid4())+' ' if action=='continue' else '')
    command=action+' '+options+body
    code=asyncio.run(_run_prompt(config,PermissionMode.BYPASS,None,'/knowledge '+command,
                                knowledge_library=str(uuid4())))
    assert code==0 and observed==[body]
