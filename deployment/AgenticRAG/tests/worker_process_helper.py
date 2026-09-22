"""Real host process used by R09 acceptance; no production import dependency."""
import json
import math
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

from agentic_rag.capabilities import ModelInput, RequestContext
from agentic_rag.config import WorkerExecutionConfig
from agentic_rag.models import LocalModelClient
from agentic_rag.profiles import EmbeddingProfile


def emit(value):
    print(json.dumps(value), flush=True)


if __name__ == '__main__':
    config = WorkerExecutionConfig.model_validate_json(Path(sys.argv[1]).read_text(encoding='utf-8'))
    client = LocalModelClient(config)
    emit({'ready': True, 'host_pid': os.getpid(), 'executable': sys.executable})
    try:
        for line in sys.stdin:
            command = json.loads(line)
            if command['op'] == 'close':
                client.close()
                emit({'closed': True})
                break
            context = RequestContext(request_id=uuid4(), owner_id=client.owner_id, purpose='qa',
                                      deadline_monotonic_ns=time.monotonic_ns() + 60000000000)
            item = ModelInput(item_id=uuid4(), text='How are immutable versions used for concurrent queries?')
            handle = client.submit_query(item, EmbeddingProfile(name=command.get('name', 'host')), context)
            result = handle.result()
            emit({'host_pid': os.getpid(), 'worker_pid': client.metadata['pid'],
                  'worker_birth': client.metadata['process_birth'], 'instance_id': client.metadata['instance_id'],
                  'model_status': handle.model_status, 'phases': handle.phases,
                  'profile_identity': result.profile_fingerprint, 'request_id': str(result.request_id),
                  'item_id': str(result.results[0].item_id), 'dimension': len(result.results[0].vector),
                  'norm': math.sqrt(sum(value*value for value in result.results[0].vector)),
                  'input_tokens': result.results[0].input_tokens, 'timings': result.timings.model_dump(mode='json')})
    finally:
        client.close()
