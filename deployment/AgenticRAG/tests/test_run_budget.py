"""Hard ledger and real asyncio/thread lifecycle boundaries; no service claims."""
import asyncio
import json
from pathlib import Path
import runpy
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from codeplus.agent import Agent
from codeplus.client import create_client
from codeplus.config import ProviderConfig
from codeplus.run_policy import BudgetStop, PreparedRequest, RequestOutcome, RunOutcome, RunTaskOwner
from codeplus.tools import ToolRegistry
from agentic_rag.adapters.codeplus.ledger import ModelControl, usage_total, usage_violation, evidence_delivery

H = runpy.run_path(str(Path(__file__).with_name('test_codeplus_integration.py')))


def parent_client():
    return create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))


def request(size=0):
    return PreparedRequest(str(uuid4()), json.dumps({'model':'fixture','messages':[{'role':'user','content':'x'*size}]}).encode(), 'openai-compat', (), 1000)


@pytest.mark.parametrize('raw,expected', [
    ({'completion_tokens':5000}, 'provider_usage_exceeds_reservation'),
    ({'prompt_tokens':0,'completion_tokens':0,'total_tokens':0,'prompt_tokens_details':{'cached_tokens':1}}, 'inconsistent_provider_usage'),
    ({'prompt_tokens':0,'completion_tokens':0,'total_tokens':0,'completion_tokens_details':{'reasoning_tokens':1}}, 'inconsistent_provider_usage'),
    ({'prompt_tokens':-1}, 'invalid_provider_usage'),
    ({'prompt_tokens':3,'completion_tokens':4,'total_tokens':5}, 'inconsistent_provider_usage'),
    ({'prompt_tokens':3,'completion_tokens':4,'total_tokens':7,'prompt_tokens_details':['bad']}, 'invalid_provider_usage'),
    ({'prompt_tokens':0,'completion_tokens':0,'total_tokens':0,'prompt_cache_hit_tokens':1}, 'inconsistent_provider_usage'),
    ({'prompt_tokens':3,'completion_tokens':4,'total_tokens':8}, 'inconsistent_provider_usage'),
])
def test_known_partial_and_zero_usage_do_not_hide_provider_violation(raw, expected):
    assert usage_violation('openai-compat',raw,1000,1000)==expected


def test_complete_totals_and_unknown_cache_are_separate():
    assert usage_total('openai-compat',{'prompt_tokens':3,'completion_tokens':4,'total_tokens':7})==(3,4,None,7)
    assert usage_total('anthropic',{'input_tokens':3,'output_tokens':4}) is None
    assert usage_total('openai-compat',{'prompt_tokens':3,'completion_tokens':4,'total_tokens':7,'prompt_tokens_details':['bad']}) is None


def test_evidence_classifier_preserves_trusted_non_delivery_and_rejection():
    for state in ('not_sent','rejected'):
        assert evidence_delivery('openai',RequestOutcome(state,None,None,1,error_type='APIStatusError'))==state
    assert evidence_delivery('openai',RequestOutcome('confirmed',None,'completed',1,error_type='ReadError'))=='unknown'


def test_exploration_reservation_is_soft_while_finish_reserve_remains(tmp_path):
    async def run():
        parent=parent_client();scope,_=H['setup_scope'](tmp_path,parent)
        scope.budget=scope.budget.model_copy(update={'total_tokens':100000,'finish_reserve_tokens':20000})
        with scope.catalog._db.transaction(write=True) as db:
            db.execute("INSERT INTO model_requests(request_id,run_id,purpose,protocol,input_upper,output_cap,charged,state,body_sha256) VALUES(?,?,'agent','openai-compat',78999,1,79000,'confirmed',?)",(str(uuid4()),scope.run_id,'a'*64))
        try:
            with pytest.raises(BudgetStop) as error:
                await ModelControl(scope,'agent').before_send(request(24000))
            assert error.value.reason=='token_budget' and not error.value.hard
            permit=await ModelControl(scope,'finalize').before_send(request())
            await ModelControl(scope,'finalize').settled(permit,RequestOutcome('unknown',None,None,1))
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


