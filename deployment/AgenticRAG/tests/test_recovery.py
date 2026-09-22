"""R14 durable protocol tests; controlled transports are not real IO evidence."""

from dataclasses import replace
import io
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest

from agentic_rag.config import KnowledgeConfig,ProcessingSnapshot
from agentic_rag.domain import ErrorCode,RagError
from agentic_rag.ingestion import (InputSelection,select_inputs,capture_inputs,process_inputs,
    begin_changes,build_changes,process_changes,inspect_recovery,continue_recovery,abandon_recovery,repair_missing_original)
from agentic_rag.ingestion.build import build_first_revision
from agentic_rag.ingestion.encoding import outcomes,read_encoded
from agentic_rag.storage import Catalog,OwnerToken,publication
from agentic_rag.storage import recovery

H=runpy.run_path(str(Path(__file__).with_name('test_mutations.py')))


@pytest.fixture
def s(tmp_path):
    catalog=Catalog(tmp_path/'data');config=H['HELPER']['configuration'](tmp_path/'data')
    return SimpleNamespace(root=tmp_path,catalog=catalog,config=config,kb=catalog.create_library('recovery').kb_id,
        model=H['Model'](),backend=H['backend'](catalog,config),tokenizer=H['HELPER']['HELPER']['tokenizer']())


def source(s,name='a.md',text='# A\nOriginal telescope approval.\n'):
    return H['source'](s,name,text)


def begin(s,paths,strict=False):
    selections=tuple(InputSelection(path=str(p)) for p in paths)
    snapshot=ProcessingSnapshot.capture(uuid4(),s.config)
    return s.catalog.begin_import(s.kb,snapshot,select_inputs(selections)) if strict else begin_changes(s.catalog,s.kb,snapshot,selections)


def expected(s,batch):
    plan=inspect_recovery(s.catalog,batch)
    return OwnerToken(**{k:UUID(v) if k!='owner_epoch' else v for k,v in plan['expected'].items()})


def resume(s,token,**kw):
    return continue_recovery(s.catalog,token,runtime_factory=lambda config:(s.tokenizer,s.model,s.backend),**kw)


@pytest.mark.parametrize('strict',[False,True])
def test_two_recovery_generations_reuse_complete_documents_without_reencoding(s,strict):
    a,b=source(s),source(s,'b.md','# B\nA second ocean document.\n')
    owner=begin(s,[a,b],strict)
    capture_inputs(s.catalog,owner);process_inputs(s.catalog,owner,s.tokenizer)
    def pause(stage,value):
        if stage=='created':raise RuntimeError('controlled service interruption')
    with pytest.raises(RuntimeError):
        if strict:build_first_revision(s.catalog,owner,s.model,s.backend,observer=pause)
        else:build_changes(s.catalog,owner,s.model,s.backend,s.tokenizer,observer=pause)
    batch,first=owner.token.batch_id,owner.token
    count=len(s.model.calls);assert count==2
    owner.close();a.write_text('new current bytes');b.unlink();source(s,'extra.md','Not in original range')
    token=expected(s,batch)
    with pytest.raises(RuntimeError):resume(s,token,observer=pause)
    token2=expected(s,batch);assert token2.owner_epoch>token.owner_epoch
    result=resume(s,token2)
    assert len(s.model.calls)==count and result['receipt']['batch_id']==str(batch)
    with s.catalog._db.transaction() as db:
        rows=db.execute('SELECT owner_epoch,revision_id,collection_name FROM index_artifacts WHERE batch_id=? ORDER BY owner_epoch',(str(batch),)).fetchall()
        assert len(rows)==3 and len({r[1] for r in rows})==3 and len({r[2] for r in rows})==3
        assert rows[0][0]==first.owner_epoch
        assert db.execute('SELECT count(*) FROM publications WHERE batch_id=?',(str(batch),)).fetchone()==(1,)
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_partial_uncommitted_document_is_recomputed_but_complete_document_is_not(s):
    a,b=source(s),source(s,'b.md','# B\nOcean approval.\n')
    owner=begin(s,[a,b]);seen=[]
    def pause(stage,value):
        if stage=='encoding_response':
            seen.append(value['item_id'])
            if len(seen)==2:raise RuntimeError('before second checkpoint commit')
    with pytest.raises(RuntimeError):process_changes(s.catalog,owner,s.tokenizer,s.model,observer=pause)
    batch=owner.token.batch_id;owner.close()
    assert len(outcomes(s.catalog,batch))==1
    resume(s,expected(s,batch))
    assert len(s.model.calls)==3


