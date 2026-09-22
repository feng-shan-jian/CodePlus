"""Real host loops, SQLite/archive and SDK; synthetic model/index transport.

Network/GPU/Milvus acceptance is separate in the R12 live helper.
"""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import runpy
import time

import httpx
import pytest

from codeplus.agent import Agent, StreamText, ThinkingText, LoopComplete
from codeplus.client import create_client, scoped_client
from codeplus.config import ProviderConfig
from codeplus.conversation import ConversationManager
from codeplus.run_policy import HostRunContext
from codeplus.tools import ToolRegistry

from agentic_rag.config import KnowledgeConfig, WorkerExecutionConfig, resolve_run
from agentic_rag.domain import RunStatus
from agentic_rag.adapters.codeplus.policy import DevelopmentConfig, KnowledgeScope

H = runpy.run_path(str(Path(__file__).with_name('source_support.py')))


def protocol_response(protocol, text='ok'):
    if protocol=='openai-compat':return sse_text(text)
    if protocol=='anthropic':
        events=[{'type':'message_start','message':{'id':'m','type':'message','role':'assistant','content':[],
            'model':'fixture-response','usage':{'input_tokens':10,'output_tokens':0,'cache_read_input_tokens':0,'cache_creation_input_tokens':0}}},
            {'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':1}}, {'type':'message_stop'}]
    else:
        events=[{'type':'response.completed','sequence_number':0,'response':{'id':'r','object':'response','created_at':1,
            'model':'fixture-response','status':'completed','output':[],
            'usage':{'input_tokens':10,'output_tokens':1,'total_tokens':11}}}]
    return ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()


async def source_tool(scope, call_id, kind='open'):
    from codeplus.tools.base import ToolCallComplete
    tool=scope.registry.get('knowledge_'+kind)
    params={'source_ref':scope.sources.issue_source(scope._fixture_ref)} if kind=='open' else {'query':'source'}
    scope.rejected_tool(call_id,tool.name,'not_admitted')
    permit=await scope.admit_tool(call_id,tool.name,params)
    result=await tool.execute(tool.params_model.model_validate(params))
    await scope.tool_finished(permit,result)
    assert not result.is_error
    return result,params


class ControlledMeter:
    """Test-only body byte unit. Never selected by the production policy."""
    identity = 'controlled-test-http-bytes'
    model = 'fixture'
    response_model = 'fixture-response'
    context_window = 1000000
    def count(self, text): return len(text.encode())
    def input_upper_bound(self, raw_body, *, output_cap): return len(raw_body)
    def frozen_identity(self): return {'meter':self.identity}


