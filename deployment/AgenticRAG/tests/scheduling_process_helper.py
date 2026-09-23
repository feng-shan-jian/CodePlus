"""Real installed host for R22's bounded CUDA scheduling experiment."""
import json
import os
from pathlib import Path
import runpy
import sys
import time
from uuid import uuid4

from agentic_rag.capabilities import ModelInput, RequestContext
from agentic_rag.config import WorkerExecutionConfig
from agentic_rag.profiles import EmbeddingProfile

H=runpy.run_path(str(Path(__file__).with_name('scheduling_support.py')))


def emit(value):
    print(json.dumps(value),flush=True)


def main():
    config=WorkerExecutionConfig.model_validate_json(Path(sys.argv[1]).read_text(encoding='utf-8'))
    client=H['ObservedClient'](config,sys.argv[2]);handles={};delivered=set()
    emit({'ready':True,'host_pid':os.getpid(),'executable':sys.executable,'owner_id':str(client.owner_id)})
    try:
        for line in sys.stdin:
            cmd=json.loads(line)
            if cmd['op']=='submit':
                text='x '*2046+'x' if cmd.get('long') else 'How are immutable versions shared?'
                items=tuple(ModelInput(item_id=uuid4(),text=text) for _ in range(2 if cmd.get('long') else 1))
                context=RequestContext(request_id=uuid4(),owner_id=client.owner_id,purpose=cmd['purpose'],
                    deadline_monotonic_ns=time.monotonic_ns()+120_000_000_000)
                profile=EmbeddingProfile(name='R22 '+str(os.getpid()))
                handle=(client.submit_documents(items,profile,context) if cmd['purpose'] in {'import','rebuild'}
                        else client.submit_query(items[0],profile,context))
                handles[cmd['id']]=handle
                handle.wait_phase('queued')
                if cmd.get('wait_phase'):handle.wait_phase(cmd['wait_phase'])
                emit({'accepted':cmd['id'],'request_id':str(context.request_id)})
            elif cmd['op']=='drain':
                rows=[]
                for name,handle in handles.items():
                    if name not in delivered and handle.execution_finished:
                        rows.append({'id':name,**client.record(handle)});delivered.add(name)
                emit({'completed':rows,'pending':len(handles)-len(delivered)})
            elif cmd['op']=='wait':
                handle=handles[cmd['id']];handle.result();assert handle.wait_finished(30)
                delivered.add(cmd['id']);emit({'id':cmd['id'],**client.record(handle)})
            elif cmd['op']=='status':
                emit(client.status())
            elif cmd['op']=='close':
                client.close();emit({'closed':True,'metadata':client.metadata});return
            else:raise ValueError('unknown helper command')
    finally:
        client.close()


if __name__=='__main__':main()
