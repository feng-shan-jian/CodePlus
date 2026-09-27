"""Mode selection and the ordinary host entry points."""
import asyncio
from contextlib import aclosing
import json
from pathlib import Path
import runpy
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from codeplus.agent import Agent
from codeplus.client import create_client, scoped_client
from codeplus.config import AppConfig, ProviderConfig
from codeplus.conversation import ConversationManager
from codeplus.run_policy import HostRunContext
from codeplus.tools import ToolRegistry
from agentic_rag.adapters.codeplus import policy as P
from agentic_rag.config import KnowledgeConfig, WorkerExecutionConfig, resolve_run
from agentic_rag.domain import RagError, ErrorCode
from agentic_rag.retrieval import RetrievalSearch
from agentic_rag.sources import SourceSession

R = runpy.run_path(str(Path(__file__).with_name('test_retrieval.py')))
RR = runpy.run_path(str(Path(__file__).with_name('test_rerank.py')))
H = runpy.run_path(str(Path(__file__).with_name('test_codeplus_integration.py')))


def settings(base, root, mode='auto'):
    data = RR['configured'](base, 'hybrid', rerank=False).model_dump(mode='json')
    if mode is None: data['retrieval'].pop('mode')
    else: data['retrieval']['mode'] = mode
    data['retrieval']['context_tokens'] = 50000
    data['budgets']['qa'].update(total_tokens=200000, finish_reserve_tokens=20000, duration_ms=600000)
    data['budgets']['report'].update(searches=25, opens=35, total_tokens=400000,
                                   finish_reserve_tokens=30000, duration_ms=1200000)
    return P.DevelopmentConfig(knowledge=KnowledgeConfig.model_validate_json(json.dumps(data)),
        worker=WorkerExecutionConfig(executable=str(root/'python'), model_cache=str(RR['CACHE']), runtime_dir=str(root/'worker')),
        answer_tokenizer=str(root/'tokenizer'), explore_output_cap=1000, finish_input_upper=8000,
        finalize_output_cap=1000, repair_output_cap=1000, compact_output_cap=1000,
        max_iterations=10, max_tool_attempts=20, cleanup_grace_ms=100)


class Model(RR['Provider']):
    def __init__(self, *args):
        super().__init__(); self.lock=threading.RLock(); self.handles={}
    def close(self, **kwargs): pass


def host_dependencies(monkeypatch, config, rows, calls):
    def backend(storage, catalog, **kwargs):
        value=R['H']['unit_backend'](catalog, config.knowledge, rows)
        value.client.close=lambda:None
        value.search=R['transport'](rows,calls)
        return value
    monkeypatch.setattr(P, 'MilvusRevisionIndex', backend)
    monkeypatch.setattr(P, 'LocalModelClient', Model)


@pytest.mark.parametrize('configured,override,effective,origin',[
    (None,None,'auto','defaults'), ('fixed',None,'fixed','configured'),
    ('fixed','auto','auto','explicit'), ('fixed','fixed','fixed','explicit'),
    ('auto','fixed','fixed','explicit')])
@pytest.mark.parametrize('kind',['qa','report'])
def test_policy_freezes_mode_origin_and_task_kind(tmp_path,monkeypatch,configured,override,effective,origin,kind):
    async def run():
        catalog,kb,base,rows,_=R['published'](tmp_path)
        config=settings(base,tmp_path,configured); host_dependencies(monkeypatch,config,rows,[])
        provider=ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic')
        parent=create_client(provider)
        policy=P.KnowledgePolicy(config,kb,provider,task_kind=kind,mode=override)
        context=HostRunContext('test','session',str(tmp_path),'openai-compat',parent,None,None)
        scope=await policy.start(context)
        try:
            bound=scope.lease.run.resolved_config
            assert bound.retrieval.mode==effective and bound.task_kind==kind
            with catalog._db.transaction() as db:
                frozen=json.loads(db.execute('SELECT frozen FROM host_runs WHERE run_id=?',(scope.run_id,)).fetchone()[0])
                started,=db.execute('SELECT started_ns FROM source_usage WHERE run_id=?',(scope.run_id,)).fetchone()
            assert frozen['mode']=={'value':effective,'source':origin}
            assert frozen['task_kind']==kind
            replacement=settings(base,tmp_path,'fixed' if effective=='auto' else 'auto')
            policy.config=replacement
            assert catalog.get_run(scope.lease.run.run_id).resolved_config==bound
            fields=set(scope.tools[0].params_model.model_fields)
            assert fields==({'query','strategy','rerank'} if effective=='auto' else {'query'})
        finally:
            await scope.aclose(); await parent._client.close()
    asyncio.run(run())


