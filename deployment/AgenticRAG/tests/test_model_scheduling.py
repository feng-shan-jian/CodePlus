"""Scheduler contracts; these controlled engines are not GPU measurements."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
from uuid import uuid4

import pytest

from agentic_rag.capabilities import EmbeddingResponse, EmbeddingResult, ModelInput, ModelTimings, RequestContext
from agentic_rag.config import WorkerExecutionConfig
from agentic_rag.models.protocol import Submit
from agentic_rag.models.worker import Job, Session, Worker
from agentic_rag.profiles import EmbeddingProfile


@pytest.mark.parametrize('initial_streak', [0, 3, 12])
def test_backlog_preserves_same_class_fifo_and_bounds_foreground_streak(tmp_path, initial_streak):
    async def scenario():
        profile = EmbeddingProfile(name='scheduler contract')
        class Engine:
            snapshot = {'test_double': True}
            def execute(self, request, cancelled, observer, admitted):
                return EmbeddingResponse(request_id=request.context.request_id, profile_fingerprint=profile.identity,
                    results=(EmbeddingResult(item_id=request.items[0].item_id, vector=(1.0,)+(0.0,)*1023,
                                             input_tokens=1),), timings=ModelTimings(queue_ms=0,load_ms=0,inference_ms=0))
        executor = ThreadPoolExecutor(1)
        config = WorkerExecutionConfig(executable=__import__('sys').executable, model_cache=str(tmp_path/'cache'),
            runtime_dir=str(tmp_path/'worker'), idle_timeout_ms=100)
        worker = Worker(config, {}, {}, executor, Engine())
        worker.front_streak = initial_streak
        sessions = [Session(None,uuid4()), Session(None,uuid4())]
        admitted = []
        for index in range(28):
            purpose = ('import','qa','report','rebuild','qa','report','qa')[index % 7]
            session = sessions[index % 2]
            request = Submit(type='submit',operation='documents',profile=profile,profile_fingerprint=profile.identity,
                items=(ModelInput(item_id=uuid4(),text=str(index)),),context=RequestContext(request_id=uuid4(),
                    owner_id=session.owner,purpose=purpose,deadline_monotonic_ns=time.monotonic_ns()+30_000_000_000))
            job = Job(request,session,1)
            worker.queue.append(job);worker.queue_bytes += 1;admitted.append(job)
        finished = []
        async def finish(job, **kwargs):
            assert kwargs['error'] is None
            finished.append(job)
        worker.finish = finish
        try:
            await asyncio.wait_for(worker.scheduler(),5)
            assert worker.queue_bytes == 0 and worker.running is None
            for purposes in ({'qa','report'},{'import','rebuild'}):
                assert [j for j in finished if j.request.context.purpose in purposes] == [
                    j for j in admitted if j.request.context.purpose in purposes]
            remaining_back=sum(j.request.context.purpose in {'import','rebuild'} for j in admitted)
            remaining_front=len(admitted)-remaining_back
            streak=initial_streak
            for job in finished:
                if job.request.context.purpose in {'qa','report'}:
                    if remaining_back:
                        assert streak < 4
                    streak+=1;remaining_front-=1
                else:
                    # A waiting foreground request has priority before its
                    # four-batch allowance has been used.
                    if remaining_front:
                        assert streak >= 4
                    remaining_back-=1;streak=0
            assert remaining_back == remaining_front == 0
        finally:
            executor.shutdown(wait=True)
    asyncio.run(scenario())