def sse_text(content='', *, calls=None, terminal='stop', reasoning=None):
    delta = {'content':content}
    if calls:
        sequence=calls if isinstance(calls,list) else [calls]
        delta['tool_calls'] = [{'index':i,'id':call[0],'type':'function',
            'function':{'name':call[1],'arguments':json.dumps(call[2])}} for i,call in enumerate(sequence)]
    if reasoning:
        delta['reasoning_content'] = reasoning
    chunk = {'id':'m','object':'chat.completion.chunk','created':1,'model':'fixture-response',
        'choices':[{'index':0,'delta':delta,'finish_reason':terminal}],
        'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
    return ('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode()


def setup_scope(tmp_path, parent, raw=None):
    kwargs = {'raw':raw} if raw is not None else {}
    catalog, old, source_session, source, version, chunks, ref = H['fixture'](tmp_path, context_tokens=50000, **kwargs)
    base = old.run.resolved_config.knowledge.model_dump(mode='json')
    old.close()
    base['budgets']['qa'].update(finish_reserve_tokens=20000)
    conf = KnowledgeConfig.model_validate_json(json.dumps(base))
    lease = catalog.start_run(ref.kb_id, resolve_run(conf, 'qa'))
    settings = DevelopmentConfig(knowledge=conf, worker=WorkerExecutionConfig(executable=str(tmp_path/'python'),
        model_cache=str(tmp_path/'models'), runtime_dir=str(tmp_path/'runtime')), answer_tokenizer=str(tmp_path/'tokenizer'),
        explore_output_cap=1000,finish_input_upper=8000,finalize_output_cap=1000,repair_output_cap=1000,
        compact_output_cap=1000,max_iterations=12,max_tool_attempts=20,cleanup_grace_ms=100)
    context = HostRunContext('test','session',str(tmp_path),'openai-compat',parent,None,None)
    scope = KnowledgeScope(settings,catalog,lease,ControlledMeter(),context,time.monotonic())
    H['dense_fixture'](scope.sources,chunks,version,ref)
    scope._fixture_ref=ref
    return scope, chunks.parsed.text


@pytest.mark.parametrize('completion', [False, True])
@pytest.mark.parametrize('repair', [False, True])
@pytest.mark.parametrize('parallel', [False, True])
def test_real_host_loops_gate_answer_and_same_loop_single_repair(tmp_path, completion, repair,parallel):
    async def run():
        parent = create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope, canonical = setup_scope(tmp_path, parent)
        if parallel:scope.registry.get('knowledge_search').is_concurrency_safe=True
        seen = []
        def transport(request):
            body = json.loads(request.content)
            seen.append(body)
            if len(seen) == 1:
                calls=[('s1','knowledge_search',{'query':'source'})]
                if parallel:calls.append(('s2','knowledge_search',{'query':'second source query'}))
                content = sse_text('UNVALIDATED DRAFT',calls=calls, terminal='tool_calls',reasoning='PRIVATE THOUGHT')
            else:
                tool_text = next(m['content'] for m in body['messages'] if m['role']=='tool' and '<source ' in m['content'])
                metadata = json.loads(tool_text.split('\n\n<source ',1)[0])
                item = metadata['items'][0]
                answer = {'markdown':'Grounded answer [^'+item['evidence_id']+']',
                    'citations':[{'evidence_id':item['evidence_id'],'spans':item['returned_spans'],
                                  'quotes':[canonical if not repair or len(seen)>2 else 'wrong quote']} ]}
                content = sse_text(json.dumps(answer))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        await scope.client.aclose()
        scope.client = scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            async def start(self, context): return scope
        registry = ToolRegistry()
        agent = Agent(parent,registry,'openai-compat',work_dir=str(tmp_path),execution_policy=Policy())
        events = []
        try:
            if completion:
                answer = await agent.run_to_completion('source question',event_callback=events.append)
                displayed = ''.join(event.get('text','') for event in events)
            else:
                conv = ConversationManager();conv.add_user_message('source question')
                from contextlib import aclosing
                async with aclosing(agent.run(conv)) as stream:
                    events.extend([event async for event in stream])
                displayed = ''.join(event.text for event in events if isinstance(event,(StreamText,ThinkingText)))
                answer = displayed
            assert 'UNVALIDATED' not in displayed and 'PRIVATE' not in displayed
            assert 'Grounded answer' in answer
            assert agent.last_run_outcome.status == 'completed'
            assert scope.catalog.get_pin(scope.lease.run.run_id).state == 'released'
            assert scope.catalog.get_run(scope.lease.run.run_id).usage.searches == (2 if parallel else 1)
            assert len(seen) == (3 if repair else 2)
            if repair:
                assert not seen[-1].get('tools')
            assert agent.client is parent and agent.registry is registry
        finally:
            await scope.aclose()
            await parent._client.close()
    asyncio.run(run())


def test_two_live_agent_scopes_overlap_without_client_budget_or_source_leak(tmp_path):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scopes=[];agents=[];arrived=0;both=asyncio.Event()
        for label in ('alpha','beta'):
            folder=tmp_path/label;folder.mkdir()
            scope,canonical=setup_scope(folder,parent,raw=(label+' isolated source\n').encode())
            await scope.client.aclose()
            def bind(current,text,name):
                calls=0
                async def transport(request):
                    nonlocal calls,arrived
                    calls+=1
                    body=json.loads(request.content)
                    if calls==1:
                        arrived+=1
                        if arrived==2:both.set()
                        await asyncio.wait_for(both.wait(),2)
                        response=sse_text(calls=('search-'+name,'knowledge_search',{'query':name}),terminal='tool_calls')
                    else:
                        source=next(m['content'] for m in body['messages'] if m['role']=='tool')
                        item=json.loads(source.split('\n\n<source ',1)[0])['items'][0]
                        assert name+' isolated source' in source
                        assert ('beta' if name=='alpha' else 'alpha')+' isolated source' not in source
                        response=sse_text(json.dumps({'markdown':name+' [^'+item['evidence_id']+']',
                            'citations':[{'evidence_id':item['evidence_id'],'spans':item['returned_spans'],'quotes':[text]}]}))
                    return httpx.Response(200,headers={'content-type':'text/event-stream'},content=response)
                class Policy:
                    async def start(self,context):return current
                return transport,Policy()
            transport,policy=bind(scope,canonical,label)
            scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
            scopes.append(scope);agents.append(Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(folder),execution_policy=policy))
        try:
            answers=await asyncio.gather(*(agent.run_to_completion(label) for agent,label in zip(agents,('alpha','beta'))))
            assert arrived==2 and all(name in answer for name,answer in zip(('alpha','beta'),answers))
            assert parent._client.max_retries==2
            for scope,agent in zip(scopes,agents):
                assert agent.client is parent and not agent._executing and agent.last_run_outcome.status=='completed'
                assert scope.catalog.get_run(scope.lease.run.run_id).usage.searches==1
                assert scope.catalog.get_pin(scope.lease.run.run_id).state=='released'
                with scope.catalog._db.transaction() as db:
                    assert db.execute('SELECT COUNT(*) FROM model_requests WHERE run_id=?',(scope.run_id,)).fetchone()==(2,)
        finally:
            for scope in scopes:await scope.aclose()
            await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('completion',[False,True])
@pytest.mark.parametrize('still_invalid',[False,True])
def test_one_repair_receives_all_mixed_citation_errors_without_partial_publication(tmp_path,still_invalid,completion):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,canonical=setup_scope(tmp_path,parent)
        seen=[]
        def transport(request):
            body=json.loads(request.content);seen.append(body)
            if len(seen)==1:
                return httpx.Response(200,headers={'content-type':'text/event-stream'},content=sse_text(
                    calls=[('s'+str(i),'knowledge_search',{'query':str(i)}) for i in range(3)],terminal='tool_calls'))
            items=[json.loads(m['content'].split('\n\n<source ',1)[0])['items'][0]
                   for m in body['messages'] if m['role']=='tool' and '<source ' in m['content']]
            ids=[v['evidence_id'] for v in items]
            if len(seen)==3:
                feedback=body['messages'][-1]['content']
                assert feedback.count('citation_quote_mismatch evidence='+ids[1])==2
                assert 'citation_range_unconfirmed evidence='+ids[2] in feedback
                assert 'unknown, missing or unused citation marker' in feedback
                assert not body.get('tools')
                with scope.catalog._db.transaction() as db:
                    assert db.execute('SELECT COUNT(*) FROM saved_citations WHERE run_id=?',(scope.run_id,)).fetchone()==(0,)
            citations=[{'evidence_id':v['evidence_id'],'spans':v['returned_spans'],'quotes':[canonical]} for v in items]
            if len(seen)==2 or still_invalid:
                citations[1]['spans']=[{'start':0,'end':3},{'start':len(canonical)-3,'end':len(canonical)}]
                citations[1]['quotes']=['wrong prefix','wrong suffix']
                citations[2]['spans']=[{'start':0,'end':len(canonical)+1}]
            answer={'markdown':'MIXED DRAFT '+''.join('[^'+v+']' for v in ids),'citations':citations}
            if len(seen)==2 or still_invalid:answer['markdown']+='[^unknown-marker]'
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=sse_text(json.dumps(answer)))
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy())
        events=[]
        try:
            if completion:
                answer=await agent.run_to_completion('compare sources',event_callback=events.append)
            else:
                conv=ConversationManager();conv.add_user_message('compare sources')
                from contextlib import aclosing
                async with aclosing(agent.run(conv)) as stream:
                    events.extend([event async for event in stream])
                answer=''.join(e.text for e in events if isinstance(e,(StreamText,ThinkingText)))
            assert len(seen)==3
            assert agent.last_run_outcome.status==('incomplete' if still_invalid else 'completed')
            assert ('MIXED DRAFT' in answer) is (not still_invalid)
            if still_invalid:
                assert not any('MIXED DRAFT' in str(e) for e in events)
                assert agent.last_run_outcome.artifact is None
                with scope.catalog._db.transaction() as db:
                    assert db.execute('SELECT COUNT(*) FROM saved_citations WHERE run_id=?',(scope.run_id,)).fetchone()==(0,)
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('field,value',[
    ('evidence_id',123),('evidence_id',{}),('evidence_id',None),
    ('quotes','AB'),('quotes',{'A':0,'B':0}),('quotes',[0]),
    ('spans','AB'),('spans',{}),('spans',[None]),
    ('spans',[{'start':True,'end':2}]),('markdown',None),('markdown',{}),
])
def test_malformed_json_citation_shape_is_repairable_but_never_published(tmp_path,field,value):
    from codeplus.conversation import Message,ToolResultBlock,ToolUseBlock
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,canonical=setup_scope(tmp_path,parent,raw=b'AB')
        result,params=await source_tool(scope,'shape-source')
        item=json.loads(result.output.split('\n\n<source ',1)[0])['items'][0]
        conv=ConversationManager(history=[Message('assistant','',tool_uses=[ToolUseBlock('shape-source','knowledge_open',params)]),
            Message('user','',tool_results=[ToolResultBlock('shape-source',result.output,source_spans=result.source_spans)])])
        await scope.client.aclose()
        scope.client=scoped_client(parent,transport=httpx.MockTransport(lambda request:httpx.Response(
            200,headers={'content-type':'text/event-stream'},content=sse_text('not displayed'))))
        try:
            async for _ in scope.client.stream(conv,control=scope.model_control('agent')):pass
            citation={'evidence_id':item['evidence_id'],'spans':[{'start':0,'end':1},{'start':1,'end':2}],'quotes':['A','B']}
            draft={'markdown':'SHAPE DRAFT [^'+item['evidence_id']+']','citations':[citation]}
            if field=='markdown':draft[field]=value
            else:citation[field]=value
            first=await scope.assess_output(json.dumps(draft),'end_turn')
            assert first.action=='repair'
            second=await scope.assess_output(json.dumps(draft),'end_turn')
            assert second.action=='stop' and second.message=='citation_invalid'
            assert scope.outcome is None
            with scope.catalog._db.transaction() as db:
                assert db.execute('SELECT COUNT(*) FROM saved_citations WHERE run_id=?',(scope.run_id,)).fetchone()==(0,)
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('completion',[False,True])
@pytest.mark.parametrize('defect',['null_markdown','unknown_marker','duplicate_claim','integer_id'])
def test_both_agent_entries_validate_all_draft_relations_before_saving(tmp_path,completion,defect):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,canonical=setup_scope(tmp_path,parent);seen=[]
        def transport(request):
            body=json.loads(request.content);seen.append(body)
            if len(seen)==1:content=sse_text(calls=('s','knowledge_search',{'query':'source'}),terminal='tool_calls')
            else:
                source=next(m['content'] for m in body['messages'] if m['role']=='tool' and '<source ' in m['content'])
                item=json.loads(source.split('\n\n<source ',1)[0])['items'][0]
                claim={'evidence_id':item['evidence_id'],'spans':item['returned_spans'],'quotes':[canonical]}
                draft={'markdown':('BAD DRAFT' if len(seen)==2 else 'VALID ANSWER')+' [^'+item['evidence_id']+']','citations':[claim]}
                if len(seen)==2:
                    if defect=='null_markdown':draft['markdown']=None
                    elif defect=='unknown_marker':draft['markdown']+='[^unconfirmed]'
                    elif defect=='duplicate_claim':draft['citations'].append(dict(claim))
                    else:claim['evidence_id']=123
                else:
                    assert not body.get('tools')
                    with scope.catalog._db.transaction() as db:
                        assert db.execute('SELECT COUNT(*) FROM saved_citations WHERE run_id=?',(scope.run_id,)).fetchone()==(0,)
                content=sse_text(json.dumps(draft))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy());events=[]
        try:
            if completion:
                answer=await agent.run_to_completion('source question',event_callback=events.append)
            else:
                from contextlib import aclosing
                conv=ConversationManager();conv.add_user_message('source question')
                async with aclosing(agent.run(conv)) as stream:events.extend([event async for event in stream])
                answer=''.join(e.text for e in events if isinstance(e,(StreamText,ThinkingText)))
            assert len(seen)==3 and 'VALID ANSWER' in answer
            assert 'BAD DRAFT' not in str(events) and agent.last_run_outcome.status=='completed'
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('protocol,transform', [('openai-compat','single'),('openai','aggregate'),('anthropic','blocks')])
def test_actual_source_spill_pairing_sdk_and_gateway_authorize_exact_intersection(tmp_path,monkeypatch,protocol,transform):
    from dataclasses import replace
    from codeplus.conversation import Message,ToolResultBlock,ToolUseBlock
    from codeplus.context.manager import apply_tool_result_budget,persisted_preview_spans
    from agentic_rag.evidence import read_evidence
    from agentic_rag.domain import Span,RagError
    from uuid import UUID
    async def run():
        parent=create_client(ProviderConfig('fixture',protocol,'https://fixture.invalid','fixture','synthetic'))
        raw=('# Heading\n'+('A甲😀"\\ repeat\n'*500)).encode()
        scope,canonical=setup_scope(tmp_path,parent,raw)
        (tmp_path/'spill').mkdir()
        seen=[]
        def transport(request):
            seen.append(bytes(request.content))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=protocol_response(protocol))
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        try:
            result,params=await source_tool(scope,'actual')
            meta=json.loads(result.output.split('\n\n<source ',1)[0])['items'][0]
            block=ToolResultBlock('actual',result.output,source_spans=result.source_spans)
            # Scale only the spill trigger to keep this formal fixture small;
            # the production 2000-codepoint preview and transforms stay intact.
            if transform=='single':
                monkeypatch.setattr('codeplus.agent.MAX_OUTPUT_CHARS',4000)
                agent=Agent(parent,ToolRegistry(),protocol,work_dir=str(tmp_path))
                agent.session_dir.mkdir(parents=True,exist_ok=True)
                block.content=agent._maybe_persist_or_truncate('actual',result.output)
                block.source_spans=agent._result_spans('actual',result,block.content)
            elif transform=='aggregate':
                monkeypatch.setattr('codeplus.context.manager.AGGREGATE_CHAR_LIMIT',4000)
                apply_tool_result_budget([block],tmp_path/'spill')
            else:
                block.content_blocks=[{'type':'text','text':result.output},{'type':'tool_reference','tool_name':'knowledge_open'}]
                block.source_spans=tuple(replace(span,block_index=0) for span in result.source_spans)
                monkeypatch.setattr('codeplus.context.manager.AGGREGATE_CHAR_LIMIT',4000)
                apply_tool_result_budget([block],tmp_path/'spill')
                assert len(block.content)<len(block.content_blocks[0]['text'])
            conv=ConversationManager(history=[Message('user','first "\\甲😀'),Message('user','merged user'),
                Message('user','',tool_results=[ToolResultBlock('orphan',result.output,source_spans=result.source_spans)]),
                Message('assistant','prefix',tool_uses=[ToolUseBlock('missing','knowledge_open',{}),ToolUseBlock('actual','knowledge_open',params)]),
                Message('user','',tool_results=[block])])
            output=[e async for e in scope.client.stream(conv,system='metered system',control=scope.model_control('agent'))]
            assert len(seen)==1
            with scope.catalog._db.transaction() as db:
                receipt=json.loads(db.execute("SELECT payload FROM delivery_receipts WHERE run_id=? AND status='confirmed'",(scope.run_id,)).fetchone()[0])
            assert len(receipt['mappings'])==1 and receipt['mappings'][0]['tool_call_id']=='actual'
            mapped=receipt['mappings'][0];body=json.loads(seen[0]);node=body
            for part in mapped['json_path']:node=node[part]
            source_span=Span.model_validate(mapped['source_span']);wire_span=mapped['body_span']
            quote=canonical[source_span.start:source_span.end]
            assert node[wire_span['start']:wire_span['end']]==quote
            scope.citations.validate(UUID(meta['evidence_id']),(source_span,),(quote,))
            if transform!='blocks':
                assert source_span.end<len(canonical)
                with pytest.raises(RagError):
                    scope.citations.validate(UUID(meta['evidence_id']),(Span(start=len(canonical)-2,end=len(canonical)),),(canonical[-2:],))
            else:
                assert mapped['json_path'][-2:]==[0,'text']
                assert source_span.end==len(canonical)
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


