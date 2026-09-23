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
from agentic_rag.adapters.codeplus.ledger import encode

H=runpy.run_path(str(Path(__file__).with_name('test_codeplus_integration.py')))


@pytest.mark.parametrize('difference',['version','range','body','cropped'])
def test_finish_dedup_uses_only_current_version_range_and_body(difference):
    from types import SimpleNamespace
    from codeplus.conversation import Message, ToolUseBlock, ToolResultBlock
    from codeplus.run_policy import SourceSpan
    from agentic_rag.adapters.codeplus.policy import KnowledgeScope
    text='sou' if difference=='cropped' else 'SOURCE' if difference=='body' else 'source'
    start=20 if difference=='range' else 0
    metadata={call:{'items':[{'candidate_id':call,'document_version_id':
        'other' if call=='b' and difference=='version' else 'same',
        'returned_spans':[{'start':0,'end':6}]}]} for call in ('a','b')}
    original=[Message(role='assistant',content='',tool_uses=[ToolUseBlock(call,'knowledge_open',{}) for call in ('a','b')]),
        Message(role='user',content='',tool_results=[
            ToolResultBlock('a','source',source_spans=(SourceSpan('a',0,6,0,6),)),
            ToolResultBlock('b',text,source_spans=(SourceSpan('b',start,start+len(text),0,len(text)),))])]
    # No catalog/archive exists in this scope: only current visible ranges and
    # bodies can be compared, even when old metadata still names the full span.
    kept,removed=KnowledgeScope._deduplicate_source_pairs(SimpleNamespace(source_metadata=metadata),original)
    assert removed==[] and kept==original
    assert [r.content for m in kept for r in m.tool_results]==['source',text]


