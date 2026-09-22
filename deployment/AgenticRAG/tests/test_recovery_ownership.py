"""Physical ownership/failure proofs with a controlled SDK transport."""

from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import UUID

import pytest
from pymilvus import MilvusClient

from agentic_rag.domain import RagError
from agentic_rag.ingestion import process_changes,abandon_recovery
from agentic_rag.storage import publication,recovery

H=runpy.run_path(str(Path(__file__).with_name('test_recovery.py')))
s=H['s']


def physical(s):
    collections={};dropped=[]
    class Client:
        create_schema=staticmethod(MilvusClient.create_schema)
        def get_server_version(self,**kw):return '3.0.1'
        def has_collection(self,name,**kw):return name in collections
        def create_collection(self,name,*,schema,**kw):
            collections[name]={'description':schema.description,'collection_id':len(collections)+100,'created_timestamp':10000+len(collections)}
        def describe_collection(self,name,**kw):return dict(collections[name])
        def drop_collection(self,name,**kw):dropped.append(name);del collections[name]
        def insert(self,name,rows,**kw):return {'insert_count':len(rows)}
    backend=H['H']['HELPER']['constructed_backend'](s.catalog,s.config,Client())
    return backend,collections,dropped


def candidate(s):
    owner=H['begin'](s,[H['source'](s)])
    process_changes(s.catalog,owner,s.tokenizer,s.model)
    prepared,artifact=publication.register(s.catalog,owner)
    return owner,prepared,artifact


def abandon(s,owner):
    batch=owner.token.batch_id;owner.close()
    abandon_recovery(s.catalog,H['expected'](s,batch))
    return batch


def test_create_roundtrip_evidence_and_only_claimed_abandoned_candidate_drops(s):
    owner,prepared,artifact=candidate(s);backend,collections,dropped=physical(s)
    backend.create(artifact,owner)
    assert backend.has_ownership(artifact)
    with pytest.raises(RagError):backend.drop_owned(artifact)
    assert not dropped
    batch=abandon(s,owner)
    result=recovery.cleanup_candidates(s.catalog,batch,backend)
    assert result[0]['state']=='reclaimed' and dropped==[artifact['collection_name']]
    assert recovery.cleanup_candidates(s.catalog,batch,backend)[0]['state']=='reclaimed'
    assert len(dropped)==1
    assert s.catalog.get_input_items(batch)[0].raw


def test_published_current_cannot_be_dropped_through_low_level_method(s):
    owner,prepared,artifact=candidate(s);backend,collections,dropped=physical(s)
    backend.create(artifact,owner)
    expected=[{**row,'vector_hash':H['H']['HELPER']['vector_hash'](H['H']['VECTOR'])} for row in prepared.rows]
    publication.record_encoded(s.catalog,owner,prepared.revision_id,expected)
    validator=H['H']['HELPER']['unit_backend'](s.catalog,s.config,expected)
    publication.validate(s.catalog,owner,prepared.revision_id,validator)
    publication.publish(s.catalog,owner,prepared.revision_id);owner.close()
    with pytest.raises(RagError):backend.drop_owned(artifact)
    assert not dropped and artifact['collection_name'] in collections


@pytest.mark.parametrize('fault',['collision','lost_response','no_receipt','replacement'])
def test_uncertain_or_replaced_physical_collections_are_retained(s,fault):
    owner,prepared,artifact=candidate(s);backend,collections,dropped=physical(s)
    name=artifact['collection_name']
    if fault=='collision':
        collections[name]={'description':'foreign','collection_id':5,'created_timestamp':10}
        with pytest.raises(RagError):backend.create(artifact,owner)
    elif fault=='lost_response':
        create=backend.client.create_collection
        def uncertain(*a,**kw):create(*a,**kw);raise TimeoutError('accepted create; response lost')
        backend.client.create_collection=uncertain
        with pytest.raises(TimeoutError):backend.create(artifact,owner)
    elif fault=='no_receipt':
        # Actual resource exists, no create/describe receipt was committed.
        collections[name]={'description':backend._ownership(artifact)[2],'collection_id':8,'created_timestamp':11}
    else:
        backend.create(artifact,owner);collections[name]['collection_id']+=1
    batch=abandon(s,owner)
    result=recovery.cleanup_candidates(s.catalog,batch,backend)
    assert result[0]['state']=='retained' and not dropped and name in collections


def test_drop_failure_is_durable_retry_and_repeated_abandon_is_offline(s):
    owner,prepared,artifact=candidate(s);backend,collections,dropped=physical(s);backend.create(artifact,owner)
    batch=abandon(s,owner);original=backend.client.drop_collection
    backend.client.drop_collection=lambda *a,**kw:(_ for _ in ()).throw(TimeoutError('service unavailable'))
    assert recovery.cleanup_candidates(s.catalog,batch,backend)[0]['state']=='retained'
    with s.catalog._db.transaction() as db:assert db.execute('SELECT count(*) FROM candidate_cleanup_attempts').fetchone()==(1,)
    backend.catalog=None
    assert abandon_recovery(s.catalog,owner.token,backend=backend)['state']=='ABANDONED'
    backend.catalog=s.catalog;backend.client.drop_collection=original
    assert recovery.cleanup_candidates(s.catalog,batch,backend)[0]['state']=='reclaimed'


def test_old_sdk_pending_does_not_protect_a_different_generation(s):
    owner,prepared,old=candidate(s);backend,collections,dropped=physical(s);backend.create(old,owner)
    io=recovery.start_io(s.catalog,owner,'milvus_insert',artifact_id=old['artifact_id'])
    batch=owner.token.batch_id;owner.close()
    with s.catalog.resume_mutation(H['expected'](s,batch)) as new:
        _,fresh=publication.register(s.catalog,new);backend.create(fresh,new)
    abandon_recovery(s.catalog,H['expected'](s,batch))
    result=recovery.cleanup_candidates(s.catalog,batch,backend)
    assert [r['state'] for r in result]==['retained','reclaimed']
    assert dropped==[fresh['collection_name']] and old['collection_name'] in collections
    recovery.observe_io(s.catalog,io,finished=True)
    assert recovery.cleanup_candidates(s.catalog,batch,backend)[0]['state']=='reclaimed'
