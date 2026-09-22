"""R13 protocol/failure tests. Model and service transports are controlled here.

Real GPU/Milvus and independently pinned processes are a separate opt-in driver.
"""

import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from agentic_rag.capabilities import EmbeddingResponse, EmbeddingResult, ModelTimings
from agentic_rag.config import ProcessingSnapshot
from agentic_rag.domain import ErrorCode, RagError
from agentic_rag.ingestion import (InputSelection, begin_changes, build_changes, process_changes,
                                 retry_failed, mutation_summary)
from agentic_rag.indexes.manifest import prepare, SCALAR_FIELDS
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.storage import Catalog, publication

HELPER = runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
VECTOR = HELPER['VECTOR']


class Model:
    owner_id = uuid4()
    def __init__(self):
        self.calls = []
        self.fail_text = None
        self.failure = ErrorCode.INVALID_INPUT
    def embed_documents(self, items, profile, context):
        self.calls.append(tuple(i.text for i in items))
        if self.fail_text and any(self.fail_text in i.text for i in items):
            raise RagError(self.failure, 'controlled model input/service failure', stage='test_model')
        return EmbeddingResponse(request_id=context.request_id, profile_fingerprint=profile.identity,
            results=tuple(EmbeddingResult(item_id=i.item_id,vector=tuple(VECTOR),input_tokens=1) for i in items),
            timings=ModelTimings(queue_ms=0,load_ms=0,inference_ms=0))


def backend(catalog, config):
    value = object.__new__(MilvusRevisionIndex)
    value.catalog,value.storage,value.store_id = catalog,config.storage,catalog.store_id
    value.server_version,value.timeout = '3.0.1',1
    value.collections = {}
    value.create = lambda artifact,owner: value.collections.__setitem__(artifact['collection_name'],[])
    value.insert = lambda artifact,rows,owner: value.collections[artifact['collection_name']].extend(rows)
    value.finalize = lambda *a,**kw: {}
    value.inspect = lambda artifact,count: {'controlled_transport':True,'count':count}
    def search(artifact, query, **kw):
        return [{'id':r['chunk_id'],'distance':1.0,'entity':{k:r[k] for k in SCALAR_FIELDS}}
                for r in value.collections[artifact['collection_name']]][:kw.get('limit',10)]
    value.search = search
    class Iterator:
        def __init__(self, rows): self.rows = rows
        def next(self):
            result,self.rows = self.rows,[]
            return result
        def close(self): pass
    value.client = SimpleNamespace(query_iterator=lambda name,**kw: Iterator(value.collections[name]),
                                   run_analyzer=lambda **kw: [SimpleNamespace(tokens=['test'])])
    return value


@pytest.fixture
def setup(tmp_path):
    catalog = Catalog(tmp_path/'data')
    config = HELPER['configuration'](tmp_path/'data')
    kb = catalog.create_library('ordinary changes').kb_id
    model = Model()
    store = backend(catalog,config)
    tokenizer = HELPER['HELPER']['tokenizer']()
    return SimpleNamespace(catalog=catalog,config=config,kb=kb,model=model,backend=store,tokenizer=tokenizer,root=tmp_path)


def source(s, name, text):
    path = s.root/name
    path.write_text(text,encoding='utf-8')
    return path


def begin(s, paths=(), **kw):
    selections = tuple(p if isinstance(p,InputSelection) else InputSelection(path=str(p)) for p in paths)
    return begin_changes(s.catalog,s.kb,ProcessingSnapshot.capture(uuid4(),s.config),selections,**kw)


def run(s, owner, **kw):
    return build_changes(s.catalog,owner,s.model,s.backend,s.tokenizer,**kw)


def members(s):
    revision = s.catalog.get_library(s.kb).current_revision_id
    with s.catalog._db.transaction() as db:
        return dict(db.execute('SELECT document_id,document_version_id FROM revision_members WHERE revision_id=?',(str(revision),)))


def initial(s):
    a = source(s,'a.md','# A\nThe old telescope approval is blue.\n')
    b = source(s,'b.md','# B\nThe legacy ocean approval is green.\n')
    c = source(s,'c.md','# C\nThe unchanged mountain report is violet.\n')
    with begin(s,(a,b,c)) as owner:
        result = run(s,owner)
    return (a,b,c),result