@pytest.mark.parametrize('completion',[False,True])
@pytest.mark.parametrize('purpose',['citation_repair','finalize'])
@pytest.mark.parametrize('all_fit',[False,True])
def test_finish_keeps_four_visible_sources_by_deduplicating_complete_pairs(tmp_path,monkeypatch,record_property,completion,purpose,all_fit):
    import os
    from uuid import uuid4
    from agentic_rag.adapters.codeplus import policy as P
    from agentic_rag.adapters.codeplus.meter import DeepSeekTextMeter
    from agentic_rag.config import ProcessingSnapshot
    from agentic_rag.ingestion import InputSelection, select_inputs, capture_inputs, process_inputs
    from agentic_rag.storage import Catalog, publication
    tokenizer=os.environ.get('R12_ANSWER_TOKENIZER')
    if not tokenizer:pytest.skip('set R12_ANSWER_TOKENIZER to the pinned real tokenizer file')
    M=runpy.run_path(str(Path(__file__).with_name('test_run_modes.py')))
    helper=M['R']['H']
    bodies={'atlas.md':'# Atlas revised final register\nThe final 2024 Atlas certification was revised. Atlas is now certified for night operation in 2024; its blue safety certificate remains in the ocean registry.\n',
        'audit.md':'# Atlas revised audit\nA later 2024 audit supersedes the earlier prohibition. Atlas is now certified for night operation in 2024.\n',
        'beacon.md':'# Beacon final register\nIn 2024, the Beacon telescope red safety certificate was approved and archived in the hill registry. Beacon was certified for night operation in 2024.\n',
        'old.md':'# Previous year\nIn 2023, Atlas had a green draft certificate in the river registry. This entry does not describe its 2024 certification.\n'}
    async def run():
        catalog=Catalog(tmp_path/'data');base=helper['configuration'](tmp_path/'data')
        kb=catalog.create_library('four source finish fixture').kb_id
        for name,text in bodies.items():(tmp_path/name).write_text(text,encoding='utf-8')
        owner=catalog.begin_import(kb,ProcessingSnapshot.capture(uuid4(),base),
            select_inputs(tuple(InputSelection(path=str(tmp_path/name)) for name in bodies)))
        capture_inputs(catalog,owner);process_inputs(catalog,owner,helper['HELPER']['tokenizer']())
        prepared,_,rows,_,_=helper['unit_ready'](catalog,owner,base)
        publication.publish(catalog,owner,prepared.revision_id);owner.close()
        settings=M['settings'](base,tmp_path)
        knowledge=settings.knowledge.model_dump(mode='json')
        knowledge['retrieval'].update(context_chunks=24,rerank_candidates=24)
        knowledge['budgets']['report'].update(searches=2,finish_reserve_tokens=34000)
        # The full four-source repair measures 7503 with this pinned template;
        # 6500 admits smaller opens but rejects the superset as a whole.
        limit=12000 if all_fit else 6500
        settings=P.DevelopmentConfig.model_validate_json(json.dumps({**settings.model_dump(mode='json'),
            'knowledge':knowledge,'finish_input_upper':limit,'answer_tokenizer':tokenizer}))
        M['host_dependencies'](monkeypatch,settings,rows,[])
        meter=DeepSeekTextMeter(tokenizer,model='deepseek-chat',protocol='openai-compat',base_url='https://api.deepseek.com')
        monkeypatch.setattr(P,'DeepSeekTextMeter',lambda *args,**kwargs:meter)
        provider=ProviderConfig('fixture','openai-compat','https://api.deepseek.com','deepseek-chat','synthetic')
        parent=create_client(provider)
        policy=P.KnowledgePolicy(settings,kb,provider,task_kind='report')
        seen=[]
        def transport(request):
            body=json.loads(request.content);seen.append(body)
            if len(seen)==1:
                content=H['sse_text'](calls=[('search1','knowledge_search',{'query':'Atlas Beacon 2024'}),
                    ('search2','knowledge_search',{'query':'Atlas Beacon certificates'})],terminal='tool_calls')
            elif len(seen)==2:
                source=next(m['content'] for m in body['messages'] if m.get('tool_call_id')=='search2')
                items=json.JSONDecoder().raw_decode(source)[0]['items'];assert len(items)==4
                content=H['sse_text'](calls=[('open'+str(i),'knowledge_open',{'source_ref':item['source_ref']})
                    for i,item in enumerate(items)],terminal='tool_calls')
            elif len(seen)==3:
                assert len([m for m in body['messages'] if m['role']=='tool'])==6
                content=(H['sse_text']('Invalid public JSON') if purpose=='citation_repair' else
                    H['sse_text'](calls=('exhausted','knowledge_search',{'query':'one more'}),terminal='tool_calls'))
            else:
                assert len(seen)==4 and not body.get('tools')
                tools=[m for m in body['messages'] if m['role']=='tool' and '<source ' in m['content']]
                if all_fit:
                    assert len(tools)==1 and tools[0]['tool_call_id']=='search2'
                else:
                    # The complete four-source search cannot fit this window.
                    # A rejected dedup trial must leave smaller opens available.
                    assert 1<=len(tools)<4 and all(m['tool_call_id'].startswith('open') for m in tools)
                uses=[t['id'] for m in body['messages'] for t in m.get('tool_calls',[])]
                assert set(uses)=={m['tool_call_id'] for m in body['messages'] if m['role']=='tool'}
                source='\n'.join(m['content'] for m in tools)
                items=[item for m in tools for item in json.JSONDecoder().raw_decode(m['content'])[0]['items']]
                if all_fit:assert {item['file_name'] for item in items}==set(bodies)
                claims=[]
                for item in items:
                    text=bodies[item['file_name']]
                    assert text in source and item['returned_spans']==[{'schema_version':1,'start':0,'end':len(text)}]
                    claims.append({'evidence_id':item['evidence_id'],'spans':item['returned_spans'],'quotes':[text]})
                assert meter.input_upper_bound(bytes(request.content),output_cap=1000)<=limit
                with catalog._db.transaction() as db:
                    payload=json.loads(db.execute("SELECT payload FROM delivery_receipts WHERE status='prepared' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
                assert len(payload['mappings'])==len(items)
                content=H['sse_text'](json.dumps({'markdown':'四份当前来源 '+''.join('[^'+c['evidence_id']+']' for c in claims),'citations':claims}))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content.replace(b'fixture-response',b'deepseek-flash'))
        monkeypatch.setattr(P,'scoped_client',lambda c:scoped_client(c,transport=httpx.MockTransport(transport)))
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=policy)
        try:
            question='仅比较2024年的Atlas与Beacon蓝色/红色安全证书、ocean/hill登记处及夜间资格；排除2023，保留审计更正。'
            if completion:await agent.run_to_completion(question)
            else:
                conv=ConversationManager();conv.add_user_message(question)
                async with aclosing(agent.run(conv)) as stream:
                    async for _ in stream:pass
            assert len(seen)==4
            assert len(agent.last_run_outcome.artifact.citation_ids)==(4 if all_fit else
                len([m for m in seen[-1]['messages'] if m['role']=='tool' and '<source ' in m['content']]))
            assert agent.last_run_outcome.status==('completed' if purpose=='citation_repair' else 'partial')
            transform=next(item for item in policy.scope.window_transforms
                           if item['purpose']==purpose and len(item['before_candidates'])==12)
            assert len(transform['before_candidates'])==12
            if all_fit:
                assert len(transform['after_candidates'])==4
                assert set(transform['deduplicated_tool_call_ids'])=={'search1','open0','open1','open2','open3'}
                assert transform['input_upper_before_dedup']>limit>=transform['input_upper_after_dedup']
            else:
                assert 'deduplicated_tool_call_ids' not in transform
                assert 0<len(transform['after_candidates'])<4
            record_property('finish_window',json.dumps(transform))
        finally:await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('completion',[False,True])
@pytest.mark.parametrize('spill',[False,True])
@pytest.mark.parametrize('kind',['search','open'])
def test_full_json_crop_preserves_wire_body_positions_and_unseen_tail(tmp_path,monkeypatch,completion,spill,kind):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        raw=('# Heading\n'+('汉字 "quoted" \\\n'*500)).encode()
        scope,canonical=H['setup_scope'](tmp_path,parent,raw=raw,context_tokens=8000)
        # Exploration no longer carries the 41-byte JSON output field, so
        # restore the original prose allowance inside the same 8000-byte window.
        padding='Source check. '*140
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
                content=H['sse_text'](padding,calls=('read','knowledge_'+kind,args),terminal='tool_calls')
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