def test_mode_and_auto_schema_reject_unauthorized_parameters(tmp_path):
    _,kb,base,_,_=R['published'](tmp_path); config=settings(base,tmp_path)
    for mode in ('AUTO','other',1):
        with pytest.raises(ValidationError):P.KnowledgePolicy(config,kb,None,mode=mode)
    with pytest.raises(ValueError):P.KnowledgePolicy(config,kb,None,task_kind='other')
    for extra in ({'top_k':1},{'model':'other'},{'kb_id':str(kb)},{'budget':1},{'strategy':'other'},{'rerank':'false'}):
        with pytest.raises(ValidationError):P.AutoSearchArguments.model_validate({'query':'q',**extra})
    for extra in ({'strategy':'dense'},{'rerank':False}):
        with pytest.raises(ValidationError):P.SearchArguments.model_validate({'query':'q',**extra})


def test_auto_call_local_six_selections_and_fixed_core_authority(tmp_path):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    config=settings(base,tmp_path).knowledge; model=Model(); calls=[]
    backend.search=R['transport'](rows,calls)
    with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
        service=RetrievalSearch(catalog,lease.run.run_id,model,backend)
        session=SourceSession(catalog,lease,R['S']['ControlledMeter'](),dense=service)
        previous=None
        for route in ('dense','bm25','hybrid'):
            for rerank in (False,True):
                R['S']['discard_window'](session)
                result=session.search('certificate',strategy=route,rerank=rerank)
                trace=session.retrieval_trace(result.payload['call_id'])
                assert result.payload['route']==trace['selection']['effective']=={'strategy':route,'rerank':rerank}
                assert trace['route']==route and trace['rerank']['enabled']==rerank
                assert trace['preceding_call_id']==previous
                previous=result.payload['call_id']
                assert service.route=='hybrid' and service.run.resolved_config.retrieval==config.retrieval
        R['S']['discard_window'](session)
        default=session.search('base selection')
        assert default.payload['route']=={'strategy':'hybrid','rerank':False}
        assert session.retrieval_trace(default.payload['call_id'])['selection']['requested']=={'strategy':None,'rerank':None}
        assert session.usage()['searches']==7
        with catalog._db.transaction() as db:assert db.execute('SELECT count(*) FROM delivered_evidence').fetchone()==(0,)
    fixed=settings(base,tmp_path,'fixed').knowledge
    with catalog.start_run(kb,resolve_run(fixed,'qa')) as lease:
        service=RetrievalSearch(catalog,lease.run.run_id,model,backend)
        session=SourceSession(catalog,lease,R['S']['ControlledMeter'](),dense=service)
        for choices in ({'strategy':'hybrid'},{'rerank':False}):
            with pytest.raises(RagError) as caught:session.search('q',**choices)
            assert caught.value.error.code==ErrorCode.INVALID_INPUT
            assert session.retrieval_trace(caught.value.error.call_id)['status']=='error'
            with pytest.raises(RagError):service.search('q',**choices)
        assert session.usage()['searches']==2