def test_mixed_batch_complete_candidate_path_identity_and_reuse(setup):
    s=setup; (a,b,c),old=initial(s); before=members(s)
    a.write_text('# A\nNew telescope approval is red.\n',encoding='utf-8')
    b.write_bytes(b'\xffinvalid UTF-8')
    d=source(s,'d.md','# D\nNew island report.\n')
    bad=source(s,'bad.md','# E\nReject this document.\n')
    s.model.fail_text='Reject'
    before_calls=len(s.model.calls)
    with begin(s,(a,b,c,d,bad)) as owner:
        def observe(stage,value):
            assert s.catalog.get_library(s.kb).current_revision_id == UUID(old['receipt']['revision_id'])
        result=run(s,owner,observer=observe)
        assert result['summary']['published_new']==1
        assert result['summary']['published_updated']==1
        assert result['summary']['failed']==2
        assert result['summary']['unchanged']==1
        assert result['metrics']['documents']==4
        assert result['metrics']['reused_vectors']==2
        assert len(s.model.calls)-before_calls==3
        assert len(set(members(s)) & set(before))==3
        assert sum(members(s)[key]==value for key,value in before.items())==2
        # Stable full candidate on every prepare invocation after publication.
        assert prepare(s.catalog,owner.token.batch_id,UUID(result['receipt']['revision_id'])).manifest_hash==result['receipt']['manifest_hash']
    assert len(s.backend.collections)==2


@pytest.mark.parametrize('kind',['unchanged','parse_failed','capture_failed','empty_directory'])
def test_normal_no_change_releases_owner_without_artifact(setup,kind,monkeypatch):
    s=setup; (a,b,c),old=initial(s)
    if kind=='parse_failed': a.write_bytes(b'\xff')
    if kind=='capture_failed': a.unlink()
    if kind=='empty_directory':
        a=s.root/'empty';a.mkdir()
    if kind=='unchanged':
        monkeypatch.setattr('agentic_rag.ingestion.processing.parse_document',lambda *a: pytest.fail('unchanged reparsed'))
    calls=len(s.model.calls)
    with begin(s,(a,)) as owner:
        result=run(s,owner)
        assert result['summary']['state']=='COMPLETED_NO_CHANGE'
        batch=owner.token.batch_id
    assert s.catalog.get_batch(batch).state.value=='COMPLETED_NO_CHANGE'
    assert s.catalog.get_library(s.kb).pending_mutation_id is None
    assert s.catalog.get_library(s.kb).current_revision_id==UUID(old['receipt']['revision_id'])
    assert len(s.backend.collections)==1 and len(s.model.calls)==calls
    with s.catalog._db.transaction() as db:
        assert db.execute('SELECT count(*) FROM revisions').fetchone()==(1,)
        assert db.execute('SELECT count(*) FROM publications').fetchone()==(1,)
    with begin(s,(c,)) as owner: owner.abandon()


def test_all_failed_new_library_has_no_published_version(setup):
    s=setup; path=source(s,'bad.md','bad');path.write_bytes(b'\xff')
    with begin(s,(path,)) as owner:
        result=run(s,owner)
    assert result['summary']['failed']==1 and result['receipt'] is None
    assert s.catalog.get_library(s.kb).current_revision_id is None
    assert not s.backend.collections


def test_explicit_delete_last_document_publishes_empty_revision(setup):
    s=setup; path=source(s,'one.md','# One\nOnly historical document.\n')
    with begin(s,(path,)) as owner: first=run(s,owner)
    identity=UUID(next(iter(members(s))))
    version=UUID(members(s)[str(identity)])
    with begin(s,delete_document_ids=(identity,)) as owner:
        result=run(s,owner)
    assert result['summary']['published_deleted']==1 and result['validation']['empty_revision']
    assert members(s)=={} and len(s.backend.collections)==2
    assert s.catalog.archives.read(s.catalog.get_version(version).raw_hash)==path.read_bytes()
    with begin(s,delete_document_ids=(identity,)) as owner:
        assert run(s,owner)['summary']['state']=='COMPLETED_NO_CHANGE'
    assert len(s.backend.collections)==2