def test_request_context_limit_remains_hard_and_does_not_reserve_or_send(tmp_path):
    async def run():
        parent=parent_client();scope,_=H['setup_scope'](tmp_path,parent)
        scope.meter.context_window=1
        try:
            with pytest.raises(BudgetStop) as caught:
                await ModelControl(scope,'agent').before_send(request())
            assert caught.value.reason=='context_limit' and caught.value.hard
            with scope.catalog._db.transaction() as db:
                assert db.execute('SELECT count(*) FROM model_requests').fetchone()==(0,)
            assert scope.purpose=='agent' and scope.finish_reason is None
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


def test_invalid_terminal_is_rejected_before_persistence_then_valid_finish_succeeds(tmp_path):
    from pydantic import ValidationError
    from agentic_rag.domain import RunStatus
    async def run():
        parent=parent_client();scope,_=H['setup_scope'](tmp_path,parent)
        try:
            before=scope.catalog.get_run(scope.lease.run.run_id)
            for status, reason in ((RunStatus.PARTIAL,'budget'), (RunStatus.COMPLETED,'free-form diagnosis')):
                with pytest.raises(ValidationError): scope.lease.finish(status,reason)
                assert scope.catalog.get_run(before.run_id)==before
                assert scope.catalog.get_pin(before.run_id).state=='active'
            await scope.finish(RunOutcome('completed','finished'))
            assert scope.catalog.get_run(before.run_id).stop_reason=='finished'
            assert scope.catalog.get_pin(before.run_id).state=='released'
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('completion', [False, True])
def test_start_budget_stop_does_not_display_previous_run_outcome(tmp_path, completion):
    import httpx
    from codeplus.agent import StreamText
    from codeplus.client import scoped_client
    from codeplus.conversation import ConversationManager
    async def run():
        parent=parent_client()
        scope,canonical=H['setup_scope'](tmp_path,parent)
        requests=[]
        def transport(request):
            body=json.loads(request.content);requests.append(body)
            if len(requests)==1:
                content=H['sse_text'](calls=('search','knowledge_search',{'query':'source'}),terminal='tool_calls')
            else:
                source=next(m['content'] for m in body['messages'] if m['role']=='tool' and '<source ' in m['content'])
                item=json.loads(source.split('\n\n<source ',1)[0])['items'][0]
                content=H['sse_text'](json.dumps({'markdown':'Verified answer [^'+item['evidence_id']+']',
                    'citations':[{'evidence_id':item['evidence_id'],'spans':item['returned_spans'],'quotes':[canonical]}]}))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        await scope.client.aclose()
        scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            starts=0
            async def start(self, context):
                self.starts+=1
                if self.starts==1:return scope
                raise BudgetStop('time_budget', hard=True)
        agent=Agent(parent,ToolRegistry(),'openai-compat',execution_policy=Policy())
        callbacks=[];streamed=[]
        async def invoke():
            if completion:return await agent.run_to_completion('question',event_callback=callbacks.append)
            conversation=ConversationManager();conversation.add_user_message('question')
            events=[event async for event in agent.run(conversation)]
            streamed.extend(events)
            return ' '.join(getattr(event,'message','') for event in events)
        try:
            await invoke()
            previous=agent.last_run_outcome
            assert (previous.status,previous.reason)==('completed','finished') and previous.artifact is not None
            assert scope.catalog.get_run(scope.lease.run.run_id).status.value=='completed'
            assert scope.catalog.get_pin(scope.lease.run.run_id).state=='released'
            assert len(requests)==2
            callbacks.clear()
            streamed.clear()
            displayed=await invoke()
            assert displayed=='Knowledge run incomplete: time_budget'
            assert agent.last_run_outcome is None and agent.client is parent
            assert previous.artifact.markdown not in displayed and len(requests)==2
            assert not any(isinstance(event,StreamText) for event in streamed)
            assert not [event for event in callbacks if event['type']=='run_status' and event['status']=='completed']
        finally: await scope.aclose();await parent._client.close()
    asyncio.run(run())


