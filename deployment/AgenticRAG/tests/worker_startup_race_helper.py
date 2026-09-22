"""Actual competing interpreter for the stale-discovery/lifetime-lock race."""
import json
from pathlib import Path
import sys
import time

from agentic_rag.domain import RagError
from agentic_rag.models.identity import atomic_private_json
from agentic_rag.storage.locks import ProcessLock

root=Path(sys.argv[1]);end=time.monotonic()+10
while not (root/'attempt-gate').exists():
    if time.monotonic()>=end:raise TimeoutError('test gate')
    time.sleep(.005)
lock=ProcessLock(root/'lifetime.lock')
try:
    lock.acquire()
    (root/'attempt-result').write_text('acquired_before_cleanup')
except RagError:
    (root/'attempt-result').write_text('blocked_by_lifetime')
    lock.acquire(timeout_ms=5000)
with lock:
    atomic_private_json(root/'active.json',{'race_owner':True})
print(json.dumps({'actual_child_pid':__import__('os').getpid(),'published_after_lifetime':True}),flush=True)
