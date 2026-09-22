"""Explicit live provider preflight; never used as mocked Agent acceptance.

Uses the selected existing CodePlus provider without changing its configuration.
Only synthetic public text is sent; credentials, headers and error bodies are not
written. Run from the intended CodePlus configuration directory.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import time

import httpx
from openai import AsyncOpenAI

from codeplus.config import load_config


async def main(report):
    config = load_config()
    result = {
        'kind': 'real_network_provider_preflight',
        'started_at': datetime.now(timezone.utc).isoformat(),
        'python': sys.version, 'platform': platform.platform(),
        'argv': sys.argv, 'cwd': str(Path.cwd()),
        'openai': importlib.metadata.version('openai'),
        'httpx': importlib.metadata.version('httpx'),
        'configuration_changed': False, 'probes': [],
    }
    for provider in config.providers:
        if provider.protocol != 'openai-compat':
            continue
        base = {'configured_model': provider.model, 'protocol': provider.protocol,
                'base_url': provider.base_url, 'credential_present': bool(provider.resolve_api_key())}
        if not provider.resolve_api_key():
            result['probes'].append({**base, 'status': 'credential_missing'})
            continue
        async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as http:
            client = AsyncOpenAI(api_key=provider.resolve_api_key(), base_url=provider.base_url,
                                 max_retries=0, timeout=30.0, http_client=http)
            for cap in (8, 32):
                payload = {'model': provider.model, 'messages': [
                    {'role': 'user', 'content': 'Count upward from one using English words until stopped.'}],
                    'max_tokens': cap, 'stream': True, 'stream_options': {'include_usage': True}}
                item = {**base, 'cap': cap, 'request_sha256': hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()).hexdigest()}
                started = time.monotonic()
                try:
                    stream = await client.chat.completions.create(**payload)
                    reasons, usages, models, chunks = [], [], [], 0
                    async with stream:
                        async for chunk in stream:
                            chunks += 1
                            models.append(chunk.model)
                            if chunk.usage:
                                usages.append(chunk.usage.model_dump(mode='json'))
                            for choice in chunk.choices:
                                if choice.finish_reason:
                                    reasons.append(choice.finish_reason)
                    item.update(status='response', models=sorted(set(models)), chunks=chunks,
                                finish_reasons=reasons, raw_usage=usages)
                except Exception as error:
                    item.update(status='error', error_type=type(error).__name__,
                                status_code=getattr(error, 'status_code', None))
                    # Provider error type/code are useful without its textual body.
                    body = getattr(error, 'body', None)
                    if isinstance(body, dict):
                        body = body.get('error', body)
                        if isinstance(body, dict):
                            item['provider_error_code'] = body.get('code')
                            item['provider_error_type'] = body.get('type')
                item['elapsed_ms'] = round((time.monotonic()-started)*1000, 3)
                result['probes'].append(item)
                report.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
            await client.close()
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    result['note'] = 'Connectivity/cap samples only: not a tokenizer hard-bound proof or Agent acceptance.'
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.report.resolve()))