def test_failed_explicit_move_does_not_steal_old_path_then_retry_moves_atomically(setup):
    s=setup; (a,b,c),_=initial(s)
    old_items={s.catalog.get_document(UUID(k)).original_name:UUID(k) for k in members(s)}
    identity=old_items['a.md']
    moved=source(s,'moved.md','# Move\nReject moved encoding.\n')
    s.model.fail_text='Reject'
    with begin(s,(InputSelection(path=str(moved),document_id=identity),)) as owner:
        result=run(s,owner); failed=owner.token.batch_id
    assert s.catalog.get_document(identity).source_key==a.as_uri()
    with begin(s,(a,)) as owner:
        assert run(s,owner)['summary']['unchanged']==1
    s.model.fail_text=None
    with retry_failed(s.catalog,failed) as owner:
        result=run(s,owner)
    assert result['summary']['published_updated']==1
    assert s.catalog.get_document(identity).source_key==moved.as_uri()
    with pytest.raises(RagError,match='superseded'):
        retry_failed(s.catalog,failed)
    # Old path now means a new document, by the confirmed D18 identity rule.
    with begin(s,(a,)) as owner:
        result=run(s,owner)
    assert result['summary']['published_new']==1


def test_retry_is_only_failed_and_checks_input_config_and_newer_versions(setup):
    s=setup; (a,b,c),_=initial(s)
    a.write_text('# A\nReject first revision.\n',encoding='utf-8')
    d=source(s,'new.md','# New\nSuccessful new document.\n')
    s.model.fail_text='Reject'
    with begin(s,(a,d)) as owner:
        run(s,owner); failed=owner.token.batch_id
    a.write_text('# A\nChanged input after failed receipt.\n',encoding='utf-8')
    with retry_failed(s.catalog,failed) as owner:
        with pytest.raises(RagError,match='retry input differs'):
            run(s,owner)
        owner.abandon()
    with retry_failed(s.catalog,failed,accept_input_changes=True) as owner:
        result=run(s,owner)
    assert result['summary']['published_updated']==1
    assert len(result['summary']['items'])==1
    with pytest.raises(RagError,match='superseded'):
        retry_failed(s.catalog,failed,accept_input_changes=True)


def test_incompatible_encoding_rejected_before_durable_batch(setup):
    s=setup; (a,b,c),_=initial(s)
    data=s.config.model_dump(mode='json');data['processing']['chunker']['max_tokens']-=1
    from agentic_rag.config import KnowledgeConfig
    other=KnowledgeConfig.model_validate_json(json.dumps(data))
    with pytest.raises(RagError,match='D16'):
        begin_changes(s.catalog,s.kb,ProcessingSnapshot.capture(uuid4(),other),(InputSelection(path=str(a)),))
    assert s.catalog.get_library(s.kb).pending_mutation_id is None


@pytest.mark.parametrize('stage',['capture','after_files','before_publish'])
def test_cancel_never_publishes_completed_subset(setup,stage):
    s=setup;(a,b,c),old=initial(s)
    a.write_text('# A\nCancelled new content.\n',encoding='utf-8')
    flag={'value':stage=='capture'}
    def observe(name,value):
        if (stage=='after_files' and name=='file_terminal') or (stage=='before_publish' and name=='validated'):
            flag['value']=True
    with begin(s,(a,b)) as owner:
        with pytest.raises(RagError) as error:
            run(s,owner,cancelled=lambda:flag['value'],observer=observe)
        assert error.value.error.code==ErrorCode.CANCELLED
        batch=owner.token.batch_id
    assert s.catalog.get_batch(batch).state.value=='WAITING_RECOVERY'
    assert s.catalog.get_library(s.kb).current_revision_id==UUID(old['receipt']['revision_id'])


@pytest.mark.parametrize('failure_stage',['model_service','insert','publish'])
def test_library_failure_retains_old_pointer_and_reports_unpublished(setup,monkeypatch,failure_stage):
    s=setup;(a,b,c),old=initial(s)
    a.write_text('# A\nReject changed content.\n',encoding='utf-8')
    if failure_stage=='model_service':
        s.model.fail_text='Reject';s.model.failure=ErrorCode.WORKER_UNAVAILABLE
    elif failure_stage=='insert':
        s.backend.insert=lambda *a,**kw: (_ for _ in ()).throw(ConnectionError('controlled service outage'))
    else:
        monkeypatch.setattr(publication,'publish',lambda *a: (_ for _ in ()).throw(ConnectionError('controlled precommit outage')))
    with begin(s,(a,)) as owner:
        with pytest.raises((RagError,ConnectionError)):
            run(s,owner)
        result=mutation_summary(s.catalog,owner.token.batch_id)
        assert result['published_new']==result['published_updated']==0
        if failure_stage!='model_service': assert result['processed_unpublished']==1
    assert s.catalog.get_library(s.kb).current_revision_id==UUID(old['receipt']['revision_id'])


