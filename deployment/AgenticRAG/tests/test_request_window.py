"""Complete request fitting through the two real host loops and SDK hook."""
import asyncio
from contextlib import aclosing
import json
from pathlib import Path
import runpy
from uuid import UUID

import httpx
import pytest

from codeplus.agent import Agent
from codeplus.client import create_client, scoped_client
from codeplus.config import ProviderConfig
from codeplus.conversation import ConversationManager
from codeplus.run_policy import BudgetStop
from codeplus.tools import ToolRegistry
from agentic_rag.domain import Span, RagError

H=runpy.run_path(str(Path(__file__).with_name('test_codeplus_integration.py')))


@pytest.mark.parametrize('completion',[False,True])
@pytest.mark.parametrize('spill',[False,True])
@pytest.mark.parametrize('kind',['search','open'])
def test_full_json_crop_preserves_wire_body_positions_and_unseen_tail(tmp_path,monkeypatch,completion,spill,kind):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        raw=('# Heading\n'+('汉字 "quoted" \\\n'*500)).encode()
        scope,canonical=H['setup_scope'](tmp_path,parent,raw=raw,context_tokens=8000)
        reference=scope.sources.issue_source(scope._fixture_ref)
        before=[];wire=[];seen=[]
        original=scope._trim_exploration
        def fit(conversation):
            initial=scope._exploration_window(conversation.history)
            before.append({'size':len(initial[1]),'spans':[m.source for m in initial[3]],
                           'contents':[r.content for message in conversation.history for r in message.tool_results]})
            return original(conversation)
        scope._trim_exploration=fit
        def transport(request):
            body=json.loads(request.content);seen.append(body)
            assert len(request.content)<=8000
            if len(seen)==1:
                args={'query':'quoted source'} if kind=='search' else {'source_ref':reference}
                content=H['sse_text']('Source check. '*140,calls=('read','knowledge_'+kind,args),terminal='tool_calls')
            else:
                source=next(m['content'] for m in body['messages'] if m['role']=='tool' and '<source ' in m['content'])
                metadata=json.JSONDecoder().raw_decode(source)[0]
                if len(seen)==2:assert metadata['host_cropped'] is True
                with scope.catalog._db.transaction() as db:
                    prepared=json.loads(db.execute("SELECT payload FROM delivery_receipts WHERE status='prepared' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
                mapped=[m for m in prepared['mappings'] if m['tool_call_id']==('read' if len(seen)==2 else 'tail')]
                assert mapped and len(mapped)==len(metadata['items'])
                for mapping,item in zip(mapped,metadata['items'],strict=True):
                    span=mapping['source_span'];position=mapping['body_span']
                    text=source[position['start']:position['end']]
                    assert text==canonical[span['start']:span['end']]
                    assert item['returned_spans']==[span]
                    assert span['end']<len(canonical)
                    wire.append((item,span,text))
                item,span,text=wire[-1]
                if kind=='open':
                    state=scope.sources._resolve(metadata['next_cursor'],'cursor')
                    assert state['next_cp']==span['end']
                if kind=='open' and len(seen)==2:
                    content=H['sse_text'](calls=('tail','knowledge_open',{'source_ref':item['source_ref'],
                        'cursor':metadata['next_cursor']}),terminal='tool_calls')
                else:
                    if kind=='open':
                        old_item,old_span,old_text=wire[0]
                        assert span['start']==old_span['end']
                        assert not any(m['tool_call_id']=='read' for m in prepared['mappings'])
                        scope.citations.save(UUID(old_item['evidence_id']),(Span.model_validate(old_span),),(old_text,))
                        with pytest.raises(RagError):scope.citations.save(UUID(item['evidence_id']),(Span.model_validate(span),),(text,))
                    content=H['sse_text'](json.dumps({'markdown':'Actual body [^'+item['evidence_id']+']',
                        'citations':[{'evidence_id':item['evidence_id'],'spans':[span],'quotes':[text]}]}))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            async def start(self,context):return scope
        if spill:
            # Actual host persistence + actual preview provenance; the body is
            # still large enough to need a second, complete-request fitting pass.
            monkeypatch.setattr('codeplus.agent.MAX_OUTPUT_CHARS',1800)
            monkeypatch.setattr('codeplus.context.manager.PREVIEW_CHARS',3000)
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy())
        agent.session_dir.mkdir(parents=True,exist_ok=True)
        try:
            if completion:await agent.run_to_completion('Read the quoted Chinese source')
            else:
                conversation=ConversationManager();conversation.add_user_message('Read the quoted Chinese source')
                async with aclosing(agent.run(conversation)) as stream:
                    async for _ in stream:pass
            assert len(seen)==(3 if kind=='open' else 2) and agent.last_run_outcome.status=='completed'
            assert any(item['size']>8000 for item in before)
            if spill:assert any('<persisted-output>' in text for item in before for text in item['contents'])
            assert scope.window_transforms and scope.window_transforms[-1]['raw_body_bytes']<=8000
            with scope.catalog._db.transaction() as db:
                evidence=[json.loads(r[0]) for r in db.execute('SELECT payload FROM delivered_evidence')]
            assert evidence
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('completion',[False,True])
def test_minimum_complete_request_still_hard_stops_without_transport(tmp_path,completion):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,_=H['setup_scope'](tmp_path,parent,context_tokens=1000)
        sent=[]
        await scope.client.aclose()
        scope.client=scoped_client(parent,transport=httpx.MockTransport(lambda request:sent.append(request)))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy())
        try:
            if completion:await agent.run_to_completion('Cannot fit mandatory request')
            else:
                conversation=ConversationManager();conversation.add_user_message('Cannot fit mandatory request')
                async with aclosing(agent.run(conversation)) as stream:
                    async for _ in stream:pass
            assert sent==[] and agent.last_run_outcome.reason=='context_limit'
            with scope.catalog._db.transaction() as db:assert db.execute('SELECT count(*) FROM model_requests').fetchone()==(0,)
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('completion',[False,True])
@pytest.mark.parametrize('refusal',['open_limit','invalid_cursor','cross_source','near_token_limit'])
def test_refused_page_keeps_confirmed_old_body_for_finish(tmp_path,completion,refusal):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,canonical=H['setup_scope'](tmp_path,parent,raw=('# Heading\n'+'source line\n'*700).encode(),
            context_tokens=8000,opens=1 if refusal=='open_limit' else 100)
        reference=scope.sources.issue_source(scope._fixture_ref)
        other=scope.sources.issue_source(scope._fixture_ref)
        seen=[];old=[]
        def transport(request):
            body=json.loads(request.content);seen.append(body)
            if len(seen)==1:
                content=H['sse_text'](calls=('page','knowledge_open',{'source_ref':reference}),terminal='tool_calls')
            else:
                source=next(m['content'] for m in body['messages'] if m['role']=='tool' and '<source ' in m['content'])
                metadata=json.JSONDecoder().raw_decode(source)[0];item=metadata['items'][0]
                with scope.catalog._db.transaction() as db:
                    receipt=json.loads(db.execute("SELECT payload FROM delivery_receipts WHERE status='prepared' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
                mapped=next(m for m in receipt['mappings'] if m['tool_call_id']=='page')
                span=mapped['source_span'];text=source[mapped['body_span']['start']:mapped['body_span']['end']]
                assert text==canonical[span['start']:span['end']]
                if len(seen)==2:
                    old.append(item['evidence_id'])
                    assert metadata['next_cursor']
                    if refusal=='near_token_limit':
                        with scope.catalog._db.transaction(write=True) as db:
                            db.execute('UPDATE source_usage SET returned_tokens=? WHERE run_id=?',
                                (scope.budget.total_tokens-scope.budget.finish_reserve_tokens-1,scope.run_id))
                    content=H['sse_text'](calls=('refused','knowledge_open',{
                        'source_ref':other if refusal=='cross_source' else reference,
                        'cursor':'cur_invalid' if refusal=='invalid_cursor' else metadata['next_cursor']}),terminal='tool_calls')
                else:
                    if refusal=='near_token_limit':
                        assert any(event['purpose']=='open_cursor_rollback' for event in scope.window_transforms)
                    else:assert not any(event['purpose']=='open_cursor' for event in scope.window_transforms)
                    assert item['evidence_id']==old[0]
                    content=H['sse_text'](json.dumps({'markdown':'Available evidence [^'+item['evidence_id']+']',
                        'citations':[{'evidence_id':item['evidence_id'],'spans':[span],'quotes':[text]}]}))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy())
        try:
            if completion:await agent.run_to_completion('Read the source')
            else:
                conversation=ConversationManager();conversation.add_user_message('Read the source')
                async with aclosing(agent.run(conversation)) as stream:
                    async for _ in stream:pass
            assert len(seen)==3
            assert agent.last_run_outcome.status==('partial' if refusal in {'open_limit','near_token_limit'} else 'completed')
            assert scope.sources.usage()['opens']==(1 if refusal=='open_limit' else 2)
            if refusal=='open_limit':assert agent.last_run_outcome.reason=='open_limit'
            if refusal=='near_token_limit':assert agent.last_run_outcome.reason=='token_budget'
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())
