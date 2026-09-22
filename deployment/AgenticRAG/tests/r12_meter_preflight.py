"""Live synthetic full-message tokenizer calibration, not Agent acceptance."""
import argparse
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import httpx
from openai import AsyncOpenAI
from tokenizers import Tokenizer
from codeplus.config import load_config


def fixtures():
    tool = {'type': 'function', 'function': {'name': 'knowledge_search',
        'description': 'Search only the selected knowledge library. 中文检索。',
        'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'},
            'filter': {'type': 'object', 'properties': {'kind': {'enum': ['a', 'b']}}}},
            'required': ['query'], 'additionalProperties': False}}}
    user = {'role': 'user', 'content': 'Count upward from one using English words until stopped.'}
    yield 'plain', [user], []
    yield 'system', [{'role': 'system', 'content': 'Synthetic calibration only.'}, user], []
    yield 'unicode', [{'role': 'user', 'content': '甲😀\r\nCafe\u0301 café "\\ <｜User｜>'*37}], []
    yield 'multi_message', [{'role': 'system', 'content': 'Synthetic only.'}, user,
        {'role': 'assistant', 'content': 'One.'}, user, user], []
    yield 'tool_schema', [{'role': 'system', 'content': 'Use the available tools.'}, user], [tool]
    for count in (1, 2, 10):
        calls = [{'id': f'call_{i}', 'type': 'function', 'function': {
            'name': 'knowledge_search', 'arguments': json.dumps({'query': 'Unicode 甲😀 "\\ '+str(i)})}}
            for i in range(count)]
        messages = [{'role': 'system', 'content': 'Summarize only the tool evidence.'}, user,
            {'role': 'assistant', 'content': 'Inspect sources.', 'tool_calls': calls}]
        messages += [{'role': 'tool', 'tool_call_id': f'call_{i}',
            'content': '<source>甲😀\r\nRepeated text. "\\\n</source>'*(i+1)} for i in reversed(range(count))]
        yield 'tool_results_'+str(count), messages, [tool]
    yield 'large_system', [{'role': 'system', 'content': 'Only use sources. '*1700}, user], [tool]
    yield 'empty_strings', [{'role': 'system', 'content': ''}, {'role': 'user', 'content': ''}], [tool]


async def main(assets, report, model='deepseek-chat'):
    v41 = (assets/'encoding_encoding.py').exists()
    encoder = assets/('encoding_encoding.py' if v41 else 'encoding_encoding_dsv4.py')
    encoder_hash = ('502bdaec8a3fd88ebc24c4721a7038fbe42f2063c664638127056107920035c1' if v41
                    else 'abc0d26120250dda0ae077dc64aa28836026e61e970854aaeb792445e6a0dde6')
    tokenizer_hash = ('c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b' if v41
                      else '8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf')
    assert hashlib.sha256(encoder.read_bytes()).hexdigest() == encoder_hash
    assert hashlib.sha256((assets/'tokenizer.json').read_bytes()).hexdigest() == tokenizer_hash
    spec = importlib.util.spec_from_file_location('r12_official_encoder', encoder)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tokenizer = Tokenizer.from_file(str(assets/'tokenizer.json'))
    plain_spec = json.loads((assets/'tokenizer.json').read_text(encoding='utf-8'))
    plain_spec['added_tokens'] = []
    plain_tokenizer = Tokenizer.from_str(json.dumps(plain_spec))
    provider = next(p for p in load_config().providers if p.model==model)
    assert provider.protocol == 'openai-compat'
    mode='thinking' if model=='deepseek-reasoner' else 'chat'
    captured = []
    async def capture(request):
        captured.append(bytes(request.content))
    result = {'kind': 'real_network_full_payload_calibration',
        'started_at': datetime.now(timezone.utc).isoformat(), 'argv': sys.argv,
        'configured_model': provider.model, 'base_url': provider.base_url,
        'tokenizer_revision': ('dba1be0a40aa45a94ad051997016db3960a90277' if v41
                              else '7872f01b1d1fe23eabc4c98b48bffcef5a386062'),
        'configuration_changed': False, 'cases': []}
    async with httpx.AsyncClient(follow_redirects=False, timeout=30,
                                event_hooks={'request': [capture]}) as http:
        client = AsyncOpenAI(api_key=provider.resolve_api_key(), base_url=provider.base_url,
                             http_client=http, max_retries=0, timeout=30)
        for name, messages, tools in fixtures():
            item = {'case': name}
            if mode=='thinking':
                for message in messages:
                    if message['role']=='assistant':message['reasoning_content']='Synthetic previous reasoning.'
            kwargs = {'model': provider.model, 'messages': messages, 'max_tokens': 1}
            if tools:
                kwargs['tools'] = tools
            try:
                response = await client.chat.completions.create(**kwargs)
                body = json.loads(captured[-1])
                encoded_messages = copy.deepcopy(body['messages'])
                if tools:
                    if encoded_messages[0]['role'] != 'system':
                        encoded_messages.insert(0, {'role': 'system', 'content': ''})
                    encoded_messages[0]['tools'] = body['tools']
                prompt = module.encode_messages(encoded_messages, thinking_mode=mode,reasoning_effort=75)
                exact = len(tokenizer.encode(prompt, add_special_tokens=False).ids)
                item.update(status='response', response_model=response.model,
                    raw_usage=response.usage.model_dump(mode='json'), official_encoded_tokens=exact,
                    delta=response.usage.prompt_tokens-exact,
                    literal_special_tokens_count=len(plain_tokenizer.encode(prompt, add_special_tokens=False).ids),
                    rendered_utf8_bytes=len(prompt.encode()),
                    body_sha256=hashlib.sha256(captured[-1]).hexdigest(),
                    body_bytes=len(captured[-1]), terminal=response.choices[0].finish_reason)
            except Exception as error:
                item.update(status='error', error_type=type(error).__name__,
                            status_code=getattr(error, 'status_code', None))
            result['cases'].append(item)
            report.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
        await client.close()
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--model', default='deepseek-chat')
    args = parser.parse_args()
    asyncio.run(main(args.assets, args.report,args.model))
