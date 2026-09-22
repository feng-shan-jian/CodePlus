"""Private build reuse cannot replace public or final publication validation."""

from pathlib import Path
import runpy
from uuid import UUID, uuid4

import pytest

from agentic_rag.config import ProcessingSnapshot
from agentic_rag.domain import RagError
from agentic_rag.ingestion import InputSelection, begin_changes, process_changes
from agentic_rag.ingestion.mutations import _BuildPreparation, prepare_changes
from agentic_rag.indexes.manifest import vector_hash
from agentic_rag.storage import Catalog, publication

H = runpy.run_path(str(Path(__file__).with_name('test_mutations.py')))
setup = H['setup']


def processed(s):
    path = H['source'](s, 'new.md', '# New\nA complete telescope document.\n')
    owner = H['begin'](s, (path,))
    try:
        process_changes(s.catalog, owner, s.tokenizer, s.model)
    except BaseException:
        owner.close()
        raise
    return owner


def assert_no_candidate(s, owner):
    with s.catalog._db.transaction() as db:
        assert db.execute('SELECT count(*) FROM index_artifacts WHERE batch_id=?',
                          (str(owner.token.batch_id),)).fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM publications WHERE batch_id=?',
                          (str(owner.token.batch_id),)).fetchone() == (0,)


def test_preparation_rejects_values_subclasses_foreign_identity_and_expired_scope(setup):
    s = setup
    with processed(s) as owner:
        revision = uuid4()
        candidate = prepare_changes(s.catalog, owner.token.batch_id, revision)
        class Unverified(_BuildPreparation):
            pass
        for unverified in (candidate, object(), object.__new__(Unverified)):
            with pytest.raises(RagError, match='internally verified'):
                publication._register(s.catalog, owner, revision, _operation=unverified)
        with _BuildPreparation(s.catalog, owner, revision) as scope:
            with pytest.raises(RagError, match='different operation'):
                publication._register(s.catalog, owner, uuid4(), _operation=scope)
            with pytest.raises(RagError, match='different operation'):
                publication._register(Catalog(s.root / 'data'), owner, revision, _operation=scope)
            other_kb = s.catalog.create_library('same epoch other library').kb_id
            with begin_changes(s.catalog, other_kb, ProcessingSnapshot.capture(uuid4(),s.config),
                               (InputSelection(path=str(s.root / 'new.md')),)) as other:
                assert other.token.owner_epoch == owner.token.owner_epoch
                with pytest.raises(RagError, match='different operation'):
                    publication._register(s.catalog, other, revision, _operation=scope)
                assert_no_candidate(s, other)
                other.abandon()
        with pytest.raises(RagError, match='different operation'):
            publication._register(s.catalog, owner, revision, _operation=scope)
        with pytest.raises(RagError, match='cannot be reused'):
            with scope:
                pass
        assert_no_candidate(s, owner)


@pytest.mark.parametrize('damage', ['row', 'nested_spec', 'dense', 'dense_and_hash'])
def test_mutated_candidate_cannot_be_registered(setup, damage):
    s = setup
    with processed(s) as owner:
        revision = uuid4()
        with _BuildPreparation(s.catalog, owner, revision) as scope:
            if damage == 'row':
                scope.candidate.rows[0]['text'] += ' changed'
            elif damage == 'nested_spec':
                scope.candidate.spec['uuid_fields'].reverse()
            else:
                value = scope.candidate.encoded_vectors[0]
                value['dense'][:] = [0.0,1.0] + [0.0]*1022
                if damage == 'dense_and_hash':
                    value['vector_hash'] = vector_hash(value['dense'])
            with pytest.raises(RagError):
                publication._register(s.catalog, owner, revision, _operation=scope)
        assert_no_candidate(s, owner)


def test_previous_owner_cannot_reuse_preparation_after_manual_takeover(setup):
    s = setup
    owner = processed(s)
    revision = uuid4()
    with _BuildPreparation(s.catalog, owner, revision) as scope:
        owner.close()
        expected = s.catalog.identify_interrupted(s.kb)
        with s.catalog.resume_mutation(expected) as resumed:
            assert resumed.token.owner_epoch > owner.token.owner_epoch
            for caller in (owner, resumed):
                with pytest.raises(RagError):
                    publication._register(s.catalog, caller, revision, _operation=scope)
            assert_no_candidate(s, resumed)


@pytest.mark.parametrize('damage', ['artifact', 'expected_rows'])
def test_base_material_mutation_is_rejected_before_candidate_write(setup, damage):
    s = setup
    H['initial'](s)
    with processed(s) as owner:
        revision = uuid4()
        with _BuildPreparation(s.catalog, owner, revision) as scope:
            if damage == 'artifact':
                scope.base_artifact['collection_name'] += '_changed'
            else:
                scope.base_expected[0]['text'] += ' changed'
            with pytest.raises(RagError, match='base material changed'):
                publication._register(s.catalog, owner, revision, _operation=scope)
        assert_no_candidate(s, owner)


@pytest.mark.parametrize('damage', ['base_archive', 'new_archive'])
def test_final_validation_rereads_archives_after_reuse_and_retains_old_pointer(setup, damage):
    s = setup
    (a,b,c), old = H['initial'](s)
    old_revision = UUID(old['receipt']['revision_id'])
    base_version = UUID(next(iter(H['members'](s).values())))
    a.write_text('# A\nChanged telescope approval.\n', encoding='utf-8')
    with H['begin'](s, (a,)) as owner:
        def corrupt(stage, _):
            if stage != 'inserted':
                return
            if damage == 'base_archive':
                digest = s.catalog.get_version(base_version).raw_hash
            else:
                digest = s.catalog.get_input_items(owner.token.batch_id)[0].raw.sha256
            s.catalog.archives._path(digest).write_bytes(b'controlled post-prepare damage')
        with pytest.raises(RagError):
            H['run'](s, owner, observer=corrupt)
        assert publication.receipt(s.catalog, owner.token.batch_id) is None
        assert s.catalog.get_library(s.kb).current_revision_id == old_revision
        assert s.catalog.get_library(s.kb).pending_mutation_id == owner.token.batch_id


@pytest.mark.parametrize('boundary', ['register', 'record_encoded', 'complete_no_change'])
def test_public_boundaries_always_rederive_archives(setup, boundary):
    s = setup
    (a,b,c), old = H['initial'](s)
    if boundary != 'complete_no_change':
        a.write_text('# A\nPublic boundary changed content.\n', encoding='utf-8')
    with H['begin'](s, (a,)) as owner:
        process_changes(s.catalog, owner, s.tokenizer, s.model)
        revision = uuid4()
        candidate = prepare_changes(s.catalog, owner.token.batch_id, revision)
        expected = []
        if boundary == 'record_encoded':
            publication.register(s.catalog, owner, revision)
            expected = [{**row,'vector_hash':vector_hash(H['VECTOR'])} for row in candidate.rows]
        base_version = UUID(next(iter(H['members'](s).values())))
        digest = s.catalog.get_version(base_version).raw_hash
        s.catalog.archives._path(digest).write_bytes(b'corruption before independent call')
        with pytest.raises(RagError, match='archive'):
            if boundary == 'register':
                publication.register(s.catalog, owner, revision)
            elif boundary == 'record_encoded':
                publication.record_encoded(s.catalog, owner, revision, expected)
            else:
                publication.complete_no_change(s.catalog, owner)
        assert publication.receipt(s.catalog, owner.token.batch_id) is None
        assert s.catalog.get_library(s.kb).current_revision_id == UUID(old['receipt']['revision_id'])