@pytest.mark.parametrize('mode',['fixed','auto'])
@pytest.mark.parametrize('completion',[False,True])
def test_both_loops_explicit_retry_and_auto_reselection_preserve_failure(tmp_path,monkeypatch,mode,completion):
    async def run():
        _,kb,base,rows,_=R['published'](tmp_path)
        config=settings(base,tmp_path,mode)
        if mode=='fixed':
            data=config.model_dump(mode='json');data['knowledge']['retrieval'].update(route='bm25',rerank=True)
            config=P.DevelopmentConfig.model_validate_json(json.dumps(data))
        calls=[]; host_dependencies(monkeypatch,config,rows,calls)
        provider=ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic')
        parent=create_client(provider);policy=P.KnowledgePolicy(config,kb,provider)
        observed=[];failure=[]
        def transport(request):
            body=json.loads(request.content);observed.append(body);scope=policy.scope
            if len(observed)==1:
                scope.provider.defect='failure'
                args={'query':'first certificate'}
                if mode=='auto':args.update(strategy='bm25',rerank=True)
                content=H['sse_text'](calls=('first','knowledge_search',args),terminal='tool_calls')
            elif len(observed)==2:
                error=json.loads(next(m['content'] for m in body['messages'] if m['role']=='tool'))
                assert error['status']=='error' and error['code']=='WORKER_UNAVAILABLE'
                assert error['call_id'] and error['stage']=='test_worker' and 'retry_same_selection' in error['allowed_actions']
                failure.append(error['call_id']);scope.provider.defect=None
                args={'query':'reformulated certificate'}
                if mode=='auto':args.update(strategy='dense',rerank=False)
                content=H['sse_text'](calls=('retry','knowledge_search',args),terminal='tool_calls')
            else:
                source=next(m['content'] for m in body['messages'] if m['role']=='tool' and '<source ' in m['content'])
                metadata=json.JSONDecoder().raw_decode(source)[0];item=metadata['items'][0]
                text=source.split('>\n',1)[1].split('\n</source>',1)[0]
                content=H['sse_text']('Supported [^'+item['evidence_id']+']')
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        client=scoped_client(parent,transport=httpx.MockTransport(transport))
        agent=Agent(client,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=policy)
        try:
            if completion:await agent.run_to_completion('Find the certificate')
            else:
                conversation=ConversationManager();conversation.add_user_message('Find the certificate')
                async with aclosing(agent.run(conversation)) as stream:
                    async for _ in stream:pass
            assert len(observed)==3 and agent.last_run_outcome.status=='completed'
            scope=policy.scope
            with scope.catalog._db.transaction() as db:
                traces=[json.loads(r[0]) for r in db.execute('SELECT payload FROM retrieval_traces ORDER BY rowid')]
                assert db.execute('SELECT count(*) FROM source_candidates WHERE call_id=?',(failure[0],)).fetchone()==(0,)
            assert [t['status'] for t in traces]==['error','ok']
            assert traces[1]['preceding_call_id']==failure[0]
            assert traces[1]['route']==('bm25' if mode=='fixed' else 'dense')
            assert traces[1]['rerank']['enabled']==(mode=='fixed')
            assert scope.sources.usage()['searches']==2
            assert all(t['revision_id']==traces[0]['revision_id'] for t in traces)
        finally:
            if policy.scope:await policy.scope.aclose()
            await client.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('args,mode,text', [('ask old question',None,'old question'),
    ('ask --mode auto question here','auto','question here'),('ask --mode fixed same','fixed','same')])
def test_tui_mode_selection_preserves_old_call(args,mode,text):
    from codeplus.commands.handlers.knowledge import handle_knowledge
    calls=[]
    ui=SimpleNamespace(knowledge_feature_available=True,knowledge_library='selected',
        send_knowledge_message=lambda *a,**kw:calls.append((a,kw)),add_system_message=lambda _:None)
    asyncio.run(handle_knowledge(SimpleNamespace(args=args,ui=ui)))
    assert calls==[((text,),{} if mode is None else {'mode':mode})]
