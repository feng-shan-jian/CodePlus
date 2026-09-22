"""Actual process boundaries around the production publish transaction."""
from contextlib import contextmanager
import json
from pathlib import Path
import runpy
import sys
import time
from uuid import uuid4

from agentic_rag.config import ProcessingSnapshot
from agentic_rag.storage import Catalog, publication
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models.identity import process_birth

H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
mode,root= sys.argv[1],Path(sys.argv[2])
catalog=Catalog(root/'data')
if mode=='other-library':
    started=time.perf_counter();kb=catalog.create_library('parallel independent library')
    with catalog.begin_mutation(kb.kb_id,ProcessingSnapshot.capture(uuid4(),H['configuration'](root/'data')),'a'*64) as owner:owner.abandon()
    print(json.dumps({'written':str(kb.kb_id),'elapsed_seconds':time.perf_counter()-started}),flush=True)
    raise SystemExit(0)

owner,config=H['processed'](catalog,root/'source.md',text=''.join(f'# Section {i}\nTelescope ocean publication boundary number {i}.\n' for i in range(1100)))
p,a,expected=H['synthetic_encoded'](catalog,owner)
backend=MilvusRevisionIndex(config.storage,catalog)
backend.create(a,owner)
for offset in range(0,len(expected),128):backend.insert(a,[{**row,'dense':H['VECTOR']} for row in expected[offset:offset+128]],owner)
state={'kb_id':str(owner.token.kb_id),'batch_id':str(owner.token.batch_id),'revision_id':str(p.revision_id),
       'collection_name':a['collection_name'],'pid':__import__('os').getpid(),'owner_nonce':str(owner.token.owner_nonce),'owner_epoch':owner.token.owner_epoch}
state['process_birth']=process_birth(state['pid'])
(root/'process-state.json').write_text(json.dumps(state),encoding='utf-8')
original_flush=backend.client.flush
def flush(*args,**kwargs):
    print(json.dumps({'gate':'service_io',**state}),flush=True)
    assert sys.stdin.readline().strip()=='continue'
    return original_flush(*args,**kwargs)
backend.client.flush=flush
backend.finalize(a,len(expected),owner)
publication.validate(catalog,owner,p.revision_id,backend)
original_transaction=catalog._db.transaction
@contextmanager
def gate_transaction(*,write=False):
    published=False
    with original_transaction(write=write) as connection:
        yield connection
        if write:
            published=connection.execute('SELECT 1 FROM publications WHERE batch_id=?',(state['batch_id'],)).fetchone() is not None
            if published and mode=='before-commit':
                print(json.dumps({'gate':'before_commit',**state}),flush=True)
                sys.stdin.readline()
    if published and mode=='after-commit':
        print(json.dumps({'gate':'after_commit',**state}),flush=True)
        sys.stdin.readline()
catalog._db.transaction=gate_transaction
receipt=publication.publish(catalog,owner,p.revision_id)
owner.close();backend.close()
print(json.dumps({'receipt':receipt}),flush=True)
