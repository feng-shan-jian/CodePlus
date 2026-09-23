"""R18 selection through both existing Agent loops and real SDK serialization."""
import asyncio
from contextlib import aclosing
import json
from pathlib import Path
import runpy

import httpx
import pytest

from codeplus.agent import Agent
from codeplus.client import create_client, scoped_client
from codeplus.config import ProviderConfig
from codeplus.conversation import ConversationManager
from codeplus.tools import ToolRegistry
from agentic_rag.domain import Span

H=runpy.run_path(str(Path(__file__).with_name('test_codeplus_integration.py')))


@pytest.mark.parametrize('completion',[False,True])
def test_both_agent_loops_retrieve_cropped_unseen_tail_and_cite_actual_wire(tmp_path,monkeypatch,completion):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,canonical=H['setup_scope'](tmp_path,parent,raw=('# Heading\n'+'registry telescope '*200).encode())
        seen=[];wire=[]
        def transport(request):
            body=json.loads(request.content);seen.append(body)
            if len(seen)==1:
                content=H['sse_text'](calls=('first','knowledge_search',{'query':'registry'}),terminal='tool_calls')
            else:
                with scope.catalog._db.transaction() as db:
                    receipt=json.loads(db.execute("SELECT payload FROM delivery_receipts WHERE status='prepared' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
                current=[m for m in receipt['mappings'] if m['tool_call_id']==('first' if len(seen)==2 else 'again')]
                assert current
                mapped=current[0];wire.append(mapped)
                span=Span.model_validate(mapped['source_span']);node=body
                for part in mapped['json_path']:node=node[part]
                quote=node[mapped['body_span']['start']:mapped['body_span']['end']]
                assert quote==canonical[span.start:span.end]
                if len(seen)==2:
                    assert span.end<len(canonical)
                    content=H['sse_text'](calls=('again','knowledge_search',{'query':'registry'}),terminal='tool_calls')
                else:
                    assert span.start==wire[0]['source_span']['end']
                    assert mapped['candidate_id']!=wire[0]['candidate_id']
                    content=H['sse_text'](json.dumps({'markdown':'Recovered original [^'+mapped['evidence_id']+']',
                        'citations':[{'evidence_id':mapped['evidence_id'],'spans':[span.model_dump()],'quotes':[quote]}]}))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            async def start(self,context):return scope
        monkeypatch.setattr('codeplus.agent.MAX_OUTPUT_CHARS',2500)
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy())
        agent.session_dir.mkdir(parents=True,exist_ok=True)
        try:
            if completion:await agent.run_to_completion('Read the registry')
            else:
                conv=ConversationManager();conv.add_user_message('Read the registry')
                async with aclosing(agent.run(conv)) as stream:
                    async for _ in stream:pass
            assert len(seen)==3 and agent.last_run_outcome.status=='completed'
            with scope.catalog._db.transaction() as db:
                assert db.execute('SELECT count(*) FROM saved_citations').fetchone()==(1,)
                traces=[json.loads(r[0]) for r in db.execute('SELECT payload FROM retrieval_traces ORDER BY rowid')]
            assert len(traces)==2
            assert traces[1]['context']['window_before']['mappings'][0]['source_span']==wire[0]['source_span']
            assert traces[1]['context']['decisions'][0]['eligible_spans'][0]['start']==wire[0]['source_span']['end']
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())