def test_original_config_and_manifest_are_displayed_and_passed_to_factory(s):
    path=source(s);owner=begin(s,[path]);capture_inputs(s.catalog,owner);batch=owner.token.batch_id;owner.close()
    current=s.config.model_dump(mode='json');current['processing']['chunker']['max_tokens']-=1
    current=KnowledgeConfig.model_validate_json(json.dumps(current))
    plan=inspect_recovery(s.catalog,batch,current_config=current)
    assert plan['default_differences'] and plan['input_manifest']['entries'][0]['requested_path']==str(path)
    assert plan['frozen_snapshot']['resolved_config']==s.config.model_dump(mode='json')
    with s.catalog._db.transaction() as db:assert db.execute('SELECT count(*) FROM recovery_plans').fetchone()==(1,)
    observed=[]
    def factory(config):
        observed.append(config);return s.tokenizer,s.model,s.backend
    continue_recovery(s.catalog,expected(s,batch),runtime_factory=factory)
    assert observed==[s.config] and current.processing.chunker.max_tokens!=s.config.processing.chunker.max_tokens


@pytest.mark.parametrize('strict',[False,True])
def test_uncommitted_snapshot_never_reads_current_path_and_strict_stays_strict(s,strict):
    path=source(s);owner=begin(s,[path],strict);batch=owner.token.batch_id;owner.close()
    path.write_text('changed latest source')
    token=expected(s,batch)
    plan=inspect_recovery(s.catalog,batch)
    assert plan['can_continue'] is (not strict)
    assert plan['execution_environment'].startswith('not_checked;')
    if strict:
        with pytest.raises(RagError):resume(s,token)
        assert s.catalog.get_library(s.kb).current_revision_id is None
        assert s.catalog.get_library(s.kb).pending_mutation_id==batch
    else:
        result=resume(s,token);assert result['receipt'] is None and result['summary']['failed']==1
        assert result['summary']['state']=='COMPLETED_NO_CHANGE'
    assert not s.model.calls
    assert s.catalog.get_input_items(batch)[0].raw is None


@pytest.mark.parametrize('damage',['missing_raw','corrupt_vectors','missing_parsed'])
def test_invalid_committed_checkpoint_fails_before_runtime_and_preserves_progress(s,damage):
    path=source(s);owner=begin(s,[path]);process_changes(s.catalog,owner,s.tokenizer,s.model)
    batch=owner.token.batch_id;owner.close();raw=s.catalog.get_input_items(batch)[0]
    if damage=='missing_raw':digest=raw.raw.sha256
    elif damage=='corrupt_vectors':digest=outcomes(s.catalog,batch)[str(raw.entry.item_id)]['encoded_hash']
    else:digest=s.catalog.get_version(next(iter_versions(s.catalog))).parsed_hash
    archive=s.catalog.archives.path_for(digest) if hasattr(s.catalog.archives,'path_for') else s.catalog.archives._path(digest)
    archive.chmod(0o600)
    if damage=='corrupt_vectors':archive.write_bytes(b'corrupt')
    else:archive.unlink()
    plan=inspect_recovery(s.catalog,batch);assert not plan['can_continue']
    token=expected(s,batch)
    with pytest.raises(RagError):continue_recovery(s.catalog,token,runtime_factory=lambda _:pytest.fail('must not load models'))
    assert len(outcomes(s.catalog,batch))==1 and s.catalog.get_library(s.kb).pending_mutation_id==batch


def iter_versions(catalog):
    with catalog._db.transaction() as db:return iter([UUID(r[0]) for r in db.execute('SELECT document_version_id FROM document_versions')])


