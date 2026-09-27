"""Model choices use real storage/publishing with controlled model/index IO."""

import asyncio
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, document_encoding_identity, resolve_run
from agentic_rag.domain import ErrorCode, RagError
from agentic_rag.ingestion import InputSelection, begin_changes, build_changes
from agentic_rag.ingestion.build import build_first_revision
from agentic_rag.model_switch import inspect_model_switch, apply_model_switch
from agentic_rag.models import FrozenTokenizer
from agentic_rag.retrieval.dense import DenseSearch
from agentic_rag.storage import Catalog

H = runpy.run_path(str(Path(__file__).with_name('test_mutations.py')))


def changed(config, *, embedding=True, rerank=False):
    data = config.model_dump(mode='json')
    if embedding:
        data['model_profiles'][0]['instruction'] = 'Retrieve passages for the requested scientific question'
    if rerank:
        data['model_profiles'][1]['instruction'] = 'Judge whether this passage answers the requested question'
    return KnowledgeConfig.model_validate_json(json.dumps(data))


@pytest.fixture
def s(tmp_path):
    config = H['HELPER']['configuration'](tmp_path/'data')
    catalog = Catalog(tmp_path/'data')
    result = SimpleNamespace(root=tmp_path, config=config, catalog=catalog,
        kb=catalog.create_library('switch').kb_id, model=H['Model'](),
        backend=H['backend'](catalog, config), tokenizer=H['HELPER']['HELPER']['tokenizer']())
    result.paths, result.initial = H['initial'](result)
    result.target = changed(config)
    result.runtime_calls = []
    def runtime(frozen):
        result.runtime_calls.append(frozen)
        return FrozenTokenizer(frozen.embedding, H['HELPER']['HELPER']['cache']()), result.model, result.backend
    result.runtime = runtime
    yield result
    result.backend.close()


def proposal(s, target=None):
    return inspect_model_switch(s.catalog, s.kb, target or s.target)


def choose(s, plan, choice=None, **kwargs):
    return apply_model_switch(s.catalog, s.kb, plan['proposal_id'], kwargs.pop('target', s.target),
                              choice=choice, runtime_factory=kwargs.pop('runtime_factory', s.runtime), **kwargs)


def test_inspection_and_empty_or_retry_input_never_authorize(s):
    before = len(s.model.calls)
    plan = proposal(s)
    assert plan['state'] == 'WAITING_CONFIRMATION'
    assert plan['scope'] == [{'kb_id':str(s.kb), 'name':'switch', 'base_revision_id':s.initial['receipt']['revision_id']}]
    assert plan['desired']['embedding']['name'] == plan['actual']['embedding']['name']
    assert plan['desired']['embedding']['dimension'] == plan['actual']['embedding']['dimension'] == 1024
    assert plan['desired']['document_encoding_fingerprint'] != plan['actual']['document_encoding_fingerprint']
    for choice in (None, '', 'retry'):
        assert choose(s, plan, choice)['state'] == 'WAITING_CONFIRMATION'
    assert proposal(s)['proposal_id'] == plan['proposal_id']
    assert len(s.model.calls) == before and not s.runtime_calls
    assert s.catalog.get_library(s.kb).pending_mutation_id is None
    # The unconfirmed proposal holds no modification lease.
    with H['begin'](s, (s.paths[0],)) as owner:
        assert H['run'](s, owner)['receipt'] is None


def test_base_change_invalidates_confirmation_without_runtime(s):
    plan = proposal(s)
    s.paths[0].write_text('# Updated\nChanged after the proposal.\n')
    with H['begin'](s, (s.paths[0],)) as owner:
        H['run'](s, owner)
    result = choose(s, plan, 'confirm')
    assert result['state'] == 'STALE_PROPOSAL' and not s.runtime_calls
    assert result['current_proposal']['proposal_id'] != plan['proposal_id']
    assert result['current_proposal']['scope'][0]['base_revision_id'] != plan['scope'][0]['base_revision_id']


