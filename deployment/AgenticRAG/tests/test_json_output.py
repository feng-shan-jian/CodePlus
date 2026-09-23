"""Knowledge JSON output uses the measured template and the actual SDK body."""
import asyncio
import copy
import json
import os
from pathlib import Path
import runpy

import httpx
import pytest

from codeplus.client import create_client, scoped_client
from codeplus.config import ProviderConfig
from codeplus.conversation import ConversationManager
from agentic_rag.adapters.codeplus.meter import DeepSeekTextMeter, MeterUnavailable
from agentic_rag.adapters.codeplus._vendor.deepseek_v41 import encode_messages

H = runpy.run_path(str(Path(__file__).with_name('test_codeplus_integration.py')))
FORMAT = {'type': 'json_object'}


@pytest.fixture
def meter():
    path = os.environ.get('R12_ANSWER_TOKENIZER')
    if not path:
        pytest.skip('set R12_ANSWER_TOKENIZER to the pinned real tokenizer file')
    return DeepSeekTextMeter(path, model='deepseek-chat', protocol='openai-compat',
                            base_url='https://api.deepseek.com')


@pytest.mark.parametrize('with_system', [False, True])
@pytest.mark.parametrize('with_tools', [False, True])
def test_json_format_is_rendered_by_the_pinned_full_template(meter, with_system, with_tools):
    messages = [{'role': 'user', 'content': '中文 JSON "\\\n'}]
    if with_system:
        messages.insert(0, {'role': 'system', 'content': 'Return JSON.'})
    body = {'model': 'deepseek-chat', 'messages': messages, 'max_tokens': 8,
            'stream': True, 'stream_options': {'include_usage': True}}
    tools = [{'type': 'function', 'function': {'name': 'read_value',
              'parameters': {'type': 'object', 'properties': {}}}}]
    if with_tools:
        body['tools'] = tools
    for use_json in (False, True):
        if use_json:
            body['response_format'] = FORMAT
        rendered_messages = copy.deepcopy(messages)
        if with_tools or use_json:
            if rendered_messages[0]['role'] != 'system':
                rendered_messages.insert(0, {'role': 'system', 'content': ''})
            if with_tools:
                rendered_messages[0]['tools'] = tools
            if use_json:
                rendered_messages[0]['response_format'] = FORMAT
        rendered = encode_messages(rendered_messages, thinking_mode='chat', reasoning_effort=75)
        assert meter.input_upper_bound(json.dumps(body).encode(), output_cap=8) == len(rendered.encode())
        assert ('## Response Format:' in rendered) is use_json
    assert 'json-object' in meter.frozen_identity()['meter']


@pytest.mark.parametrize('value', [None, {}, {'type': 'text'}, {'type': 'json_schema'},
                                  {'type': 'json_object', 'schema': {}}])
def test_unverified_formats_are_refused(meter, value):
    body = {'model': 'deepseek-chat', 'messages': [{'role': 'user', 'content': 'JSON'}],
            'max_tokens': 8, 'stream': True, 'stream_options': {'include_usage': True},
            'response_format': value}
    with pytest.raises(MeterUnavailable, match='unsupported_response_format'):
        meter.input_upper_bound(json.dumps(body).encode(), output_cap=8)


@pytest.mark.parametrize('purpose', ['agent', 'finalize', 'citation_repair', 'compact'])
def test_knowledge_previews_and_actual_wire_match_without_changing_ordinary_calls(tmp_path, purpose):
    async def run():
        parent = create_client(ProviderConfig('fixture', 'openai-compat', 'https://fixture.invalid', 'fixture', 'synthetic'))
        scope, _ = H['setup_scope'](tmp_path, parent)
        observed, previews = [], []
        count = scope.meter.input_upper_bound
        def measured(raw, *, output_cap):
            previews.append(json.loads(raw))
            return count(raw, output_cap=output_cap)
        scope.meter.input_upper_bound = measured
        def transport(request):
            observed.append(json.loads(request.content))
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=H['sse_text']('{}'))
        await scope.client.aclose()
        scope.client = scoped_client(parent, transport=httpx.MockTransport(transport))
        conversation = ConversationManager()
        conversation.add_user_message('Return JSON after reading.')
        scope._question = 'Return JSON after reading.'
        scope.purpose = purpose
        tools = None
        if purpose == 'agent':
            scope._exploration_window(conversation.history)
            tools = scope.registry.get_all_schemas('openai-compat')
        elif purpose != 'compact':
            scope._trim_finish(conversation)
        preview = previews[-1] if previews else None
        try:
            async for _ in scope.client.stream(conversation, system=scope.system_prompt,
                                                tools=tools, control=scope.model_control(purpose)):
                pass
            use_json = purpose in {'finalize', 'citation_repair'}
            assert ('response_format' in observed[0]) is use_json
            if use_json:
                assert observed[0]['response_format'] == FORMAT
            if purpose == 'agent':
                # The host registry is flat; Chat Completions wraps each
                # function schema without changing its name or parameters.
                assert [{'type': item['type'], **item['function']} for item in observed[0]['tools']] == scope.registry.get_all_schemas('openai-compat')
            else:
                assert 'tools' not in observed[0]
            assert previews[-1] == observed[0]  # Actual HTTP gate measured the added field.
            if preview is not None:
                assert preview == observed[0]  # Source and finish windows count that same body.
            with scope.catalog._db.transaction() as db:
                assert db.execute('SELECT count(*) FROM model_requests').fetchone() == (1,)
            await parent._client.close()
            ordinary_http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
            parent._client = parent._client.with_options(http_client=ordinary_http, max_retries=0)
            async for _ in parent.stream(conversation, system='Ordinary prose'):
                pass
            assert len(observed) == 2 and 'response_format' not in observed[1]
        finally:
            await scope.aclose()
            await parent._client.close()
    asyncio.run(run())