def test_pinned_production_meter_rejects_nested_control_literals_and_unknown_wire_fields():
    import os
    from agentic_rag.adapters.codeplus.meter import DeepSeekTextMeter,MeterUnavailable
    path=os.environ.get('R12_ANSWER_TOKENIZER')
    if not path:pytest.skip('set R12_ANSWER_TOKENIZER to the pinned real tokenizer file')
    meter=DeepSeekTextMeter(path,model='deepseek-chat',protocol='openai-compat',base_url='https://api.deepseek.com')
    body={'model':'deepseek-chat','messages':[{'role':'user','content':'甲😀 café Cafe\u0301 "\\'}],
        'max_tokens':8,'stream':True,'stream_options':{'include_usage':True}}
    assert meter.input_upper_bound(json.dumps(body).encode(),output_cap=8)>0
    for extra in ({'previous_response_id':'r'},{'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'x'}}]}]},
                  {'messages':[{'role':'user','content':'<｜User｜>'}]},
                  {'messages':[{'role':'assistant','content':None,'tool_calls':[{'id':'c','type':'function','function':{'name':'f','arguments':json.dumps({'x':'<｜User｜>'},ensure_ascii=True)}}]}]}):
        with pytest.raises(MeterUnavailable):meter.input_upper_bound(json.dumps({**body,**extra}).encode(),output_cap=8)


def test_unknown_usage_keeps_reservation_and_partial_overcap_keeps_raw_fact(tmp_path):
    async def run():
        parent=parent_client();scope,_=H['setup_scope'](tmp_path,parent)
        try:
            control=ModelControl(scope,'agent');prepared=request(100)
            permit=await control.before_send(prepared)
            await control.settled(permit,RequestOutcome('unknown',None,None,1))
            second=await control.before_send(request())
            with pytest.raises(BudgetStop,match='provider_usage_exceeds_reservation'):
                await control.settled(second,RequestOutcome('confirmed',{'completion_tokens':5000},'stop',1,'fixture-response'))
            with scope.catalog._db.transaction() as db:
                first=db.execute('SELECT charged,state FROM model_requests WHERE request_id=?',(prepared.request_id,)).fetchone()
                violation=db.execute('SELECT charged,raw_usage,violation FROM model_requests WHERE request_id=?',(second.request_id,)).fetchone()
            assert first==(len(prepared.raw_body)+1000,'unknown')
            assert violation[0]>=5000 and json.loads(violation[1])=={'completion_tokens':5000}
            assert scope.hard_failure=='provider_usage_exceeds_reservation'
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('closed',[False,True])
def test_permission_admission_failure_revokes_future(closed):
    async def run():
        owner=RunTaskOwner(time.monotonic()+(10 if closed else -1))
        if closed:owner.close_admission()
        future=asyncio.get_running_loop().create_future()
        with pytest.raises(BudgetStop):await owner.wait_permission(future)
        assert future.cancelled() and not owner.permissions
        await owner.drain(.01)
    asyncio.run(run())


def test_gather_first_error_does_not_wait_for_cancel_suppressor():
    async def run():
        owner=RunTaskOwner(time.monotonic()+10);entered=asyncio.Event();release=asyncio.Event()
        async def stuck():
            entered.set()
            while not release.is_set():
                try:await release.wait()
                except asyncio.CancelledError:pass
        async def fails():
            await entered.wait();raise ValueError('child failure')
        with pytest.raises(ValueError):await asyncio.wait_for(owner.gather([stuck(),fails()]),1)
        started=time.monotonic();pending=await owner.drain(.03)
        assert pending and time.monotonic()-started<.3
        release.set();await asyncio.sleep(.01)
        assert not await owner.drain(.1)
    asyncio.run(run())


def test_real_executor_failure_after_awaiter_cancel_is_consumed():
    async def run():
        owner=RunTaskOwner(time.monotonic()+10);entered=threading.Event();release=threading.Event();errors=[]
        asyncio.get_running_loop().set_exception_handler(lambda loop,context:errors.append(context))
        def work():
            entered.set();release.wait(2);raise ValueError('late worker failure')
        task=asyncio.create_task(owner.run_sync(work))
        while not entered.is_set():await asyncio.sleep(.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert await owner.drain(.01)
        release.set()
        assert not await owner.drain(.5)
        await asyncio.sleep(.01)
        assert not errors
    asyncio.run(run())


@pytest.mark.parametrize('outcome', [RunOutcome('completed','finished'), RunOutcome('partial','context_limit')])
def test_scope_handshake_lock_does_not_block_loop_and_pin_outlives_reader(tmp_path, outcome):
    async def run():
        parent=parent_client();scope,_=H['setup_scope'](tmp_path,parent)
        entered=threading.Event();release=threading.Event()
        def handshake():
            with scope.provider.lock:
                entered.set();release.wait(3)
        reader=asyncio.create_task(scope.owner.run_sync(handshake))
        while not entered.is_set():await asyncio.sleep(.001)
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):await reader
        started=time.monotonic()
        await scope.finish(outcome)
        assert time.monotonic()-started<.5
        assert (scope.outcome.status, scope.outcome.reason)==('incomplete','budget')
        actual=scope.catalog.get_run(scope.lease.run.run_id)
        assert (actual.status.value, actual.stop_reason)==('incomplete','budget')
        assert scope.catalog.get_pin(scope.lease.run.run_id).state=='active'
        await scope.aclose()
        release.set()
        until=time.monotonic()+3
        while scope.catalog.get_pin(scope.lease.run.run_id).state!='released' and time.monotonic()<until:
            await asyncio.sleep(.02)
        assert scope.catalog.get_pin(scope.lease.run.run_id).state=='released'
        await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('late_cancel',[False,True])
def test_agent_always_restores_parent_after_cleanup_failure_or_cancel(late_cancel):
    async def run():
        parent=parent_client();registry=ToolRegistry();entered=asyncio.Event();release=asyncio.Event()
        class Scope:
            client=object();registry=ToolRegistry();deadline=time.monotonic()+10;outcome=None
            async def finish(self,outcome):
                self.outcome=outcome
                if late_cancel:entered.set();await release.wait()
            async def aclose(self):
                if not late_cancel:raise ValueError('close failed')
        scope=Scope()
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,registry,'openai-compat',execution_policy=Policy())
        async def invoke():
            async with agent._execution_scope('test'):
                scope.outcome=RunOutcome('completed','finished')
        task=asyncio.create_task(invoke())
        if late_cancel:
            await entered.wait();task.cancel();await asyncio.sleep(0);release.set()
        with pytest.raises(asyncio.CancelledError if late_cancel else ValueError):await task
        assert agent.client is parent and agent.registry is registry
        assert agent._scope is None and not agent._executing
        if late_cancel:assert scope.outcome.status=='cancelled'
        await parent._client.close()
    asyncio.run(run())