def test_old_receipt_retry_does_not_publish_again_or_rewind(setup):
    s=setup;(a,b,c),_=initial(s)
    a.write_text('# A\nSecond content.\n',encoding='utf-8')
    with begin(s,(a,)) as old_owner: second=run(s,old_owner)
    a.write_text('# A\nThird content.\n',encoding='utf-8')
    with begin(s,(a,)) as owner: third=run(s,owner)
    assert run(s,old_owner)['receipt']==second['receipt']
    assert s.catalog.get_library(s.kb).current_revision_id==UUID(third['receipt']['revision_id'])


def test_corrupt_reused_float32_vector_is_library_failure(setup):
    s=setup;(a,b,c),old=initial(s)
    first=next(iter(s.backend.collections.values()))
    first[0]['dense']=[0.0,1.0]+[0.0]*1022
    a.write_text('# A\nValid update with corrupt base transport.\n',encoding='utf-8')
    with begin(s,(a,)) as owner:
        with pytest.raises(RagError,match='digest differs'):
            run(s,owner)
    assert s.catalog.get_library(s.kb).current_revision_id==UUID(old['receipt']['revision_id'])


def test_same_library_busy_and_missing_path_is_not_deletion(setup):
    s=setup;(a,b,c),_=initial(s);old=members(s)
    with begin(s,(a,)) as owner:
        with pytest.raises(RagError) as error: begin(s,(b,))
        assert error.value.error.code==ErrorCode.LIBRARY_BUSY
        run(s,owner)
    a.unlink()
    with begin(s,(a,)) as owner: run(s,owner)
    assert members(s)==old


def test_retry_rejects_add_delete_aba_but_allows_unrelated_publication(setup):
    s=setup; (a,b,c),_=initial(s)
    path=source(s,'failed_new.md','# New\nReject original input.\n')
    s.model.fail_text='Reject'
    with begin(s,(path,)) as owner:
        failed_result=run(s,owner);failed=owner.token.batch_id
    identity=UUID(failed_result['summary']['items'][0]['document_id'])
    d=source(s,'unrelated.md','# Different\nUnrelated publication.\n')
    with begin(s,(d,)) as owner:run(s,owner)
    with retry_failed(s.catalog,failed) as owner:
        assert run(s,owner)['summary']['state']=='COMPLETED_NO_CHANGE'
    s.model.fail_text=None
    with begin(s,(path,)) as owner:run(s,owner)
    with begin(s,delete_document_ids=(identity,)) as owner:run(s,owner)
    assert str(identity) not in members(s)
    with pytest.raises(RagError,match='intervening publication'):
        retry_failed(s.catalog,failed)


def test_all_failures_with_changed_index_do_not_publish(setup):
    s=setup;(a,b,c),old=initial(s)
    data=s.config.model_dump(mode='json');data['processing']['index']['bm25_k1']=1.3
    from agentic_rag.config import KnowledgeConfig
    s.config=KnowledgeConfig.model_validate_json(json.dumps(data));s.backend.storage=s.config.storage
    a.write_bytes(b'\xff')
    with begin(s,(a,)) as owner:
        result=run(s,owner)
    assert result['summary']['state']=='COMPLETED_NO_CHANGE' and result['summary']['failed']==1
    assert s.catalog.get_library(s.kb).current_revision_id==UUID(old['receipt']['revision_id'])


def test_successful_index_change_reuses_every_vector(setup):
    s=setup;(a,b,c),_=initial(s)
    data=s.config.model_dump(mode='json');data['processing']['index']['bm25_k1']=1.3
    from agentic_rag.config import KnowledgeConfig
    s.config=KnowledgeConfig.model_validate_json(json.dumps(data));s.backend.storage=s.config.storage
    calls=len(s.model.calls)
    with begin(s,(a,)) as owner:
        process_changes(s.catalog,owner,s.tokenizer,s.model)
        with pytest.raises(RagError,match='successful index changes'):
            publication.complete_no_change(s.catalog,owner)
        result=run(s,owner)
    assert result['summary']['state']=='PUBLISHED' and result['metrics']['reused_vectors']==3
    assert len(s.model.calls)==calls
    assert result['summary']['published_index_changed']