def test_actual_compact_never_grants_pending_prefix_and_only_real_tail_later_grants(tmp_path):
    from codeplus.conversation import Message,ToolResultBlock,ToolUseBlock
    from codeplus.context.manager import auto_compact
    from uuid import UUID
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,canonical=setup_scope(tmp_path,parent)
        def pair(call,result,params):
            return [Message('assistant','',tool_uses=[ToolUseBlock(call,'knowledge_open',params)]),
                Message('user','',tool_results=[ToolResultBlock(call,result.output,source_spans=result.source_spans)])]
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(lambda request:
            httpx.Response(200,headers={'content-type':'text/event-stream'},content=sse_text('<summary>compressed, no original source body</summary>'))))
        try:
            old,op=await source_tool(scope,'old');pending,pp=await source_tool(scope,'prefix');tail,tp=await source_tool(scope,'tail')
            # Confirm old evidence first; it survives later history compaction.
            conv=ConversationManager(history=[Message('user','question')]+pair('old',old,op))
            _=[e async for e in scope.client.stream(conv,control=scope.model_control('agent'))]
            conv=ConversationManager(history=[Message('user','ordinary prefix '*750)]+pair('old',old,op)+pair('prefix',pending,pp)+
                [Message('user','recent1'),Message('assistant','recent2'),Message('user','recent3')]+pair('tail',tail,tp))
            result=await auto_compact(conv,scope.client,1000000,tmp_path/'spill',protocol='openai-compat',manual=True,
                control_factory=scope.model_control)
            assert result is not None and not isinstance(result,str)
            def evidence_ids():
                with scope.catalog._db.transaction() as db:return {row[0] for row in db.execute('SELECT evidence_id FROM delivered_evidence WHERE run_id=?',(scope.run_id,))}
            eid=lambda r:json.loads(r.output.split('\n\n<source ',1)[0])['items'][0]['evidence_id']
            assert evidence_ids()=={eid(old)}
            _=[e async for e in scope.client.stream(conv,control=scope.model_control('agent'))]
            assert evidence_ids()=={eid(old),eid(tail)}
            assert eid(pending) not in evidence_ids()
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('completion',[False,True])
@pytest.mark.parametrize('scenario',['no_hits','tool_failure','denied','unknown','invalid'])
def test_host_loops_never_release_ungrounded_output_or_count_rejected_tool(tmp_path,completion,scenario):
    from codeplus.permissions import DangerousCommandDetector,PathSandbox,PermissionChecker,RuleEngine
    from codeplus.conversation import ConversationManager
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,_=setup_scope(tmp_path,parent)
        calls=[]
        if scenario in {'no_hits','tool_failure'}:
            def search(query,**kwargs):
                if scenario=='tool_failure':raise ConnectionError('synthetic dependency failure')
                return {'hits':[]}
            scope.sources.dense.search=search
        name='Bash' if scenario=='unknown' else 'knowledge_search'
        args={'query':'' if scenario=='invalid' else 'source'}
        def transport(request):
            calls.append(json.loads(request.content))
            body=sse_text('PRIVATE DRAFT',calls=('attempt',name,args),terminal='tool_calls') if len(calls)==1 else sse_text('UNSUPPORTED FINAL')
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=body)
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        checker=None
        if scenario=='denied':
            rules=tmp_path/'rules.yaml';rules.write_text('- rule: "knowledge_search(*)"\n  effect: deny\n')
            checker=PermissionChecker(DangerousCommandDetector(),PathSandbox(str(tmp_path)),RuleEngine(local_rules_path=rules))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy(),permission_checker=checker)
        try:
            if completion:text=await agent.run_to_completion('question')
            else:
                conv=ConversationManager();conv.add_user_message('question')
                events=[event async for event in agent.run(conv)]
                text=''.join(event.text for event in events if isinstance(event,(StreamText,ThinkingText)))
            assert 'PRIVATE' not in text and 'UNSUPPORTED' not in text
            assert agent.last_run_outcome.status=='incomplete'
            assert agent.last_run_outcome.reason=='no_evidence'
            usage=scope.catalog.get_run(scope.lease.run.run_id).usage
            assert usage.searches==(1 if scenario in {'no_hits','tool_failure'} else 0)
            assert scope.catalog.get_pin(scope.lease.run.run_id).state=='released'
            assert len(calls)==2
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