def test_target_change_and_different_scope_cannot_reuse_confirmation(s):
    plan = proposal(s)
    data = s.target.model_dump(mode='json')
    data['model_profiles'][0]['instruction'] += ' with exact evidence'
    target = KnowledgeConfig.model_validate_json(json.dumps(data))
    result = choose(s, plan, 'confirm', target=target)
    assert result['state'] == 'STALE_PROPOSAL' and not s.runtime_calls
    other = s.catalog.create_library('other').kb_id
    with pytest.raises(RagError) as exc:
        apply_model_switch(s.catalog, other, plan['proposal_id'], target, choice='confirm', runtime_factory=s.runtime)
    assert exc.value.error.code in (ErrorCode.SCOPE_MISMATCH, ErrorCode.NOT_READY)
    assert s.catalog.get_library(other).pending_mutation_id is None


def test_archived_all_members_publish_and_old_run_first_search_keeps_old_profile(s):
    old = s.catalog.start_current_run(s.kb, s.target, 'qa')
    before = H['members'](s)
    for path in s.paths:
        path.unlink()
    plan = proposal(s)
    result = choose(s, plan, 'confirm')
    assert result['state'] == 'PUBLISHED', result
    assert set(H['members'](s)) == set(before)
    assert all(H['members'](s)[key] != value for key, value in before.items())
    assert result['build']['metrics']['documents'] == 3
    assert s.runtime_calls == [s.target]
    calls = []
    def query(item, profile, context):
        calls.append(profile.identity)
        return s.model.embed_documents((item,), profile, context)
    s.model.embed_query = query
    try:
        found = DenseSearch(s.catalog, old.run.run_id, s.model, s.backend).search('telescope')
        assert found['revision_id'] == s.initial['receipt']['revision_id']
        assert calls == [s.config.embedding.identity]
        with s.catalog.start_current_run(s.kb, s.target, 'qa') as new:
            assert new.run.revision_id == UUID(result['receipt']['revision_id'])
            DenseSearch(s.catalog, new.run.run_id, s.model, s.backend).search('telescope')
            assert calls[-1] == s.target.embedding.identity
    finally:
        old.close()
    assert s.target.embedding.instruction != s.config.embedding.instruction


def test_failure_retries_same_approved_batch_and_reuses_complete_document(s):
    plan = proposal(s)
    s.model.fail_text = 'legacy ocean'
    first = choose(s, plan, 'confirm')
    assert first['state'] == 'WAITING_RECOVERY' and first['attempts'] == 1
    batch_id = first['batch_id']
    count = len(s.model.calls)
    assert choose(s, plan)['attempts'] == 1
    assert choose(s, plan, 'confirm')['attempts'] == 1
    assert len(s.model.calls) == count
    second = choose(s, plan, 'retry')
    assert second['state'] == 'WAITING_RECOVERY' and second['batch_id'] == batch_id and second['attempts'] == 2
    assert s.catalog.get_library(s.kb).current_revision_id == UUID(s.initial['receipt']['revision_id'])
    assert len(second['errors']) == 2
    s.model.fail_text = None
    last = choose(s, plan, 'retry')
    assert last['state'] == 'PUBLISHED' and last['batch_id'] == batch_id and last['attempts'] == 3
    from collections import Counter
    counts = Counter(s.model.calls)
    assert counts[(s.paths[0].read_text(),)] == counts[(s.paths[2].read_text(),)] == 2
    assert counts[(s.paths[1].read_text(),)] == 4  # initial, two failed rebuild calls, success
    with s.catalog._db.transaction() as db:
        assert db.execute('SELECT count(*) FROM publications WHERE batch_id=?', (batch_id,)).fetchone() == (1,)


def test_keep_after_failure_restarts_without_reprompt_and_releases_dependencies(s):
    plan = proposal(s)
    def unavailable(_):
        raise RagError(ErrorCode.DEPENDENCY_UNAVAILABLE, 'frozen model unavailable', stage='test')
    result = choose(s, plan, 'confirm', runtime_factory=unavailable)
    assert result['state'] == 'WAITING_RECOVERY'
    with s.catalog._db.transaction() as db:
        assert db.execute('SELECT count(*) FROM revision_dependencies WHERE batch_id=?', (result['batch_id'],)).fetchone()[0] > 0
    kept = choose(s, plan, 'keep_original')
    assert kept['state'] == 'KEPT_ORIGINAL' and kept['actual']['embedding'] == s.config.embedding.model_dump(mode='json')
    restarted = Catalog(s.root/'data')
    assert inspect_model_switch(restarted, s.kb, s.target)['state'] == 'KEPT_ORIGINAL'
    assert restarted.get_library(s.kb).pending_mutation_id is None
    with restarted._db.transaction() as db:
        assert db.execute('SELECT count(*) FROM revision_dependencies WHERE batch_id=?', (result['batch_id'],)).fetchone() == (0,)
    with H['begin'](s, (s.paths[0],)) as owner:
        assert H['run'](s, owner)['receipt'] is None


