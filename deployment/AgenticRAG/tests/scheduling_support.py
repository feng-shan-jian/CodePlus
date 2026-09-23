"""Read-only client observations for opt-in R22 measurements.

No worker messages, model outputs or scheduling decisions are replaced. Times
are host receive observations in the same OS monotonic clock domain, not GPU
kernel timestamps. queue_ms includes input validation in the current contract.
"""
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from agentic_rag.domain import RagError
from agentic_rag.models import LocalModelClient


def write(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


class ObservedClient(LocalModelClient):
    def __init__(self, config, trace_path, **kwargs):
        self.trace_path=Path(trace_path)
        self.trace_lock=threading.Lock()
        self.events=[]
        super().__init__(config,**kwargs)

    def observe(self, value):
        row={'observed_ns':time.monotonic_ns(),'wall_time':time.time(),'host_pid':os.getpid(),
             'owner_id':str(self.owner_id),**value}
        with self.trace_lock:
            self.events.append(row)
            with self.trace_path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')
        return row

    def _submit(self, operation, items, profile, context, query=None):
        self.observe({'type':'submitted','request_id':str(context.request_id),'context':context.model_dump(mode='json'),
            'operation':operation,'profile_fingerprint':profile.identity,'item_ids':[str(x.item_id) for x in items],
            'input_sha256':[hashlib.sha256(x.text.encode()).hexdigest() for x in items]})
        return super()._submit(operation,items,profile,context,query)

    def _receive(self, deadline=None):
        value=super()._receive(deadline)
        if value.get('type') in {'phase','finished','cancel_ack'}:
            observed={key:item for key,item in value.items() if key!='response'}
            if value.get('response'):
                response=value['response']
                observed['response']={key:response[key] for key in ('request_id','profile_fingerprint','timings')}
                observed['response']['input_tokens']=[x['input_tokens'] for x in response['results']]
            self.observe(observed)
        return value

    def record(self, handle):
        try:
            response=handle.result()
            result={'status':'completed','timings':response.timings.model_dump(mode='json'),
                    'input_tokens':[r.input_tokens for r in response.results]}
        except RagError as exc:
            result={'status':'failed','error':exc.error.model_dump(mode='json')}
        with self.trace_lock:
            events=[r for r in self.events if r.get('request_id')==str(handle.request.context.request_id)]
        return {'request_id':str(handle.request.context.request_id),'owner_id':str(self.owner_id),
            'purpose':handle.request.context.purpose,'profile_fingerprint':handle.request.profile.identity,
            'worker':handle.worker_identity,'model_status':handle.model_status,'events':events,
            'execution_finished':handle.execution_finished,'completion_source':handle.completion_source,**result}
