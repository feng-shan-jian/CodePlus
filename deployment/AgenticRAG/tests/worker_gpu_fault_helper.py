"""Controlled Python/synchronization faults AFTER a real CUDA layer executed."""
import json
import os
import sys
import threading
import time
from uuid import uuid4

from agentic_rag.capabilities import ModelInput,RequestContext
from agentic_rag.models.engine import CudaEngine,UnsafeCudaCompletion
from agentic_rag.models.identity import coordination_directory
from agentic_rag.models.protocol import Submit
from agentic_rag.profiles import EmbeddingProfile
from agentic_rag.storage.locks import ProcessLock


def request():
    profile=EmbeddingProfile(name='fault-verification')
    return Submit(type='submit',operation='query',profile=profile,profile_fingerprint=profile.identity,
        context=RequestContext(request_id=uuid4(),owner_id=uuid4(),purpose='qa',deadline_monotonic_ns=time.monotonic_ns()+60000000000),
        items=(ModelInput(item_id=uuid4(),text='Version isolation and recovery.'),))


if __name__=='__main__':
    mode,cache=sys.argv[1:]
    with ProcessLock(coordination_directory()/'lifetime.lock'):
        engine=CudaEngine(cache);req=request();cancelled=threading.Event()
        engine.execute(req,cancelled,lambda _:None,time.monotonic_ns())
        event=engine.torch.cuda.Event()
        def injected_after_layer(*_):
            engine.torch.cuda._sleep(30000000)
            event.record()
            if mode=='fatal':
                def controlled_sync_failure():raise RuntimeError('controlled synchronization failure')
                engine.torch.cuda.synchronize=controlled_sync_failure
                print(json.dumps({'pid':os.getpid(),'real_layer_executed':True,'injected':'synchronize_failure'}),flush=True)
            raise RuntimeError('controlled Python failure after CUDA layer')
        hook=engine.model.layers[0].register_forward_hook(injected_after_layer)
        try:
            engine.execute(request(),cancelled,lambda _:None,time.monotonic_ns())
        except RuntimeError as exc:
            assert mode=='forward' and str(exc)=='controlled Python failure after CUDA layer'
            assert event.query(),'execution returned before queued CUDA work completed'
            print(json.dumps({'pid':os.getpid(),'real_layer_executed':True,'injected':'forward_python_failure',
                              'cuda_event_complete_before_error_return':True}),flush=True)
        finally:
            hook.remove()
            if mode!='fatal':engine.unload()
