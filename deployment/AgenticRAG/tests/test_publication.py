"""Publication contracts; mocked database transport is distinct from real acceptance."""

import copy
import json
from pathlib import Path
import runpy
from uuid import uuid4

import pytest

from agentic_rag.domain import RagError
from agentic_rag.indexes.manifest import text_for_index, TEXT_MAX_BYTES, vector_hash
from agentic_rag.storage import Catalog, publication

H = runpy.run_path(str(Path(__file__).with_name('publication_support.py')))


def test_publication_receipt_atomic_terminal_close_and_old_retry_after_newer(tmp_path):
    catalog = Catalog(tmp_path/'data')
    owner, config = H['processed'](catalog, tmp_path/'doc.md')
    first, _, _, _, _ = H['unit_ready'](catalog, owner, config)
    old = publication.publish(catalog, owner, first.revision_id)
    assert publication.publish(catalog, owner, first.revision_id) == old
    assert catalog.get_library(owner.token.kb_id).current_revision_id == first.revision_id
    assert catalog.get_library(owner.token.kb_id).pending_mutation_id is None
    owner.close()
    second_owner, config2 = H['processed'](catalog, tmp_path/'doc.md', kb_id=owner.token.kb_id, text='# New\nChanged immutable telescope source.\n')
    second, _, _, _, _ = H['unit_ready'](catalog, second_owner, config2)
    publication.publish(catalog, second_owner, second.revision_id)
    second_owner.close()
    assert publication.publish(catalog, owner, first.revision_id) == old
    assert catalog.get_library(owner.token.kb_id).current_revision_id == second.revision_id
    with pytest.raises(RagError): publication.publish(catalog, owner, second.revision_id)


def test_different_libraries_same_epoch_cannot_modify_artifacts(tmp_path):
    catalog = Catalog(tmp_path/'data')
    a, ac = H['processed'](catalog, tmp_path/'a.md')
    b, bc = H['processed'](catalog, tmp_path/'b.md')
    assert a.token.owner_epoch == b.token.owner_epoch
    ap, aa = publication.register(catalog, a)
    expected = [{**r,'vector_hash':vector_hash(H['VECTOR'])} for r in ap.rows]
    before = publication.artifact(catalog, ap.revision_id)
    with pytest.raises(RagError): publication.record_encoded(catalog,b,ap.revision_id,expected)
    with pytest.raises(RagError): publication.validate(catalog,b,ap.revision_id,H['unit_backend'](catalog,bc,expected))
    with pytest.raises(RagError): H['unit_backend'](catalog,bc,expected)._writable(aa,b)
    assert publication.artifact(catalog,ap.revision_id) == before
    assert catalog.get_batch(b.token.batch_id).state.value == 'PROCESSING'
    a.abandon(); b.abandon()


def test_failed_transaction_does_not_publish_any_part(tmp_path):
    catalog = Catalog(tmp_path/'data')
    owner, config = H['processed'](catalog,tmp_path/'a.md')
    p, _, _, _, _ = H['unit_ready'](catalog,owner,config)
    with catalog._db.transaction(write=True) as connection:
        connection.execute("CREATE TRIGGER test_abort_pointer BEFORE UPDATE OF current_revision_id ON libraries BEGIN SELECT RAISE(ABORT,'injected commit failure'); END")
    with pytest.raises(RagError): publication.publish(catalog,owner,p.revision_id)
    assert publication.receipt(catalog,owner.token.batch_id) is None
    assert catalog.get_library(owner.token.kb_id).current_revision_id is None
    assert catalog.get_batch(owner.token.batch_id).state.value == 'READY'
    owner.abandon()


def test_terminal_close_rejects_mismatched_owner(tmp_path):
    from dataclasses import replace
    catalog = Catalog(tmp_path/'data')
    owner, config = H['processed'](catalog,tmp_path/'a.md')
    p, _, _, backend, _ = H['unit_ready'](catalog,owner,config)
    publication.publish(catalog,owner,p.revision_id)
    with pytest.raises(RagError): backend._writable(publication.artifact(catalog,p.revision_id),owner)
    owner._token = replace(owner.token, owner_nonce=uuid4())
    with pytest.raises(RagError): owner.close()


def test_validate_derives_checkpoints_and_rejects_arbitrary_boolean(tmp_path):
    catalog=Catalog(tmp_path/'data')
    owner,config=H['processed'](catalog,tmp_path/'a.md')
    p,a,expected=H['synthetic_encoded'](catalog,owner)
    class Forged:
        def validate(self,*args): return {'passed':True}
    with pytest.raises(RagError): publication.validate(catalog,owner,p.revision_id,Forged())
    from agentic_rag.indexes.milvus import MilvusRevisionIndex
    class Subclass(MilvusRevisionIndex):
        def validate(self,*args): return {}
    forged=object.__new__(Subclass);forged.catalog=catalog;forged.storage=config.storage
    with pytest.raises(RagError): publication.validate(catalog,owner,p.revision_id,forged)
    corrupted=copy.deepcopy(expected); corrupted[0]['body_hash']='f'*64
    with pytest.raises(RagError): publication.validate(catalog,owner,p.revision_id,H['unit_backend'](catalog,config,corrupted))
    assert catalog.get_library(owner.token.kb_id).current_revision_id is None
    owner.abandon()


