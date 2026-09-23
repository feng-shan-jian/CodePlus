"""Explicit isolated runner transport substitutions; no service/quality claim."""
import argparse
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

mode,root= sys.argv[1],Path(sys.argv[2])
route=sys.argv[3] if len(sys.argv)>3 else 'dense'
runner=Path(__file__).resolve().parents[1]/'eval/dense_runner.py'
main=runpy.run_path(str(runner))['run']
g=main.__globals__
root.mkdir(exist_ok=True)
state={'kb_id':str(uuid4()),'revision_id':str(uuid4())}
(root/'state.json').write_text(json.dumps(state),encoding='utf-8')
class Lease:
    run=SimpleNamespace(run_id=uuid4(),revision_id=UUID(state['revision_id']))
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def finish(self,status,reason):
        assert (status.value,reason) in [('failed','explicit_error'),('completed','finished')]
        (root/'finish.json').write_text(json.dumps({'status':status.value,'reason':reason}),encoding='utf-8')
class Catalog:
    def __init__(self,*args):pass
    def start_run(self,*args):
        if mode=='run-init':raise RuntimeError('injected run initialization failure')
        return Lease()
class Backend:
    def __init__(self,*args):
        if mode in ('service-init','missing-extra'):raise ConnectionError('injected absent Milvus')
    def close(self):
        if mode=='close' and route=='bm25':raise RuntimeError('injected close failure')
class Provider:
    metadata={}
    def __init__(self,*args):
        assert route!='bm25','pure BM25 must not construct a model client'
    def close(self):
        if mode=='close':raise RuntimeError('injected close failure')
    def status(self):return {'synthetic_transport':True}
class Search:
    def __init__(self,*args):
        if mode=='search-init':raise RuntimeError('injected DenseSearch initialization failure')
        self.calls=0
    def search(self,query,**kwargs):
        self.calls+=1
        if mode=='question' and self.calls==4:raise ConnectionError('injected fourth request failure')
        if mode=='warm' and self.calls==201:raise ConnectionError('injected warm request failure')
        return {'hits':[],'elapsed_ms':1.0}
g.update(Catalog=Catalog,MilvusRevisionIndex=Backend,LocalModelClient=Provider,RetrievalSearch=Search)
if mode=='missing-extra':
    original=g['importlib'].metadata.version
    def version(name):
        if name=='pymilvus':raise g['importlib'].metadata.PackageNotFoundError(name)
        return original(name)
    g['importlib'].metadata.version=version
g['publication'].artifact=lambda *args,**kwargs: {'synthetic_transport':True}
args=SimpleNamespace(root=str(root),action=route,endpoint='http://127.0.0.1:19532',
    cuda_python=sys.executable,model_cache=str(root/'models'),report=str(root/'report.json'),
    ids=str(runner.parent/'development-ids.json'),dataset_hash='f80fc4033be6625b19da2af9529cf925d147e9ad62c95b943df2c3d08ec2e898')
try:main(args)
except Exception as exc:print(type(exc).__name__+': '+str(exc))