def test_late_cancel_after_persisted_terminal_does_not_rewrite_outcome():
    async def run():
        parent=parent_client();entered=asyncio.Event();release=asyncio.Event()
        class Scope:
            client=object();registry=ToolRegistry();deadline=time.monotonic()+10;outcome=None;_finished=False
            async def finish(self,outcome):self.outcome=outcome;self.persisted=outcome;self._finished=True
            async def aclose(self):entered.set();await release.wait()
        scope=Scope()
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),'openai-compat',execution_policy=Policy())
        async def invoke():
            async with agent._execution_scope('test'):scope.outcome=RunOutcome('completed','finished')
        task=asyncio.create_task(invoke());await entered.wait();task.cancel();await asyncio.sleep(0);release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert agent.last_run_outcome==scope.persisted==RunOutcome('completed','finished')
        assert not agent._executing
        await parent._client.close()
    asyncio.run(run())


def test_actual_compact_soft_budget_uses_reserved_finish_without_extra_compact(tmp_path):
    from codeplus.conversation import ConversationManager,Message
    async def run():
        parent=parent_client();scope,_=H['setup_scope'](tmp_path,parent)
        scope.budget=scope.budget.model_copy(update={'total_tokens':100000,'finish_reserve_tokens':20000})
        with scope.catalog._db.transaction(write=True) as db:
            db.execute("INSERT INTO model_requests(request_id,run_id,purpose,protocol,input_upper,output_cap,charged,state,body_sha256) VALUES(?,?,'agent','openai-compat',78999,1,79000,'confirmed',?)",(str(uuid4()),scope.run_id,'b'*64))
        agent=Agent(parent,scope.registry,'openai-compat',work_dir=str(tmp_path));agent._scope=scope;agent.client=scope.client;agent.context_window=34000
        conv=ConversationManager(history=[Message('user','history '*1600)]+[Message('user','recent'+str(i)) for i in range(5)])
        try:
            scope.prepare_turn(conv)
            assert await agent._compact_for_run(conv) is None
            assert scope.purpose=='finalize' and scope.finish_reason=='token_budget'
            assert await agent._compact_for_run(conv) is None
            permit=await scope.model_control('finalize').before_send(request())
            await scope.model_control('finalize').settled(permit,RequestOutcome('unknown',None,None,1))
            with scope.catalog._db.transaction() as db:
                assert db.execute("SELECT COUNT(*) FROM model_requests WHERE run_id=? AND purpose='compact'",(scope.run_id,)).fetchone()==(0,)
                assert db.execute("SELECT COUNT(*) FROM model_requests WHERE run_id=? AND purpose='finalize'",(scope.run_id,)).fetchone()==(1,)
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