def test_actual_agent_permission_event_closed_before_resume_revokes_future(tmp_path):
    from codeplus.agent import PermissionRequest
    from codeplus.permissions import DangerousCommandDetector,PathSandbox,PermissionChecker,RuleEngine
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,_=setup_scope(tmp_path,parent);calls=[]
        def transport(request):
            calls.append(request)
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=sse_text(calls=('ask','knowledge_search',{'query':'source'}),terminal='tool_calls'))
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        rules=tmp_path/'rules.yaml';rules.write_text('- rule: "knowledge_search(*)"\n  effect: ask\n')
        checker=PermissionChecker(DangerousCommandDetector(),PathSandbox(str(tmp_path)),RuleEngine(local_rules_path=rules))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy(),permission_checker=checker)
        conv=ConversationManager();conv.add_user_message('question');stream=agent.run(conv)
        try:
            async for event in stream:
                if isinstance(event,PermissionRequest):break
            assert isinstance(event,PermissionRequest) and not event.future.done()
            await stream.aclose()
            assert event.future.cancelled()
            assert scope.catalog.get_run(scope.lease.run.run_id).usage.searches==0
            assert scope.catalog.get_pin(scope.lease.run.run_id).state=='released'
            assert len(calls)==1
        finally:
            await stream.aclose();await parent._client.close()
    asyncio.run(run())


