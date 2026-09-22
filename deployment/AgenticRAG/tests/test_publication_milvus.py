"""Real Milvus acceptance with explicit synthetic 1024D protocol vectors.

Semantic quality is tested separately by the full 609-document CUDA runner.
"""
import copy
import json
import os
from pathlib import Path
import runpy
import socket
import time

import pytest
from agentic_rag.domain import RagError
from agentic_rag.storage import Catalog, publication
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.indexes.manifest import vector_hash

H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
pytestmark=pytest.mark.skipif(os.environ.get('R10_REAL')!='1',reason='explicit real owned Milvus required')


@pytest.mark.parametrize('fault',['none','row_hash','index_timeout','schema'])
def test_real_candidate_validation_and_failure_never_publish(tmp_path,fault):
    report={'scope':'actual Milvus production schema; synthetic normalized 1024D vectors', 'fault':fault}
    catalog=Catalog(tmp_path/'data')
    text=''.join(f'# Section {i}\nTelescope ocean immutable document number {i}.\n' for i in range(1100))
    owner,config=H['processed'](catalog,tmp_path/'source.md',text=text)
    prepared,artifact,expected=H['synthetic_encoded'](catalog,owner)
    backend=MilvusRevisionIndex(config.storage,catalog)
    owned_artifacts=[artifact]
    report['chunks']=len(expected)
    assert len(expected)>=1024
    try:
        if fault=='schema':
            backend.client.create_collection(artifact['collection_name'],dimension=4)
            with pytest.raises(RagError) as failure:publication.validate(catalog,owner,prepared.revision_id,backend)
            report['expected_error']=str(failure.value)
            assert catalog.get_library(owner.token.kb_id).current_revision_id is None
            assert publication.receipt(catalog,owner.token.batch_id) is None
            owner.abandon();report['result']='PASS'
            return
        backend.create(artifact,owner)
        rows=[{**row,'dense':H['VECTOR']} for row in expected]
        if fault=='row_hash':rows[-1]['body_hash']='f'*64
        for start in range(0,len(rows),128):backend.insert(artifact,rows[start:start+128],owner)
        if fault=='index_timeout':
            with pytest.raises(TimeoutError) as failure:
                backend.finalize(artifact,len(rows)+1,owner,index_timeout=0)
            report['expected_error']=str(failure.value)
        else:
            report['finalize']=backend.finalize(artifact,len(rows),owner)
            if fault=='row_hash':
                with pytest.raises(RagError) as failure:publication.validate(catalog,owner,prepared.revision_id,backend)
                report['expected_error']=str(failure.value)
            else:
                report['validation']=publication.validate(catalog,owner,prepared.revision_id,backend)
                report['receipt']=publication.publish(catalog,owner,prepared.revision_id)
                owner.close()
                assert publication.receipt(catalog,owner.token.batch_id)==report['receipt']
                report['immutable_model_vector_search']=backend.search(artifact,tuple(H['VECTOR']),limit=1)
                assert len(report['immutable_model_vector_search'])==1
                assert backend.search(artifact,H['VECTOR'],filter='chunk_id == "absent"')==[]
                report['normal_empty']=[]
                # Published outages preserve the authoritative pointer.
                backend.client.release_collection(artifact['collection_name'])
                with pytest.raises(Exception) as failure:backend.search(artifact,H['VECTOR'])
                report['unloaded_error']=str(failure.value)
                assert catalog.get_library(owner.token.kb_id).current_revision_id==prepared.revision_id
                backend.client.load_collection(artifact['collection_name'])
                # A real subsequent physical version advances the pointer. An
                # old response-loss retry returns its receipt without rollback.
                next_owner,next_config=H['processed'](catalog,tmp_path/'source.md',kb_id=owner.token.kb_id,
                    text=text.replace('Telescope','New telescope'))
                try:
                    np,na,ne=H['synthetic_encoded'](catalog,next_owner);owned_artifacts.append(na)
                    backend.create(na,next_owner)
                    for offset in range(0,len(ne),128):backend.insert(na,[{**r,'dense':H['VECTOR']} for r in ne[offset:offset+128]],next_owner)
                    backend.finalize(na,len(ne),next_owner)
                    publication.validate(catalog,next_owner,np.revision_id,backend)
                    report['next_receipt']=publication.publish(catalog,next_owner,np.revision_id)
                    report['old_retry_after_newer']=publication.publish(catalog,owner,prepared.revision_id)
                    assert report['old_retry_after_newer']==report['receipt']
                    assert catalog.get_library(owner.token.kb_id).current_revision_id==np.revision_id
                finally:next_owner.close()
        if fault!='none':
            assert catalog.get_library(owner.token.kb_id).current_revision_id is None
            assert publication.receipt(catalog,owner.token.batch_id) is None
            owner.abandon()
        report['result']='PASS'
    finally:
        owner.close()
        # Only this random fixture's owned name, after all fixture leases ended.
        for own in owned_artifacts:backend.client.drop_collection(backend._name(own))
        backend.close()
        if os.environ.get('R10_MILVUS_REPORT_DIR'):
            Path(os.environ['R10_MILVUS_REPORT_DIR'],f'R10-milvus-{fault}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=list)+'\n',encoding='utf-8')


def test_real_missing_endpoint_is_error_not_empty_success(tmp_path):
    catalog=Catalog(tmp_path/'data')
    owner,config=H['processed'](catalog,tmp_path/'source.md')
    report={'scope':'real Milvus client connecting to an owned unserved loopback port'}
    try:
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1',0))
            endpoint='http://127.0.0.1:'+str(reserved.getsockname()[1])
            storage=config.storage.model_copy(update={'milvus_uri':endpoint})
            with pytest.raises(Exception) as failure:MilvusRevisionIndex(storage,catalog,timeout=1)
            report.update(endpoint=endpoint,error_type=type(failure.value).__name__,error=str(failure.value))
        assert catalog.get_library(owner.token.kb_id).current_revision_id is None
        assert publication.receipt(catalog,owner.token.batch_id) is None
        owner.abandon();report['result']='PASS'
    finally:
        owner.close()
        if os.environ.get('R10_MILVUS_REPORT_DIR'):
            Path(os.environ['R10_MILVUS_REPORT_DIR'],'R10-milvus-unavailable.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