def test_configuration_change_during_failed_rebuild_requires_new_choice(s):
    plan = proposal(s)
    result = choose(s, plan, 'confirm', runtime_factory=lambda _: (_ for _ in ()).throw(RuntimeError('load failed')))
    other = s.target.model_dump(mode='json')
    other['processing']['chunker']['max_tokens'] -= 1
    other = KnowledgeConfig.model_validate_json(json.dumps(other))
    stale = choose(s, plan, 'retry', target=other)
    assert stale['state'] == 'STALE_PROPOSAL' and stale['attempts'] == result['attempts']
    choose(s, plan, 'keep_original', target=other)
    assert proposal(s, other)['state'] == 'WAITING_CONFIRMATION'


def test_active_build_has_no_failure_choices_and_does_not_admit_retry(s):
    plan = proposal(s)
    observations = []
    def observe(stage, value):
        if stage == 'prepared':
            status = proposal(s)
            assert status['state'] == 'BUILDING' and status['choices'] == []
            observations.append(choose(s, plan, 'retry'))
    result = choose(s, plan, 'confirm', observer=observe)
    assert result['state'] == 'PUBLISHED'
    assert observations[0]['attempts'] == 1 and observations[0]['errors'] == []


def test_interrupted_runtime_admission_already_owns_complete_archived_batch(s):
    plan = proposal(s)
    class Interrupted(BaseException):
        pass
    def stop(_):
        active = proposal(s)
        assert active['state'] == 'BUILDING'
        assert len(s.catalog.get_input_items(UUID(active['batch_id']))) == 3
        raise Interrupted()
    with pytest.raises(Interrupted):
        choose(s, plan, 'confirm', runtime_factory=stop)
    reopened = proposal(s)
    assert reopened['state'] == 'WAITING_RECOVERY' and reopened['attempts'] == 1
    assert s.catalog.get_library(s.kb).pending_mutation_id == UUID(reopened['batch_id'])
    assert choose(s, plan, 'retry')['state'] == 'PUBLISHED'


def test_missing_archived_bytes_after_confirmation_are_recoverable_in_same_batch(s):
    plan = proposal(s)
    version = s.catalog.get_version(UUID(next(iter(H['members'](s).values()))))
    path = s.catalog.archives._path(version.raw_hash)
    backup = path.with_name(path.name+'.r16-test-backup')
    path.replace(backup)
    try:
        failed = choose(s, plan, 'confirm')
        assert failed['state'] == 'WAITING_RECOVERY' and failed['choices'] == ['retry', 'keep_original']
        assert s.catalog.get_library(s.kb).pending_mutation_id == UUID(failed['batch_id'])
    finally:
        backup.replace(path)
    resumed = choose(s, plan, 'retry')
    assert resumed['state'] == 'PUBLISHED' and resumed['batch_id'] == failed['batch_id']


def test_other_pending_mutation_blocks_confirmation_and_scope_is_one_library(s):
    plan = proposal(s)
    owner = H['begin'](s, (s.paths[0],))
    try:
        with pytest.raises(RagError) as exc:
            choose(s, plan, 'confirm')
        assert exc.value.error.code == ErrorCode.LIBRARY_BUSY
        assert not s.runtime_calls
        other = s.catalog.create_library('independent').kb_id
        with begin_changes(s.catalog, other, ProcessingSnapshot.capture(uuid4(), s.config),
                           (InputSelection(path=str(s.paths[0])),)) as independent:
            build_changes(s.catalog, independent, s.model, s.backend, s.tokenizer)
    finally:
        owner.abandon()


def test_empty_published_library_switches_without_encoding_then_accepts_new_import(s):
    ids = tuple(UUID(value) for value in H['members'](s))
    with H['begin'](s, delete_document_ids=ids) as owner:
        H['run'](s, owner)
    count = len(s.model.calls)
    result = choose(s, proposal(s), 'confirm')
    assert result['state'] == 'PUBLISHED', result
    assert result['build']['validation']['empty_revision'] and len(s.model.calls) == count
    with begin_changes(s.catalog, s.kb, ProcessingSnapshot.capture(uuid4(), s.target),
                       (InputSelection(path=str(s.paths[0])),)) as owner:
        build_changes(s.catalog, owner, s.model, s.backend, s.runtime(s.target)[0])
    assert len(H['members'](s)) == 1