def test_actual_tui_permission_identity_and_busy_during_cleanup(tmp_path,monkeypatch):
    from codeplus.app import CodePlusApp,ChatInput
    from codeplus.agent import PermissionRequest,PermissionResponse
    from codeplus.permission_dialog import InlinePermissionWidget
    monkeypatch.chdir(tmp_path)
    async def run():
        provider=ProviderConfig('fixture','openai-compat','https://fixture.invalid','deepseek-chat','synthetic')
        app=CodePlusApp([provider],enable_fork=False)
        try:
            async with app.run_test(size=(100,35)) as pilot:
                loop=asyncio.get_running_loop()
                first=PermissionRequest('knowledge_search','first',loop.create_future())
                await app._handle_permission_request(first);await pilot.pause()
                first.future.cancel();await pilot.pause()
                assert app._pending_perm_request is None
                assert not app.query(InlinePermissionWidget)
                second=PermissionRequest('knowledge_open','second',loop.create_future())
                await app._handle_permission_request(second);await pilot.pause()
                old=InlinePermissionWidget.Responded(PermissionResponse.ALLOW)
                old._request_future=first.future
                app.post_message(old);await pilot.pause()
                assert not second.future.done()
                widget=app.query_one(InlinePermissionWidget);widget.action_deny();await pilot.pause()
                assert second.future.result()==PermissionResponse.DENY
                assert not app.query_one(ChatInput).disabled
                release=asyncio.Event();task=asyncio.create_task(release.wait())
                app._knowledge_active=True;app._streaming=False;app._agent_task=task
                await app._dispatch_command('ordinary message while old readers drain')
                await app._process_mailbox_notifications()
                assert app._agent_task is task and app._knowledge_active
                release.set();await task;app._knowledge_active=False;app._agent_task=None
        finally:
            if app.session:app.session.close()
            if app.client:await app.client._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('completion',[False,True])
