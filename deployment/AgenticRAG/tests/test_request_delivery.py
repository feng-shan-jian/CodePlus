"""Actual SDK serialization/stream behavior over a controlled HTTP transport.

These are protocol boundary tests, not network-model acceptance evidence.
"""
import asyncio
import json
import time

import httpx
import pytest

from codeplus.client import create_client, scoped_client
from codeplus.config import ProviderConfig
from codeplus.conversation import ConversationManager, ToolResultBlock, ToolUseBlock
from codeplus.run_policy import BudgetStop, SourceSpan
from codeplus.tools.base import StreamEnd


class Control:
    output_cap = 17
    purpose = 'agent'

    def __init__(self, refuse=False):
        self.deadline = time.monotonic()+10
        self.refuse = refuse
        self.requests, self.outcomes = [], []

    async def before_send(self, request):
        self.requests.append(request)
        if self.refuse:
            raise BudgetStop('fixture_refusal', hard=True)
        return 'permit'

    async def settled(self, permit, result):
        self.outcomes.append((permit, result))


def test_final_http_hook_refuses_when_synchronous_admission_crosses_deadline():
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        sent=[]
        client=scoped_client(parent,transport=httpx.MockTransport(lambda request:sent.append(request)))
        class Slow(Control):
            async def before_send(self,request):
                permit=await super().before_send(request)
                self.deadline=time.monotonic()-.001
                return permit
        control=Slow()
        try:
            with pytest.raises(BudgetStop,match='time_budget'):
                _=[event async for event in client.stream(conversation(),control=control)]
            assert not sent
            assert control.outcomes[0][0]=='permit' and control.outcomes[0][1].delivery=='not_sent'
        finally:
            await client.aclose();await parent._client.close()
    asyncio.run(run())


def test_repeated_cancel_during_local_settlement_cannot_abandon_receipt():
    async def run():
        parent=create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        client=scoped_client(parent,transport=httpx.MockTransport(lambda request:httpx.Response(200,
            headers={'content-type':'text/event-stream'},content=sse(compat_events('stop'))+b'data: [DONE]\n\n')))
        entered=asyncio.Event();release=asyncio.Event()
        class Slow(Control):
            async def settled(self,permit,result):
                entered.set();await release.wait();await super().settled(permit,result)
        control=Slow()
        async def consume():return [event async for event in client.stream(conversation(),control=control)]
        task=asyncio.create_task(consume())
        try:
            await entered.wait();task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
            assert not task.done();release.set()
            with pytest.raises(asyncio.CancelledError):await task
            assert len(control.outcomes)==1 and control.outcomes[0][1].delivery=='confirmed'
        finally:
            await client.aclose();await parent._client.close()
    asyncio.run(run())


def sse(events, *, named=False):
    return ''.join((f"event: {item['type']}\n" if named else '')+
                   'data: '+json.dumps(item)+'\n\n' for item in events).encode()


def compat_events(reason='length', *, usage=True):
    result = [{'id': 'c', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'm',
        'choices': [{'index': 0, 'delta': {'content': 'internal draft'}, 'finish_reason': reason}]}]
    if usage:
        result[0]['usage'] = {'prompt_tokens': 29, 'completion_tokens': 17, 'total_tokens': 46}
    return result


def conversation():
    conv = ConversationManager()
    conv.add_user_message('question')
    conv.add_assistant_message('', [ToolUseBlock('call', 'knowledge_open', {'x': 'a'})])
    conv.add_tool_results_message([ToolResultBlock('call', 'prefix甲😀suffix', source_spans=(
        SourceSpan('candidate', 50, 52, 6, 8),))])
    return conv


@pytest.mark.parametrize('protocol', ['anthropic', 'openai', 'openai-compat'])
def test_sdk_gate_restores_refusal_without_transport_or_retry(protocol):
    async def run():
        calls = []
        async def transport(request):
            calls.append(request)
            return httpx.Response(500)
        parent = create_client(ProviderConfig(name='fixture', base_url='https://fixture.invalid', protocol=protocol, model='fixture', api_key='synthetic'))
        client = scoped_client(parent, transport=httpx.MockTransport(transport))
        control = Control(refuse=True)
        try:
            with pytest.raises(BudgetStop, match='fixture_refusal'):
                async for _ in client.stream(conversation(), system='system', control=control):
                    pass
            assert calls == []
            assert len(control.requests) == len(control.outcomes) == 1
            assert control.outcomes[0][1].delivery == 'not_sent'
            body = json.loads(control.requests[0].raw_body)
            assert body['max_output_tokens' if protocol == 'openai' else 'max_tokens'] == 17
            mapping = control.requests[0].mappings[0]
            node = body
            for part in mapping.path:
                node = node[part]
            assert node[mapping.source.body_start:mapping.source.body_end] == '甲😀'
            assert parent._client.max_retries == 2
        finally:
            await client.aclose()
            await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['disconnect', 'close', 'unknown_terminal'])