def test_missing_selection_retry_cannot_overwrite_later_identity(setup):
    s=setup;(a,b,c),_=initial(s)
    a.unlink()
    with begin(s,(a,)) as owner:
        failed=owner.token.batch_id;run(s,owner)
    a.write_text('# A\nA later successful update.\n',encoding='utf-8')
    with begin(s,(a,)) as owner:run(s,owner)
    a.write_text('# A\nStale retry must not replace later version.\n',encoding='utf-8')
    with pytest.raises(RagError,match='later document'):
        retry_failed(s.catalog,failed,accept_input_changes=True)


def test_explicit_same_content_move_and_index_change_preserve_both_changes(setup):
    s=setup;(a,b,c),_=initial(s)
    identity=next(UUID(k) for k in members(s) if s.catalog.get_document(UUID(k)).original_name=='a.md')
    old_version=UUID(members(s)[str(identity)])
    moved=source(s,'moved.md',a.read_text(encoding='utf-8'))
    data=s.config.model_dump(mode='json');data['processing']['index']['bm25_k1']=1.3
    from agentic_rag.config import KnowledgeConfig
    s.config=KnowledgeConfig.model_validate_json(json.dumps(data))
    with begin(s,(InputSelection(path=str(moved),document_id=identity),)) as owner:
        result=run(s,owner)
    new_version=UUID(members(s)[str(identity)])
    assert new_version!=old_version and result['summary']['published_updated']==1
    assert result['summary']['published_index_changed']
    assert s.catalog.get_document(identity).source_key==moved.as_uri()
    assert s.catalog.get_version(old_version).source_uri==a.as_uri()
    assert s.catalog.get_version(new_version).source_uri==moved.as_uri()


def test_later_encoding_batch_failure_discards_complete_document_prefix(setup):
    s=setup
    data=s.config.model_dump(mode='json')
    data['processing']['chunker'].update(max_tokens=40,overlap_tokens=4)
    from agentic_rag.config import KnowledgeConfig
    s.config=KnowledgeConfig.model_validate_json(json.dumps(data))
    (a,b,c),_=initial(s);before=members(s)
    a.write_text('# A\n'+('Every distant telescope requires precise calibration and approval. '*100)+'\n',encoding='utf-8')
    d=source(s,'success.md','# Good\nA valid new file.\n')
    original=s.model.embed_documents;calls=[]
    def later_failure(items,profile,context):
        calls.append(items)
        if len(calls)==2:
            raise RagError(ErrorCode.INPUT_TOO_LONG,'controlled later document batch failure',stage='test_model')
        return original(items,profile,context)
    s.model.embed_documents=later_failure
    with begin(s,(a,d)) as owner:
        result=run(s,owner)
        assert result['summary']['failed']==1 and result['summary']['published_new']==1
        failed=next(i for i in result['summary']['items'] if i['state']=='failed')
        assert failed['encoded_hash'] is None
        assert all(members(s)[key]==value for key,value in before.items())
        actual=set(r['chunk_id'] for r in s.backend.collections[publication.artifact(s.catalog,UUID(result['receipt']['revision_id']))['collection_name']])
        assert not actual.intersection(str(i.item_id) for i in calls[0])
    assert len(calls)>2


def test_failed_directory_retry_does_not_expand_unbounded_range(setup):
    s=setup; path=s.root/'missing-directory'
    with begin(s,(path,)) as owner:
        failed=owner.token.batch_id;run(s,owner)
    path.mkdir()
    (path/'one.md').write_text('# One\nFirst material.\n',encoding='utf-8')
    (path/'two.md').write_text('# Two\nSecond material.\n',encoding='utf-8')
    with pytest.raises(RagError,match='file range'):
        retry_failed(s.catalog,failed,accept_input_changes=True)


@pytest.mark.parametrize('module',['agentic_rag.indexes.manifest','agentic_rag.indexes.milvus',
                                   'agentic_rag.storage.publication','agentic_rag.ingestion','agentic_rag.retrieval.dense'])
def test_public_modules_import_independently_without_optional_engines(tmp_path,module):
    import subprocess,sys
    code='import importlib,sys;importlib.import_module(sys.argv[1]);assert not any(m in sys.modules for m in ("torch","transformers","pymilvus","codeplus"))'
    result=subprocess.run([sys.executable,'-I','-B','-c',code,module],cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