def test_register_rolls_back_all_parts_and_same_request_is_idempotent(tmp_path):
    catalog=Catalog(tmp_path/'data');owner,config=H['processed'](catalog,tmp_path/'a.md')
    revision=uuid4()
    with catalog._db.transaction(write=True) as connection:
        connection.execute("CREATE TRIGGER test_register_failure BEFORE INSERT ON index_artifacts BEGIN SELECT RAISE(ABORT,'artifact fault'); END")
    with pytest.raises(RagError): publication.register(catalog,owner,revision)
    with catalog._db.transaction(write=True) as connection:
        assert connection.execute('SELECT count(*) FROM revisions').fetchone()==(0,)
        assert connection.execute('SELECT count(*) FROM revision_members').fetchone()==(0,)
        connection.execute('DROP TRIGGER test_register_failure')
    first=publication.register(catalog,owner,revision)
    assert publication.register(catalog,owner,revision)==first
    assert publication.register(catalog,owner)==first
    with pytest.raises(RagError):publication.register(catalog,owner,uuid4())
    owner.abandon()


def test_full_iterator_exceeds_3000_and_checks_every_vector(tmp_path):
    catalog=Catalog(tmp_path/'data')
    owner,config=H['processed'](catalog,tmp_path/'a.md')
    p,a,seed=H['synthetic_encoded'](catalog,owner)
    rows=[{**seed[0], 'chunk_id':str(uuid4())} for _ in range(4041)]
    backend=H['unit_backend'](catalog,config,rows)
    assert backend.validate(a,rows)['rows_checked']==4041
    expected=copy.deepcopy(rows); expected[-1]['vector_hash']='0'*64
    with pytest.raises(RagError): backend.validate(a,expected)
    owner.abandon()


@pytest.mark.parametrize('title,body', [('', ' '*TEXT_MAX_BYTES+'x'), ('标题', 'a'*(TEXT_MAX_BYTES-6)), ('', '🫨'*(TEXT_MAX_BYTES//4+1))], ids=['whitespace','heading','unicode'])
def test_utf8_limit_never_truncates(title,body):
    with pytest.raises(RagError,match='UTF-8 bytes'): text_for_index(title,body)


def test_utf8_boundary_and_vector_nonfinite():
    body='🫨'*(TEXT_MAX_BYTES//4)+'abc'
    assert len(text_for_index('',body).encode())==TEXT_MAX_BYTES
    with pytest.raises(RagError): vector_hash([float('nan')]+H['VECTOR'][1:])
    with pytest.raises(RagError): vector_hash([0.0]*1024)


def test_fixed_run_keeps_version_source_after_unpublished_rename_and_usage(tmp_path):
    from agentic_rag.config import resolve_run,ProcessingSnapshot
    from agentic_rag.ingestion import InputSelection,select_inputs,capture_inputs
    from uuid import UUID
    from agentic_rag.capabilities import EmbeddingResponse,EmbeddingResult,ModelTimings
    from agentic_rag.retrieval import DenseSearch
    catalog=Catalog(tmp_path/'data');owner,config=H['processed'](catalog,tmp_path/'old-name.md')
    p,a,expected,backend,_=H['unit_ready'](catalog,owner,config)
    publication.publish(catalog,owner,p.revision_id);owner.close()
    class Provider:
        owner_id=uuid4()
        def embed_query(self,item,profile,context):
            return EmbeddingResponse(request_id=context.request_id,profile_fingerprint=profile.identity,
                results=(EmbeddingResult(item_id=item.item_id,vector=tuple(H['VECTOR']),input_tokens=9),),
                timings=ModelTimings(queue_ms=0,load_ms=0,inference_ms=0))
    with catalog.start_run(owner.token.kb_id,resolve_run(config,'qa')) as lease:
        search=DenseSearch(catalog,lease.run.run_id,Provider(),backend)
        before=search.search('immutable')['hits'][0]
        assert before['source_name']=='old-name.md'
        renamed=tmp_path/'new-name.md';renamed.write_text('Unpublished different source.',encoding='utf-8')
        manifest=select_inputs((InputSelection(path=str(renamed),document_id=UUID(before['document_id'])),))
        with catalog.begin_import(owner.token.kb_id,ProcessingSnapshot.capture(uuid4(),config),manifest) as pending:
            capture_inputs(catalog,pending)
            assert catalog.get_document(UUID(before['document_id'])).original_name=='new-name.md'
            with catalog._db.transaction(write=True) as connection:
                usage=lease.run.usage.model_copy(update={'searches':1})
                connection.execute('UPDATE runs SET usage=? WHERE run_id=?',(usage.model_dump_json(),str(lease.run.run_id)))
            (tmp_path/'old-name.md').unlink()
            after=search.search('immutable')['hits'][0]
            assert after==before
            pending.abandon()
        backend.search=lambda *args,**kwargs: (_ for _ in ()).throw(ConnectionError('actual transport is unavailable'))
        with pytest.raises(ConnectionError):search.search('immutable')