def test_rerank_alone_changes_new_run_identity_without_rebuild(s):
    target = changed(s.config, embedding=False, rerank=True)
    with s.catalog.start_current_run(s.kb, s.config, 'qa') as old:
        plan = proposal(s, target)
        assert plan['state'] == 'CURRENT' and plan['proposal_id'] is None
        with s.catalog.start_current_run(s.kb, target, 'qa') as new:
            assert new.run.revision_id == old.run.revision_id
            assert new.run.resolved_config_hash != old.run.resolved_config_hash
            assert old.run.resolved_config.knowledge.reranker.identity == s.config.reranker.identity
            assert new.run.resolved_config.knowledge.reranker.identity == target.reranker.identity
            assert document_encoding_identity(target) == document_encoding_identity(s.config)
    assert not s.runtime_calls


def test_new_query_freezes_actual_encoder_but_latest_rerank_and_return_limits(s):
    data = changed(s.config, rerank=True).model_dump(mode='json')
    data['retrieval']['context_tokens'] += 100
    desired = KnowledgeConfig.model_validate_json(json.dumps(data))
    with s.catalog.start_current_run(s.kb, desired, 'qa') as lease:
        actual = lease.run.resolved_config
        assert actual.knowledge.embedding.identity == s.config.embedding.identity
        assert actual.knowledge.reranker.identity == desired.reranker.identity
        assert actual.retrieval.context_tokens == desired.retrieval.context_tokens
        def unavailable(item, profile, context):
            assert profile.identity == s.config.embedding.identity
            raise RagError(ErrorCode.DEPENDENCY_UNAVAILABLE, 'old encoder unavailable', stage='query')
        s.model.embed_query = unavailable
        with pytest.raises(RagError, match='old encoder unavailable'):
            DenseSearch(s.catalog, lease.run.run_id, s.model, s.backend).search('question')
    with pytest.raises(RagError, match='encoding differs'):
        s.catalog.start_run(s.kb, resolve_run(desired, 'qa'))


def test_public_first_import_contract_still_rejects_existing_library(s):
    from agentic_rag.ingestion import select_inputs, capture_inputs, process_inputs
    with s.catalog.begin_import(s.kb, ProcessingSnapshot.capture(uuid4(), s.target),
            select_inputs((InputSelection(path=str(s.paths[0])),))) as owner:
        capture_inputs(s.catalog, owner)
        process_inputs(s.catalog, owner, s.runtime(s.target)[0])
        with pytest.raises(RagError, match='new library'):
            build_first_revision(s.catalog, owner, s.model, s.backend)
        from agentic_rag.storage import publication
        with pytest.raises(RagError, match='approved model switch'):
            publication.register(s.catalog, owner)
        owner.abandon()


def test_normal_knowledge_policy_uses_current_encoding_and_exposes_proposal(s, monkeypatch):
    from codeplus.client import create_client
    from codeplus.config import ProviderConfig
    from codeplus.run_policy import HostRunContext, RunOutcome
    from agentic_rag.adapters.codeplus import policy
    from agentic_rag.config import WorkerExecutionConfig
    conf = policy.DevelopmentConfig(knowledge=s.target,
        worker=WorkerExecutionConfig(executable=str(s.root/'python'), model_cache=str(s.root/'cache'), runtime_dir=str(s.root/'worker')),
        cleanup_grace_ms=0)
    async def initialize(self):
        assert self.lease.run.resolved_config.knowledge.embedding.identity == s.config.embedding.identity
    monkeypatch.setattr(policy.KnowledgeScope, 'initialize', initialize)
    async def run():
        provider = ProviderConfig('fixture', 'openai-compat', 'https://fixture.invalid', 'fixture', 'synthetic')
        client = create_client(provider)
        adapter = policy.KnowledgePolicy(conf, s.kb, provider)
        context = HostRunContext('test', 'session', str(s.root), 'openai-compat', client, None, None)
        scope = await adapter.start(context)
        try:
            assert adapter.model_switch['state'] == 'WAITING_CONFIRMATION'
        finally:
            await scope.finish(RunOutcome('cancelled', 'user_cancelled'))
            await scope.aclose()
            await client.aclose()
    asyncio.run(run())
