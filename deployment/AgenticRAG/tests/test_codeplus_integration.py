"""Real host loops, SQLite/archive and SDK; synthetic model/index transport.

Network/GPU/Milvus acceptance is separate from these controlled transports.
"""
import asyncio
import json
from pathlib import Path
import runpy
import time

import httpx
import pytest

from codeplus.agent import Agent, StreamText
from codeplus.client import create_client, scoped_client
from codeplus.config import ProviderConfig
from codeplus.conversation import ConversationManager
from codeplus.run_policy import HostRunContext
from codeplus.tools import ToolRegistry

from agentic_rag.config import KnowledgeConfig, WorkerExecutionConfig, resolve_run
from agentic_rag.domain import ErrorCode, RagError, RunStatus
from agentic_rag.adapters.codeplus.policy import DevelopmentConfig, KnowledgeScope, SourceTextMeter
from codeplus.tools.base import ToolCallComplete

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
    tool=next(tool for tool in scope.tools if tool.name=='knowledge_'+kind)
    params={'source_ref':scope.sources.issue_source(scope._fixture_ref)} if kind=='open' else {'query':'source'}
    result=await tool.execute(tool.params_model.model_validate(params))
    scope.tool_finished(ToolCallComplete(call_id,tool.name,params),result)
    assert not result.is_error
    return result,params


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


def setup_scope(tmp_path, parent, raw=None, *, context_tokens=50000, protocol='openai-compat', opens=100):
    kwargs = {'raw':raw} if raw is not None else {}
    catalog, old, source_session, source, version, chunks, ref = H['fixture'](tmp_path, context_tokens=context_tokens, opens=opens, **kwargs)
    base = old.run.resolved_config.knowledge.model_dump(mode='json')
    old.close()
    conf = KnowledgeConfig.model_validate_json(json.dumps(base))
    lease = catalog.start_run(ref.kb_id, resolve_run(conf, 'qa'))
    settings = DevelopmentConfig(knowledge=conf, worker=WorkerExecutionConfig(executable=str(tmp_path/'python'),
        model_cache=str(tmp_path/'models'), runtime_dir=str(tmp_path/'runtime')), cleanup_grace_ms=100)
    context = HostRunContext('test','session',str(tmp_path),protocol,parent,None,None)
    scope = KnowledgeScope(settings,catalog,lease,SourceTextMeter(),context,time.monotonic())
    H['dense_fixture'](scope.sources,chunks,version,ref)
    scope._fixture_ref=ref
    return scope, chunks.parsed.text


class BoundPolicy:
    def __init__(self, scope):
        self.scope = scope

    async def start(self, context):
        return self.scope


@pytest.mark.parametrize('completion', [False, True])
def test_knowledge_tools_coexist_restore_and_return_plain_text(tmp_path, completion):
    from codeplus.tools import create_default_registry
    async def run():
        parent = create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope, _ = setup_scope(tmp_path, parent)
        observed = []
        destination = tmp_path/'normal-tool-output.txt'
        def transport(request):
            body = json.loads(request.content)
            observed.append(body)
            names = {tool['function']['name'] for tool in body['tools']}
            assert {'knowledge_search','knowledge_open','WriteFile','ReadFile'} <= names
            assert 'project instructions remain' in str(body['messages'])
            if len(observed) <= 3:
                content = sse_text(calls=(str(len(observed)), 'knowledge_search', {'query':'source'}),terminal='tool_calls')
            elif len(observed) == 4:
                content = sse_text(calls=('write','WriteFile',{'file_path':str(destination),'content':'ordinary file output'}),terminal='tool_calls')
            else:
                content = sse_text('Plain answer with ordinary tools.')
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        client = scoped_client(parent,transport=httpx.MockTransport(transport))
        registry = create_default_registry()
        old = scope.tools[0]
        registry.register(old)
        registry.disable(old.name)
        registry.mark_discovered(old.name)
        agent = Agent(client,registry,'openai-compat',work_dir=str(tmp_path),execution_policy=BoundPolicy(scope),
                      instructions_content='project instructions remain')
        try:
            if completion:
                text = await agent.run_to_completion('Find the source and save a file.')
            else:
                conv = ConversationManager();conv.add_user_message('Find the source and save a file.')
                events = [event async for event in agent.run(conv)]
                text = ''.join(event.text for event in events if isinstance(event,StreamText))
            assert text == 'Plain answer with ordinary tools.'
            assert destination.read_text() == 'ordinary file output'
            assert scope.sources.usage()['searches'] == 3
            assert registry.get(old.name) is old and not registry.is_enabled(old.name)
            assert registry.is_discovered(old.name) and registry.get('knowledge_open') is None
            assert agent.client is client and agent.registry is registry
            assert agent.last_run_outcome.status == 'completed'
            assert scope.catalog.get_pin(scope.lease.run.run_id).state == 'released'
        finally:
            await scope.aclose();await client.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('completion', [False, True])
