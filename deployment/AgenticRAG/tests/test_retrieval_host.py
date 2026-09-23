"""Normal policy initialization and both installed Agent loops, controlled IO."""
import asyncio
from contextlib import aclosing
import json
from pathlib import Path
import runpy
import threading

import httpx
import pytest

from codeplus.agent import Agent
from codeplus.client import create_client, scoped_client
from codeplus.config import ProviderConfig
from codeplus.conversation import ConversationManager
from codeplus.tools import ToolRegistry
from agentic_rag.config import KnowledgeConfig, WorkerExecutionConfig
from agentic_rag.adapters.codeplus import policy as implementation

R=runpy.run_path(str(Path(__file__).with_name('test_retrieval.py')))
HOST=runpy.run_path(str(Path(__file__).with_name('test_codeplus_integration.py')))


@pytest.mark.parametrize('route',['bm25','hybrid'])
@pytest.mark.parametrize('completion',[False,True])
def test_normal_policy_fixed_routes_keep_query_only_tools_delivery_and_citations(tmp_path,monkeypatch,route,completion):
    async def run():
        _,kb,base,rows,_=R['published'](tmp_path)
        data=base.model_dump(mode='json');data['retrieval'].update(route=route,rerank=False,context_tokens=50000)
        data['budgets']['qa'].update(total_tokens=200000,finish_reserve_tokens=20000,duration_ms=600000)
        config=KnowledgeConfig.model_validate_json(json.dumps(data))
        settings=implementation.DevelopmentConfig(knowledge=config,
            worker=WorkerExecutionConfig(executable=str(tmp_path/'missing-python.exe'),model_cache=str(tmp_path/'missing-models'),runtime_dir=str(tmp_path/'worker')),
            answer_tokenizer=str(tmp_path/'unused-tokenizer'),explore_output_cap=1000,finish_input_upper=8000,
            finalize_output_cap=1000,repair_output_cap=1000,compact_output_cap=1000,max_iterations=5,max_tool_attempts=10,cleanup_grace_ms=100)
        answer_provider=ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic')
        parent=create_client(answer_provider)
        policy=implementation.KnowledgePolicy(settings,kb,answer_provider)
        calls=[];requests=[]
        def backend(storage,catalog,**kwargs):
            value=R['H']['unit_backend'](catalog,config,rows)
            value.client.close=lambda:None
            value.search=R['transport'](rows,calls)
            return value
        class Model(R['Provider']):
            def __init__(self,*args):super().__init__();self.lock=threading.RLock();self.handles={}
            def close(self,**kwargs):pass
        if route=='hybrid':monkeypatch.setattr(implementation,'LocalModelClient',Model)
        monkeypatch.setattr(implementation,'MilvusRevisionIndex',backend)
        monkeypatch.setattr(implementation,'DeepSeekTextMeter',lambda *args,**kwargs:HOST['ControlledMeter']())
        def transport(request):
            body=json.loads(request.content);requests.append(body)
            if len(requests)==1:
                content=HOST['sse_text'](calls=('search','knowledge_search',{'query':'telescope certificate'}),terminal='tool_calls')
            else:
                tool_text=next(m['content'] for m in body['messages'] if m['role']=='tool' and '<source ' in m['content'])
                metadata=json.loads(tool_text.split('\n\n<source ',1)[0]);item=metadata['items'][0]
                text=tool_text.split('\n\n<source ',1)[1].split('>\n',1)[1].split('\n</source>',1)[0]
                assert metadata['route']=={'strategy':route,'rerank':False}
                assert 'rrf_k' not in tool_text and 'branch_ranks' not in tool_text
                content=HOST['sse_text'](json.dumps({'markdown':'Grounded answer [^'+item['evidence_id']+']',
                    'citations':[{'evidence_id':item['evidence_id'],'spans':item['returned_spans'],'quotes':[text]}]}))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        monkeypatch.setattr(implementation,'scoped_client',lambda client:scoped_client(client,transport=httpx.MockTransport(transport)))
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=policy)
        try:
            if completion:await agent.run_to_completion('Find the certificate')
            else:
                conversation=ConversationManager();conversation.add_user_message('Find the certificate')
                async with aclosing(agent.run(conversation)) as stream:
                    async for _ in stream:pass
            scope=policy.scope
            assert agent.last_run_outcome.status=='completed'
            assert set(scope.registry.get('knowledge_search').params_model.model_fields)=={'query'}
            assert scope.sources.dense.route==route
            assert [c['field'] for c in calls]==(['sparse'] if route=='bm25' else ['dense','sparse'])
            if route=='bm25':
                assert scope.provider.sock is None and not scope.provider.handles and scope.provider.metadata is None
            else:assert len(scope.provider.calls)==1
            with scope.catalog._db.transaction() as db:
                assert db.execute('SELECT count(*) FROM retrieval_traces').fetchone()==(1,)
                assert db.execute('SELECT count(*) FROM delivered_evidence').fetchone()[0]>=1
                assert db.execute('SELECT count(*) FROM saved_citations').fetchone()==(1,)
            assert scope.catalog.get_pin(scope.lease.run.run_id).state=='released'
        finally:
            if policy.scope:await policy.scope.aclose()
            await parent._client.close()
    asyncio.run(run())
