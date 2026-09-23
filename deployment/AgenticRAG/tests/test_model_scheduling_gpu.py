"""Opt-in real loading/queued deadlines, distinct from controlled-engine tests."""
import json
import os
from pathlib import Path
import runpy
import sys
import time
from uuid import uuid4

import pytest

from agentic_rag.capabilities import ModelInput,RequestContext
from agentic_rag.config import WorkerExecutionConfig
from agentic_rag.domain import ErrorCode,RagError
from agentic_rag.models.identity import process_birth
from agentic_rag.profiles import EmbeddingProfile

H=runpy.run_path(str(Path(__file__).with_name('scheduling_support.py')))
pytestmark=pytest.mark.skipif(os.environ.get('R22_REAL')!='1',reason='explicit installed CUDA scheduling acceptance required')


def test_load_time_deadline_and_queued_expiry_do_not_wait_for_active_model_load(tmp_path):
    config=WorkerExecutionConfig(executable=os.environ['R09_CUDA_PYTHON'],model_cache=os.environ['R09_MODEL_CACHE'],
        runtime_dir=str(tmp_path/'worker'),idle_timeout_ms=500)
    first=H['ObservedClient'](config,tmp_path/'first.jsonl');other=H['ObservedClient'](config,tmp_path/'other.jsonl')
    profile=EmbeddingProfile(name='loading deadline')
    item=ModelInput(item_id=uuid4(),text='Immutable revisions protect concurrent readers.')
    def context(client,seconds=60):
        return RequestContext(request_id=uuid4(),owner_id=client.owner_id,purpose='qa',
            deadline_monotonic_ns=time.monotonic_ns()+int(seconds*1e9))
    report={'status':'RUNNING','boundary':'real CUDA load path and real queue; no inference fault injection'}
    try:
        first.submit_query(item,profile,context(first)).result()
        other.submit_query(item,profile,context(other)).result()
        changed=profile.model_copy(update={'instruction':'Retrieve original evidence about concurrent versioned reads.'})
        # A genuinely different identity forces the production validation/load
        # path. The same absolute deadline is not renewed at that boundary.
        active=first.submit_query(item,changed,context(first,.75));active.wait_phase('loading')
        expired=other.submit_query(item,profile,context(other,.05));expired.wait_phase('queued')
        with pytest.raises(RagError) as caught:expired.result()
        assert caught.value.error.code==ErrorCode.DEADLINE_EXCEEDED
        assert expired.wait_finished(2)
        report['queued']=other.record(expired)
        assert report['queued']['events'][-1]['error']['stage']=='queue'
        assert not active.execution_finished
        with pytest.raises(RagError) as caught:active.result()
        assert caught.value.error.code==ErrorCode.DEADLINE_EXCEEDED
        assert active.wait_finished(60)
        report['loading']=first.record(active)
        assert report['loading']['events'][-1]['error']['stage'] in {'model_assets','load'}
        assert all(row.get('phase')!='inference_started' for row in report['loading']['events'])
        recovered=other.submit_query(item,profile,context(other));recovered.result()
        report['survivor']=other.record(recovered)
        assert recovered.execution_finished and report['survivor']['status']=='completed'
        report['status']='PASS'
    except BaseException as exc:
        report.update(status='FAIL',error=repr(exc));raise
    finally:
        metadata=first.metadata or other.metadata
        first.close();other.close()
        if metadata:
            end=time.monotonic()+30
            while process_birth(metadata['pid'])==metadata['process_birth'] and time.monotonic()<end:time.sleep(.03)
            assert process_birth(metadata['pid'])!=metadata['process_birth']
        H['write'](tmp_path/'deadline.json',report)


def test_absolute_deadline_also_expires_after_real_weight_load_before_inference(tmp_path):
    config=WorkerExecutionConfig(executable=os.environ['R09_CUDA_PYTHON'],model_cache=os.environ['R09_MODEL_CACHE'],
        runtime_dir=str(tmp_path/'worker'),idle_timeout_ms=500)
    client=H['ObservedClient'](config,tmp_path/'weight-load.jsonl')
    profile=EmbeddingProfile(name='weight load deadline')
    item=ModelInput(item_id=uuid4(),text='A new model identity must not renew a request deadline.')
    def context(seconds):
        return RequestContext(request_id=uuid4(),owner_id=client.owner_id,purpose='qa',
            deadline_monotonic_ns=time.monotonic_ns()+int(seconds*1e9))
    report={'status':'RUNNING','boundary':'actual changed-profile weights loaded, then deadline refuses inference',
        'deadline_seconds':2.0,'selection_basis':'preceding same-device validation ~0.33s, assets ~1.09s, full switch >3s'}
    try:
        warm=client.submit_query(item,profile,context(60));warm.result()
        report['warmup']=client.record(warm)
        changed=profile.model_copy(update={'instruction':'Retrieve source evidence for model loading deadline measurements.'})
        active=client.submit_query(item,changed,context(2.0));active.wait_phase('loading')
        with pytest.raises(RagError) as caught:active.result()
        assert caught.value.error.code==ErrorCode.DEADLINE_EXCEEDED
        assert active.wait_finished(60)
        report['expired']=client.record(active)
        assert report['expired']['events'][-1]['error']['stage']=='load'
        assert active.model_status['loaded']
        assert active.model_status['profile_fingerprint']==changed.identity
        assert active.model_status['model_instance_id']!=warm.model_status['model_instance_id']
        assert active.model_status['load_count']==warm.model_status['load_count']+1
        assert all(event.get('phase')!='inference_started' for event in report['expired']['events'])
        reuse=client.submit_query(item,changed,context(60));reuse.result()
        report['reuse_after_deadline']=client.record(reuse)
        assert reuse.model_status['model_instance_id']==active.model_status['model_instance_id']
        assert reuse.model_status['load_count']==active.model_status['load_count']
        report['status']='PASS'
    except BaseException as exc:
        report.update(status='FAIL',error=repr(exc));raise
    finally:
        metadata=client.metadata;client.close()
        if metadata:
            end=time.monotonic()+30
            while process_birth(metadata['pid'])==metadata['process_birth'] and time.monotonic()<end:time.sleep(.03)
            assert process_birth(metadata['pid'])!=metadata['process_birth']
        H['write'](tmp_path/'weight-load-deadline.json',report)