def test_environment_unavailable_is_retryable_without_config_change(s):
    owner=begin(s,[source(s)]);capture_inputs(s.catalog,owner);batch=owner.token.batch_id;owner.close()
    def unavailable(_):raise RagError(ErrorCode.DEPENDENCY_UNAVAILABLE,'original model unavailable',stage='test')
    with pytest.raises(RagError):continue_recovery(s.catalog,expected(s,batch),runtime_factory=unavailable)
    assert s.catalog.get_batch(batch).state.value=='WAITING_RECOVERY'
    assert resume(s,expected(s,batch))['receipt']


@pytest.mark.parametrize('terminal_state',['published','no_change','abandoned'])
def test_terminal_replays_without_runtime_or_archive_even_after_new_publication(s,terminal_state):
    path=source(s);owner=begin(s,[path]);batch=owner.token.batch_id;token=owner.token
    if terminal_state=='abandoned':result=owner.abandon()
    elif terminal_state=='no_change':
        path.unlink();result=build_changes(s.catalog,owner,s.model,s.backend,s.tokenizer);owner.close()
    else:result=build_changes(s.catalog,owner,s.model,s.backend,s.tokenizer);owner.close()
    new=source(s,'new.md','# New\nNewer current publication.\n')
    with begin(s,[new]) as next_owner:new_result=build_changes(s.catalog,next_owner,s.model,s.backend,s.tokenizer)
    latest=s.catalog.get_library(s.kb).current_revision_id
    for action in ('continue','abandon'):
        replay=continue_recovery(s.catalog,token,runtime_factory=lambda _:pytest.fail('terminal runtime factory')) if action=='continue' else abandon_recovery(s.catalog,token)
        assert replay['receipt']==result['receipt'] and replay['summary']==result['summary']
        assert s.catalog.get_library(s.kb).current_revision_id==latest
        assert s.catalog.get_library(s.kb).pending_mutation_id is None


def test_terminal_no_change_build_retry_returns_same_completion(s):
    path=source(s);owner=begin(s,[path]);path.unlink()
    first=build_changes(s.catalog,owner,s.model,s.backend,s.tokenizer)
    second=build_changes(s.catalog,owner,None,None,None)
    assert first['summary']==second['summary'] and second['receipt'] is None
    owner.close()


def test_same_library_new_mutations_block_other_library_can_work(s):
    path=source(s);owner=begin(s,[path]);batch=owner.token.batch_id;owner.close()
    with pytest.raises(RagError) as error:begin(s,[path])
    assert error.value.error.code==ErrorCode.LIBRARY_BUSY
    with pytest.raises(RagError):s.catalog.begin_mutation(s.kb,ProcessingSnapshot.capture(uuid4(),s.config),'a'*64)
    other=s.catalog.create_library('independent').kb_id
    with begin_changes(s.catalog,other,ProcessingSnapshot.capture(uuid4(),s.config),(InputSelection(path=str(path)),)) as another:
        build_changes(s.catalog,another,s.model,s.backend,s.tokenizer)
    abandon_recovery(s.catalog,expected(s,batch))
    with begin(s,[path]) as after:after.abandon()


def test_stale_continue_abandon_cas_and_produced_by_rejected(s):
    owner=begin(s,[source(s)]);capture_inputs(s.catalog,owner);old=owner.token;batch=old.batch_id;owner.close()
    token=expected(s,batch)
    with s.catalog.resume_mutation(token) as renewed:
        with pytest.raises(RagError):s.catalog.resume_mutation(token)
        with pytest.raises(RagError):abandon_recovery(s.catalog,token)
        with pytest.raises(RagError):
            with s.catalog._owned(renewed,old):pass
    with pytest.raises(RagError):resume(s,token)
    abandon_recovery(s.catalog,expected(s,batch))