def test_terminal_chunk_does_not_confirm_interrupted_or_unknown_stream(mode):
    class Interrupted(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield sse(compat_events('invented' if mode == 'unknown_terminal' else 'stop'))
            if mode == 'disconnect':
                raise httpx.ReadError('synthetic disconnected tail')
            yield b'data: [DONE]\n\n'
        async def aclose(self): self.closed = True
    async def run():
        wire = Interrupted()
        parent = create_client(ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic'))
        client = scoped_client(parent,transport=httpx.MockTransport(lambda request: httpx.Response(
            200,headers={'content-type':'text/event-stream'},stream=wire)))
        control = Control()
        stream = client.stream(conversation(),control=control)
        try:
            if mode == 'close':
                await anext(stream)
                await stream.aclose()
            elif mode == 'disconnect':
                with pytest.raises(httpx.ReadError):
                    async for _ in stream: pass
            else:
                async for _ in stream: pass
            assert control.outcomes[0][1].delivery == 'unknown'
            assert wire.closed
        finally:
            await stream.aclose();await client.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('protocol,terminal', [('anthropic','max_tokens'),('openai','incomplete'),('openai','failed')])
def test_native_protocol_raw_terminal_usage_and_failure_details(protocol, terminal):
    if protocol == 'anthropic':
        events = [
            {'type':'message_start','message':{'id':'msg','type':'message','role':'assistant','content':[],
                'model':'fixture','stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':29,'output_tokens':0}}},
            {'type':'message_delta','delta':{'stop_reason':terminal,'stop_sequence':None},'usage':{'output_tokens':17}},
            {'type':'message_stop'}]
    else:
        response = {'id':'resp','object':'response','created_at':1,'model':'fixture','status':terminal,
            'output':[], 'usage':{'input_tokens':29,'output_tokens':17,'total_tokens':46},
            'incomplete_details':{'reason':'max_output_tokens'} if terminal == 'incomplete' else None,
            'error':{'code':'server_error','message':'synthetic secret body not retained'} if terminal == 'failed' else None}
        events = [{'type':'response.'+terminal,'response':response,'sequence_number':0}]
    async def run():
        parent = create_client(ProviderConfig('fixture',protocol,'https://fixture.invalid','fixture','synthetic'))
        client = scoped_client(parent,transport=httpx.MockTransport(lambda request: httpx.Response(
            200,headers={'content-type':'text/event-stream'},content=sse(events,named=True))))
        control = Control()
        try:
            output = [event async for event in client.stream(conversation(),control=control)]
            outcome = control.outcomes[0][1]
            assert outcome.delivery == 'confirmed' and outcome.terminal == terminal
            assert outcome.raw_usage['output_tokens'] == 17
            assert next(e for e in output if isinstance(e,StreamEnd)).terminal == terminal
            if protocol == 'openai':
                assert outcome.terminal_details == {
                    'incomplete_reason':'max_output_tokens' if terminal == 'incomplete' else None,
                    'error_code':'server_error' if terminal == 'failed' else None}
        finally:
            await client.aclose();await parent._client.close()
    asyncio.run(run())


@pytest.mark.parametrize('reason,usage', [('length', True), ('stop', False), (None, True)])
def test_compat_keeps_real_terminal_and_usage_even_on_choice_chunk(reason, usage):
    async def run():
        calls = []
        def transport(request):
            calls.append(request)
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                content=sse(compat_events(reason, usage=usage))+b'data: [DONE]\n\n')
        parent = create_client(ProviderConfig(name='fixture', base_url='https://fixture.invalid', protocol='openai-compat', model='fixture', api_key='synthetic'))
        client = scoped_client(parent, transport=httpx.MockTransport(transport))
        control = Control()
        try:
            events = [event async for event in client.stream(conversation(), control=control)]
            end = next(event for event in events if isinstance(event, StreamEnd))
            outcome = control.outcomes[0][1]
            assert len(calls) == 1
            assert end.terminal == outcome.terminal == reason
            assert outcome.delivery == ('confirmed' if reason else 'unknown')
            assert (outcome.raw_usage is not None) is usage
            if usage:
                assert outcome.raw_usage['total_tokens'] == 46
        finally:
            await client.aclose()
            await parent._client.close()
    asyncio.run(run())