def test_cancel_waits_for_real_source_reader_before_releasing_pin(tmp_path, completion):
    import threading
    async def run():
        parent = create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope, _ = setup_scope(tmp_path, parent)
        reached, release = threading.Event(), threading.Event()
        search = scope.sources.search
        def blocked(*args, **kwargs):
            reached.set();release.wait(5)
            return search(*args, **kwargs)
        scope.sources.search = blocked
        client = scoped_client(parent,transport=httpx.MockTransport(lambda request:httpx.Response(200,
            headers={'content-type':'text/event-stream'},content=sse_text(calls=('call','knowledge_search',{'query':'source'}),terminal='tool_calls'))))
        registry = ToolRegistry()
        agent = Agent(client,registry,'openai-compat',work_dir=str(tmp_path),execution_policy=BoundPolicy(scope))
        async def consume():
            if completion:
                return await agent.run_to_completion('question')
            conv=ConversationManager();conv.add_user_message('question')
            return [event async for event in agent.run(conv)]
        task=asyncio.create_task(consume())
        try:
            assert await asyncio.to_thread(reached.wait,5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert registry.get('knowledge_search') is None
            assert agent.last_run_outcome.status == 'cancelled'
            assert scope.catalog.get_pin(scope.lease.run.run_id).state == 'active'
            release.set()
            for _ in range(100):
                if scope.catalog.get_pin(scope.lease.run.run_id).state == 'released':break
                await asyncio.sleep(.02)
            assert scope.catalog.get_pin(scope.lease.run.run_id).state == 'released'
        finally:
            release.set();await client.aclose();await parent._client.close()
    asyncio.run(run())


def test_library_switch_keeps_conversation_and_restores_tools_after_failure(tmp_path):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scopes=[setup_scope(tmp_path/str(index),parent)[0] for index in range(2)]
        calls=[]
        def transport(request):
            body=json.loads(request.content);calls.append(body)
            index=len(calls)
            if index>=4:
                return httpx.Response(500,json={'error':{'message':'failure'}})
            content=(sse_text(calls=('call'+str(index),'knowledge_search',{'query':'source'}),terminal='tool_calls')
                     if index in (1,3) else sse_text('first answer'))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        client=scoped_client(parent,transport=httpx.MockTransport(transport))
        registry=ToolRegistry();agent=Agent(client,registry,'openai-compat',work_dir=str(tmp_path))
        conv=ConversationManager()
        try:
            agent.execution_policy=BoundPolicy(scopes[0])
            assert await agent.run_to_completion('first library',conv)=='first answer'
            agent.execution_policy=BoundPolicy(scopes[1])
            with pytest.raises(Exception):await agent.run_to_completion('second library',conv)
            assert any(m.content=='first answer' for m in conv.history)
            assert agent.last_run_outcome.status=='failed'
            assert not registry.list_tools()
            assert all(scope.catalog.get_pin(scope.lease.run.run_id).state=='released' for scope in scopes)
            with scopes[1].catalog._db.transaction() as db:
                assert db.execute('SELECT count(*) FROM delivered_evidence').fetchone()==(0,)
        finally:
            for scope in scopes:await scope.aclose()
            await client.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('status', [503, 307])
def test_retry_or_redirect_confirms_only_successful_source_delivery(tmp_path, status):
    from codeplus.conversation import ToolResultBlock,ToolUseBlock
    from agentic_rag.evidence import read_evidence
    from uuid import UUID
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,_=setup_scope(tmp_path,parent)
        result,params=await source_tool(scope,'source')
        evidence_id=json.JSONDecoder().raw_decode(result.output)[0]['items'][0]['evidence_id']
        conv=ConversationManager();conv.add_user_message('question')
        conv.add_assistant_message('',[ToolUseBlock('source','knowledge_open',params)])
        conv.add_tool_results_message([ToolResultBlock('source',result.output,source_spans=result.source_spans)])
        calls=[]
        def transport(request):
            calls.append(request)
            if len(calls)==1:
                return httpx.Response(status,headers={'retry-after-ms':'1','location':'https://fixture.invalid/redirected'},
                    json={'error':{'message':'retry or redirect'}})
            with scope.catalog._db.transaction() as db:
                assert db.execute('SELECT count(*) FROM delivered_evidence').fetchone()==(0,)
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=sse_text('source answer'))
        client=scoped_client(parent,transport=httpx.MockTransport(transport))
        try:
            _=[event async for event in client.stream(conv,control=scope.model_control('agent'))]
            assert len(calls)==2
            if status==307:
                assert calls[-1].url.path=='/redirected'
                assert calls[-1].content==calls[0].content
            with scope.catalog._db.transaction() as db:
                receipts=list(db.execute('SELECT request_id,status FROM delivery_receipts ORDER BY rowid'))
            assert [state for _,state in receipts]==['rejected','confirmed']
            assert len({identity for identity,_ in receipts})==2
            _,_,stored=read_evidence(scope.catalog,scope.lease.run.run_id,UUID(evidence_id))
            assert stored['deliveries']==[receipts[-1][0]]
        finally:
            await scope.aclose();await client.aclose();await parent._client.close()
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
        client=scoped_client(parent,transport=httpx.MockTransport(transport))
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
            output=[e async for e in client.stream(conv,system='metered system',control=scope.model_control('agent'))]
            assert len(seen)==1
            with scope.catalog._db.transaction() as db:
                receipt=json.loads(db.execute("SELECT payload FROM delivery_receipts WHERE run_id=? AND status='confirmed'",(scope.run_id,)).fetchone()[0])
            assert len(receipt['mappings'])==1 and receipt['mappings'][0]['tool_call_id']=='actual'
            mapped=receipt['mappings'][0];body=json.loads(seen[0]);node=body
            for part in mapped['json_path']:node=node[part]
            source_span=Span.model_validate(mapped['source_span']);wire_span=mapped['body_span']
            quote=canonical[source_span.start:source_span.end]
            assert node[wire_span['start']:wire_span['end']]==quote
            record,_,_=read_evidence(scope.catalog,scope.lease.run.run_id,UUID(meta['evidence_id']))
            assert source_span in record.spans
            if transform!='blocks':
                assert source_span.end<len(canonical)

            else:
                assert mapped['json_path'][-2:]==[0,'text']
                assert source_span.end==len(canonical)
        finally:
            await scope.aclose();await client.aclose();await parent._client.close()
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
        client=scoped_client(parent,transport=httpx.MockTransport(lambda request:
            httpx.Response(200,headers={'content-type':'text/event-stream'},content=sse_text('<summary>compressed, no original source body</summary>'))))
        try:
            old,op=await source_tool(scope,'old');pending,pp=await source_tool(scope,'prefix');tail,tp=await source_tool(scope,'tail')
            # Confirm old evidence first; it survives later history compaction.
            conv=ConversationManager(history=[Message('user','question')]+pair('old',old,op))
            _=[e async for e in client.stream(conv,control=scope.model_control('agent'))]
            conv=ConversationManager(history=[Message('user','ordinary prefix '*750)]+pair('old',old,op)+pair('prefix',pending,pp)+
                [Message('user','recent1'),Message('assistant','recent2'),Message('user','recent3')]+pair('tail',tail,tp))
            result=await auto_compact(conv,client,1000000,tmp_path/'spill',protocol='openai-compat',manual=True,
                control_factory=scope.model_control)
            assert result is not None and not isinstance(result,str)
            def evidence_ids():
                with scope.catalog._db.transaction() as db:return {row[0] for row in db.execute('SELECT evidence_id FROM delivered_evidence WHERE run_id=?',(scope.run_id,))}
            eid=lambda r:json.loads(r.output.split('\n\n<source ',1)[0])['items'][0]['evidence_id']
            assert evidence_ids()=={eid(old)}
            _=[e async for e in client.stream(conv,control=scope.model_control('agent'))]
            assert evidence_ids()=={eid(old),eid(tail)}
            assert eid(pending) not in evidence_ids()
        finally:
            await scope.aclose();await client.aclose();await parent._client.close()
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
        client=scoped_client(parent,transport=httpx.MockTransport(transport))
        rules=tmp_path/'rules.yaml';rules.write_text('- rule: "knowledge_search(*)"\n  effect: ask\n')
        checker=PermissionChecker(DangerousCommandDetector(),PathSandbox(str(tmp_path)),RuleEngine(local_rules_path=rules))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(client,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy(),permission_checker=checker)
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
            await stream.aclose();await client.aclose();await parent._client.close()
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