def test_both_actual_loops_compact_without_host_environment_or_recovery(tmp_path,completion):
    from codeplus.conversation import Message
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,_=setup_scope(tmp_path,parent);seen=[]
        def transport(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=sse_text(
                '<summary>short retained research</summary>' if len(seen)==1 else 'UNVALIDATED'))
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy())
        agent.context_window=34000;agent.instructions_content='HOST_PRIVATE_INSTRUCTIONS'
        old_recovery=agent.recovery_state;old_recovery.record_file_read('host-private.txt','HOST_PRIVATE_RECOVERY')
        conv=ConversationManager(history=[Message('user','research history '*800)]+[Message('user','recent '+str(i)) for i in range(5)])
        try:
            if completion:await agent.run_to_completion('',conv)
            else:_=[event async for event in agent.run(conv)]
            assert len(seen)==2
            text=json.dumps(seen,ensure_ascii=False)
            assert 'HOST_PRIVATE_' not in text and str(tmp_path).replace('\\','\\\\') not in text
            assert agent.instructions_content=='HOST_PRIVATE_INSTRUCTIONS' and agent.recovery_state is old_recovery
            with scope.catalog._db.transaction() as db:
                assert list(db.execute('SELECT purpose FROM model_requests WHERE run_id=? ORDER BY rowid',(scope.run_id,)))==[('compact',),('agent',)]
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('protocol,terminal,reason,receipt',[
    ('openai-compat','length',None,'confirmed'),
    ('openai-compat','content_filter',None,'rejected'),
    ('openai-compat','invented',None,'unknown'),
    ('anthropic','max_tokens',None,'confirmed'),
    ('anthropic','refusal',None,'rejected'),
    ('anthropic','invented',None,'unknown'),
    ('openai','incomplete','max_output_tokens','confirmed'),
    ('openai','incomplete','content_filter','rejected'),
    ('openai','incomplete',None,'unknown'),
    ('openai','failed','server_error','rejected'),
    ('openai','completed','missing_model','unknown'),
    ('openai','completed','changed_model','unknown'),
    ('openai-compat','tail_error',None,'unknown'),
])
@pytest.mark.parametrize('completion',[False,True])
def test_sdk_terminal_transport_and_evidence_are_independent(tmp_path,protocol,terminal,reason,receipt,completion):
    from codeplus.conversation import Message,ToolResultBlock,ToolUseBlock
    async def run():
        parent=create_client(ProviderConfig('fixture',protocol,'https://fixture.invalid','fixture','synthetic'))
        scope,_=setup_scope(tmp_path,parent)
        result,params=await source_tool(scope,'terminal-source')
        conv=ConversationManager(history=[Message('assistant','',tool_uses=[ToolUseBlock('terminal-source','knowledge_open',params)]),
            Message('user','',tool_results=[ToolResultBlock('terminal-source',result.output,source_spans=result.source_spans)])])
        if protocol=='openai-compat':wire=sse_text('PRIVATE TERMINAL DRAFT',terminal='stop' if terminal=='tail_error' else terminal)
        else:
            if protocol=='anthropic':
                events=[{'type':'message_start','message':{'id':'m','type':'message','role':'assistant','content':[],
                    'model':'fixture-response','usage':{'input_tokens':10,'output_tokens':0,'cache_read_input_tokens':0,'cache_creation_input_tokens':0}}},
                    {'type':'message_delta','delta':{'stop_reason':terminal},'usage':{'output_tokens':1}},{'type':'message_stop'}]
            else:
                response={'id':'r','object':'response','created_at':1,'model':'fixture-response','status':terminal,'output':[],
                    'usage':{'input_tokens':10,'output_tokens':1,'total_tokens':11},
                    'incomplete_details':{'reason':reason} if terminal=='incomplete' and reason else None,
                    'error':{'code':reason,'message':'not copied into reports'} if terminal=='failed' else None}
                if terminal=='failed' or reason=='missing_model':response['model']=None
                if reason=='changed_model':response['model']='different-model'
                events=[{'type':'response.'+terminal,'sequence_number':0,'response':response}]
            wire=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
        requests=[]
        def transport(request):
            requests.append(request)
            if terminal=='tail_error':
                class Broken(httpx.AsyncByteStream):
                    async def __aiter__(self):
                        yield wire.removesuffix(b'data: [DONE]\n\n')
                        raise httpx.ReadError('controlled disconnected tail')
                    async def aclose(self):pass
                return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Broken())
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire)
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),protocol,work_dir=str(tmp_path),execution_policy=Policy());output=[]
        async def invoke():
            if completion:return await agent.run_to_completion('',conv,event_callback=output.append)
            from contextlib import aclosing
            async with aclosing(agent.run(conv)) as stream:output.extend([e async for e in stream])
        try:
            if receipt=='rejected':
                with pytest.raises(RuntimeError,match='answer_provider_failed'):await invoke()
            elif terminal=='tail_error':
                with pytest.raises(httpx.ReadError):await invoke()
            else:await invoke()
            assert len(requests)==1 and 'PRIVATE TERMINAL DRAFT' not in str(output)
            assert agent.last_run_outcome.artifact is None
            run=scope.catalog.get_run(scope.lease.run.run_id)
            failed=receipt=='rejected' or reason in {'missing_model','changed_model'} or terminal=='tail_error'
            assert (run.status.value,run.stop_reason)==(('failed','explicit_error') if failed else ('incomplete','provider_truncated'))
            with scope.catalog._db.transaction() as db:
                saved=db.execute('SELECT status FROM delivery_receipts WHERE run_id=?',(scope.run_id,)).fetchone()[0]
                assert saved==receipt
                count=db.execute('SELECT COUNT(*) FROM delivered_evidence WHERE run_id=?',(scope.run_id,)).fetchone()[0]
                assert (count>0)==(receipt=='confirmed')
                state,raw,outcome=db.execute('SELECT state,raw_usage,outcome FROM model_requests WHERE run_id=?',(scope.run_id,)).fetchone()
                assert json.loads(raw) and json.loads(outcome)['terminal']==('stop' if terminal=='tail_error' else terminal)
                assert state==('unknown' if terminal in {'invented','tail_error'} else 'confirmed')
                violation=db.execute('SELECT violation FROM model_requests WHERE run_id=?',(scope.run_id,)).fetchone()[0]
                if reason in {'missing_model','changed_model'}:
                    assert violation=='provider_model_identity_'+('missing' if reason=='missing_model' else 'changed')
                else:assert violation is None
                assert db.execute('SELECT COUNT(*) FROM saved_citations WHERE run_id=?',(scope.run_id,)).fetchone()==(0,)
            assert scope.catalog.get_pin(scope.lease.run.run_id).state=='released'
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('completion',[False,True])
@pytest.mark.parametrize('can_fit',[False,True])
def test_finish_crops_individual_tool_pairs_and_logs_actual_wire_sources(tmp_path,completion,can_fit):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,canonical=setup_scope(tmp_path,parent,raw=('source exact text '*100+'\n').encode())
        scope.finish_input_upper=6500 if can_fit else 2700
        handle=scope.sources.issue_source(scope._fixture_ref)
        seen=[]
        def transport(request):
            body=json.loads(request.content);seen.append(body)
            if len(seen)==1:
                calls=[('old'+str(i),'knowledge_search',{'query':str(i)}) for i in range(3)]
                calls.append(('latest-open','knowledge_open',{'source_ref':handle}))
                content=sse_text(calls=calls,terminal='tool_calls')
            elif len(seen)==2:
                assert len([m for m in body['messages'] if m['role']=='tool'])==4
                content=sse_text('{}')
            else:
                tools=[m for m in body['messages'] if m['role']=='tool']
                assert 1<=len(tools)<4 and any(m['tool_call_id']=='latest-open' for m in tools)
                assert not body.get('tools')
                source=next(m['content'] for m in tools if m['tool_call_id']=='latest-open')
                item=json.loads(source.split('\n\n<source ',1)[0])['items'][0]
                content=sse_text(json.dumps({'markdown':'kept open [^'+item['evidence_id']+']',
                    'citations':[{'evidence_id':item['evidence_id'],'spans':item['returned_spans'],'quotes':[canonical]}]}))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy());events=[]
        try:
            if completion:answer=await agent.run_to_completion('read sources',event_callback=events.append)
            else:
                from contextlib import aclosing
                conv=ConversationManager();conv.add_user_message('read sources')
                async with aclosing(agent.run(conv)) as stream:events.extend([e async for e in stream])
                answer=''.join(e.text for e in events if isinstance(e,(StreamText,ThinkingText)))
            assert len(seen)==(3 if can_fit else 2)
            transform=scope.window_transforms[-1]
            if can_fit:
                assert agent.last_run_outcome.status=='completed' and 'kept open' in answer
                with scope.catalog._db.transaction() as db:
                    payload=json.loads(db.execute("SELECT payload FROM delivery_receipts WHERE run_id=? AND json_extract(payload,'$.purpose')='citation_repair'",(scope.run_id,)).fetchone()[0])
                assert transform['after_candidates']==[v['candidate_id'] for v in payload['mappings']]
                assert transform['after_candidates'] and len(transform['after_candidates'])<len(transform['before_candidates'])
            else:
                assert agent.last_run_outcome.artifact is None and transform['rejected']=='no_source_pair_fits'
                assert transform['after_candidates']==[]
                with scope.catalog._db.transaction() as db:
                    assert db.execute("SELECT COUNT(*) FROM model_requests WHERE run_id=? AND purpose='citation_repair'",(scope.run_id,)).fetchone()==(0,)
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('completion',[False,True])
@pytest.mark.parametrize('phase',['validate','render_markdown'])
def test_both_entries_recheck_deadline_after_synchronous_citation_work(tmp_path,completion,phase):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,canonical=setup_scope(tmp_path,parent);requests=[]
        original=getattr(scope.citations,phase)
        def slow(*args):
            result=original(*args)
            scope.deadline=scope.owner.deadline=time.monotonic()+.005
            time.sleep(.02)
            return result
        setattr(scope.citations,phase,slow)
        def transport(request):
            body=json.loads(request.content);requests.append(body)
            if len(requests)==1:content=sse_text(calls=('s','knowledge_search',{'query':'source'}),terminal='tool_calls')
            else:
                source=next(m['content'] for m in body['messages'] if m['role']=='tool')
                item=json.loads(source.split('\n\n<source ',1)[0])['items'][0]
                content=sse_text(json.dumps({'markdown':'LATE BODY [^'+item['evidence_id']+']',
                    'citations':[{'evidence_id':item['evidence_id'],'spans':item['returned_spans'],'quotes':[canonical]}]}))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy());events=[]
        try:
            if completion:answer=await agent.run_to_completion('question',event_callback=events.append)
            else:
                from contextlib import aclosing
                conv=ConversationManager();conv.add_user_message('question')
                async with aclosing(agent.run(conv)) as stream:events.extend([e async for e in stream])
                answer=''.join(e.text for e in events if isinstance(e,(StreamText,ThinkingText)))
            assert 'LATE BODY' not in answer and 'LATE BODY' not in str(events)
            assert len(requests)==2 and agent.last_run_outcome.artifact is None
            actual=scope.catalog.get_run(scope.lease.run.run_id)
            assert actual.status.value=='incomplete' and actual.stop_reason=='time_budget'
            with scope.catalog._db.transaction() as db:
                assert db.execute('SELECT artifact FROM host_runs WHERE run_id=?',(scope.run_id,)).fetchone()==(None,)
                if phase=='validate':assert db.execute('SELECT COUNT(*) FROM saved_citations WHERE run_id=?',(scope.run_id,)).fetchone()==(0,)
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())
