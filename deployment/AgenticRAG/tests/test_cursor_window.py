"""A cropped previous-page cursor must keep its original upper boundary."""
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
def test_previous_range_continuation_keeps_sibling_tool_pair(tmp_path,completion):
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        scope,canonical=H['setup_scope'](tmp_path,parent,
            raw=('# Heading\n'+'汉字 "quoted" \\\n'*500).encode(),context_tokens=8000)
        boundary=3000
        reference=scope.sources.issue_source(scope._fixture_ref,anchor_span=Span(start=boundary,end=boundary+1))
        seen=[];previous=[]
        def transport(request):
            body=json.loads(request.content);seen.append(body)
            assert len(request.content)<=8000
            if len(seen)==1:
                content=H['sse_text']('Source check. '*140,calls=[
                    ('sibling','knowledge_open',{'source_ref':'src_invalid'}),
                    ('anchor','knowledge_open',{'source_ref':reference})],terminal='tool_calls')
            else:
                assert any(message.get('tool_call_id')=='sibling' for message in body['messages'])
                assert any(call['id']=='sibling' for message in body['messages'] for call in message.get('tool_calls',()))
                source=next(message for message in body['messages']
                    if message['role']=='tool' and '<source ' in message['content'])
                metadata=json.JSONDecoder().raw_decode(source['content'])[0]
                with scope.catalog._db.transaction() as db:
                    receipt=json.loads(db.execute("SELECT payload FROM delivery_receipts WHERE status='prepared' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
                mapping=next(item for item in receipt['mappings'] if item['tool_call_id']==source['tool_call_id'])
                span=mapping['source_span'];position=mapping['body_span'];item=metadata['items'][0]
                text=source['content'][position['start']:position['end']]
                assert text==canonical[span['start']:span['end']]
                assert item['returned_spans']==metadata['returned_spans']==[span]
                if len(seen)==2:
                    assert span['start']==boundary
                    cursor=metadata['previous_cursor']
                    assert scope.sources._resolve(cursor,'cursor')['range_end']==boundary
                    content=H['sse_text'](calls=('previous','knowledge_open',{'source_ref':reference,'cursor':cursor}),terminal='tool_calls')
                elif len(seen)==3:
                    assert source['tool_call_id']=='previous' and span['start']==0
                    assert metadata['host_cropped'] and span['end']<boundary
                    cursor=metadata['next_cursor'];state=scope.sources._resolve(cursor,'cursor')
                    assert state['next_cp']==span['end'] and state['range_end']==boundary
                    previous.append(span)
                    content=H['sse_text'](calls=('tail','knowledge_open',{'source_ref':reference,'cursor':cursor}),terminal='tool_calls')
                else:
                    assert source['tool_call_id']=='tail'
                    assert span['start']==previous[0]['end'] and span['end']<=boundary
                    if metadata['next_cursor']:
                        assert scope.sources._resolve(metadata['next_cursor'],'cursor')['range_end']==boundary
                    content=H['sse_text'](json.dumps({'markdown':'Verified prefix [^'+item['evidence_id']+']',
                        'citations':[{'evidence_id':item['evidence_id'],'spans':[span],'quotes':[text]}]}))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=content)
        await scope.client.aclose();scope.client=scoped_client(parent,transport=httpx.MockTransport(transport))
        class Policy:
            async def start(self,context):return scope
        agent=Agent(parent,ToolRegistry(),'openai-compat',work_dir=str(tmp_path),execution_policy=Policy())
        try:
            if completion:await agent.run_to_completion('Read the earlier source page')
            else:
                conversation=ConversationManager();conversation.add_user_message('Read the earlier source page')
                async with aclosing(agent.run(conversation)) as stream:
                    async for _ in stream:pass
            assert len(seen)==4 and agent.last_run_outcome.status=='completed'
            moved=[event['removed_tool_call_id'] for event in scope.window_transforms if event['purpose']=='open_cursor']
            assert moved==['anchor','previous']
        finally:
            await scope.aclose();await parent._client.close()
    asyncio.run(run())