def test_missing_manifest_cannot_resume_or_invent_range(s):
    owner=s.catalog.begin_mutation(s.kb,ProcessingSnapshot.capture(uuid4(),s.config),'a'*64)
    batch=owner.token.batch_id;owner.close();plan=inspect_recovery(s.catalog,batch)
    assert not plan['can_continue'] and 'manifest' in plan['errors'][0]
    with pytest.raises(RagError):continue_recovery(s.catalog,expected(s,batch),runtime_factory=lambda _:pytest.fail('no manifest'))
    abandon_recovery(s.catalog,expected(s,batch))


def test_exact_missing_original_repair_rejects_current_changed_bytes(s):
    path=source(s);original=path.read_bytes();owner=begin(s,[path]);capture_inputs(s.catalog,owner)
    batch=owner.token.batch_id;owner.close();raw=s.catalog.get_input_items(batch)[0]
    archive=s.catalog.archives._path(raw.raw.sha256);archive.chmod(0o600);archive.unlink()
    with pytest.raises(RagError):repair_missing_original(s.catalog,batch,raw.entry.item_id,b'changed')
    assert repair_missing_original(s.catalog,batch,raw.entry.item_id,original)==raw.raw.sha256
    assert resume(s,expected(s,batch))['receipt']


def test_abandon_preserves_archives_and_inflight_cleanup_dependency(s):
    owner=begin(s,[source(s)]);process_changes(s.catalog,owner,s.tokenizer,s.model)
    _,artifact=publication.register(s.catalog,owner)
    identity=recovery.start_io(s.catalog,owner,'milvus_insert',artifact_id=artifact['artifact_id'])
    batch=owner.token.batch_id;owner.close()
    token=expected(s,batch);abandon_recovery(s.catalog,token)
    retained=recovery.cleanup_candidates(s.catalog,batch,s.backend)
    assert retained[0]['state']=='retained' and retained[0]['pending_io'][0]['io_id']==str(identity)
    assert read_encoded(s.catalog,batch,s.catalog.get_input_items(batch)[0])
    recovery.observe_io(s.catalog,identity,finished=True)
    assert recovery.unresolved_io(s.catalog,batch)==[]


def test_known_synchronous_input_rejection_finishes_io_receipt(s):
    s.model.fail_text='Original'
    with begin(s,[source(s)]) as owner:
        result=build_changes(s.catalog,owner,s.model,s.backend,s.tokenizer)
    assert result['summary']['failed']==1 and recovery.unresolved_io(s.catalog,owner.token.batch_id)==[]


@pytest.mark.parametrize('damage',['config','raw','parsed','chunk_set','chunk_order','vector_digest','vector_dimension'])
def test_content_addressed_but_wrong_encoding_checkpoint_is_rejected(s,damage):
    owner=begin(s,[source(s,text='# A\n'+('Original telescope approval. '*900))])
    process_changes(s.catalog,owner,s.tokenizer,s.model)
    batch=owner.token.batch_id;owner.close();raw=s.catalog.get_input_items(batch)[0]
    original=outcomes(s.catalog,batch)[str(raw.entry.item_id)]
    payload=json.loads(s.catalog.archives.read(original['encoded_hash']))
    assert len(payload['vectors'])>1
    if damage=='config':payload['binding']['config_fingerprint']='a'*64
    elif damage=='raw':payload['binding']['raw_hash']='a'*64
    elif damage=='parsed':payload['binding']['artifacts'][0]['sha256']='a'*64
    elif damage=='chunk_set':payload['vectors']=payload['vectors'][:-1]
    elif damage=='chunk_order':payload['vectors'].reverse()
    elif damage=='vector_digest':payload['vectors'][0]['vector_hash']='a'*64
    else:payload['vectors'][0]['dense'].pop()
    # A correct archive hash alone must not bless another configuration,
    # processing result, partial/reordered document or altered float32 vector.
    forged=s.catalog.archives.put(io.BytesIO(json.dumps(payload).encode()))
    with pytest.raises(RagError) as error:
        read_encoded(s.catalog,batch,raw,{**original,'encoded_hash':forged.sha256})
    assert error.value.error.code==ErrorCode.CHECKPOINT_INVALID
    assert read_encoded(s.catalog,batch,raw)
