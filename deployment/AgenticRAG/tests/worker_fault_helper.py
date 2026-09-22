"""Controlled completion faults in a real interpreter; never a GPU capability test."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
import time
from uuid import uuid4

from agentic_rag.capabilities import ModelInput, RequestContext
from agentic_rag.config import WorkerExecutionConfig
from agentic_rag.models import engine, worker
from agentic_rag.models.protocol import Submit
from agentic_rag.profiles import EmbeddingProfile


async def injected_serve(*_):
    class Poisoned:
        def execute(self, *_):
            raise engine.UnsafeCudaCompletion('controlled synchronization failure')
    executor = ThreadPoolExecutor(1)
    instance = worker.Worker(WorkerExecutionConfig(executable=sys.executable,model_cache=sys.argv[2],runtime_dir=sys.argv[2]),
                             {},{},executor,Poisoned())
    session = worker.Session(None,uuid4())
    profile = EmbeddingProfile(name='controlled')
    request = Submit(type='submit',operation='query',profile=profile,profile_fingerprint=profile.identity,
        context=RequestContext(request_id=uuid4(),owner_id=session.owner,purpose='qa',deadline_monotonic_ns=time.monotonic_ns()+1000000000),
        items=(ModelInput(item_id=uuid4(),text='fixture'),))
    instance.queue.append(worker.Job(request,session,1));instance.queue_bytes=1
    async def forbidden_finish(*_,**__):
        print('UNSAFE_FINISHED',flush=True)
    instance.finish=forbidden_finish
    try:
        await instance.scheduler()
    finally:
        executor.shutdown(wait=True)


if __name__ == '__main__':
    # Invoke production main in an actual child with only its execution dependency
    # replaced. No alternate behavior is exposed through production IPC.
    root=sys.argv[2]
    worker.describe=lambda: {}
    worker.serve=injected_serve
    sys.argv=['worker','--bootstrap',root]
    worker.main()
