"""One real interpreter competing for the same R14 recovery CAS selection."""

import json,os,sys,time
from pathlib import Path
from uuid import UUID
import agentic_rag
from agentic_rag.config import KnowledgeConfig,WorkerExecutionConfig
from agentic_rag.domain import RagError
from agentic_rag.ingestion import continue_recovery,abandon_recovery
from agentic_rag.models import FrozenTokenizer,LocalModelClient
from agentic_rag.models.identity import process_birth
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.storage import Catalog,OwnerToken


def write(path,value):
    temporary=path.with_suffix('.pending');temporary.write_text(json.dumps(value),encoding='utf-8');os.replace(temporary,path)


def main():
    root=Path(sys.argv[1]);name,action=sys.argv[2:4];settings=json.loads((root/'settings.json').read_text())
    config=KnowledgeConfig.model_validate_json(json.dumps(settings['config']));worker=WorkerExecutionConfig.model_validate_json(json.dumps(settings['worker']))
    catalog=Catalog(config.storage.data_dir);expected=OwnerToken(**{k:UUID(v) if k!='owner_epoch' else v for k,v in settings['expected'].items()})
    info={'pid':os.getpid(),'birth':process_birth(os.getpid()),'executable':sys.executable,'package':agentic_rag.__file__,'action':action,'time':time.time()}
    original=catalog.resume_mutation
    def acquired(token):
        owner=original(token);write(root/(name+'-acquired.json'),{**info,'new_epoch':owner.token.owner_epoch})
        end=time.monotonic()+90
        while not (root/'release.json').exists():
            if time.monotonic()>end:owner.close();raise TimeoutError('competition release missing')
            time.sleep(.02)
        return owner
    catalog.resume_mutation=acquired
    provider=backend=None
    def runtime(frozen):
        nonlocal provider,backend
        provider=LocalModelClient(worker);backend=MilvusRevisionIndex(frozen.storage,catalog)
        return FrozenTokenizer(frozen.embedding,worker.model_cache),provider,backend
    write(root/(name+'-ready.json'),info)
    try:
        end=time.monotonic()+90
        while not (root/'start.json').exists():
            if time.monotonic()>end:raise TimeoutError('competition start missing')
            time.sleep(.02)
        result=continue_recovery(catalog,expected,runtime_factory=runtime) if action=='continue' else abandon_recovery(catalog,expected)
        info.update(status='completed',result=result)
    except RagError as exc:info.update(status='rejected',error=exc.error.model_dump(mode='json'))
    finally:
        if provider:provider.close()
        if backend:backend.close()
        info['finished']=time.time();write(root/(name+'-result.json'),info)


if __name__=='__main__':main()