def test_parallel_model_admissions_share_one_hard_reservation_ledger(tmp_path):
    async def run():
        parent=parent_client();scope,_=H['setup_scope'](tmp_path,parent)
        scope.budget=scope.budget.model_copy(update={'total_tokens':1400,'finish_reserve_tokens':200})
        try:
            results=await asyncio.gather(*(scope.model_control('agent').before_send(request()) for _ in range(2)),return_exceptions=True)
            permits=[r for r in results if not isinstance(r,BaseException)]
            assert len(permits)==1 and sum(isinstance(r,BudgetStop) for r in results)==1
            await scope.model_control('agent').settled(permits[0],RequestOutcome('unknown',None,None,1))
            with scope.catalog._db.transaction() as db:
                count,charge=db.execute('SELECT COUNT(*),SUM(charged) FROM model_requests WHERE run_id=?',(scope.run_id,)).fetchone()
            assert count==1 and charge<=1200
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


def test_final_http_deadline_refunds_actual_ledger_and_never_grants_source(tmp_path):
    import httpx
    from codeplus.client import scoped_client
    from codeplus.conversation import ConversationManager, Message, ToolUseBlock, ToolResultBlock
    async def run():
        parent=parent_client();scope,_=H['setup_scope'](tmp_path,parent)
        result,params=await H['source_tool'](scope,'open-before-deadline')
        conv=ConversationManager(history=[
            Message('assistant','',tool_uses=[ToolUseBlock('open-before-deadline','knowledge_open',params)]),
            Message('user','',tool_results=[ToolResultBlock('open-before-deadline',result.output,source_spans=result.source_spans)])])
        seen=[]
        await scope.client.aclose()
        scope.client=scoped_client(parent,transport=httpx.MockTransport(lambda request:seen.append(request)))
        class SlowAdmission(ModelControl):
            async def before_send(self,request):
                permit=await super().before_send(request)
                # Synchronous post-reservation work crosses the deadline without
                # giving asyncio.timeout a scheduling opportunity.
                self.deadline=time.monotonic()+.01
                time.sleep(.03)
                return permit
        try:
            with pytest.raises(BudgetStop,match='time_budget'):
                async for _ in scope.client.stream(conv,control=SlowAdmission(scope,'agent')):pass
            assert seen==[]
            with scope.catalog._db.transaction() as db:
                assert list(db.execute('SELECT state,charged FROM model_requests WHERE run_id=?',(scope.run_id,)))==[('not_sent',0)]
                assert list(db.execute('SELECT status FROM delivery_receipts WHERE run_id=?',(scope.run_id,)))==[('not_sent',)]
                assert db.execute('SELECT COUNT(*) FROM delivered_evidence WHERE run_id=?',(scope.run_id,)).fetchone()==(0,)
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())
