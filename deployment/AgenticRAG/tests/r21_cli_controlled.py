"""Opt-in subprocess probe: installed real main/Agent/outcomes, synthetic HTTP/index.

Used by R21 to check OS exits, not as real network or answer-quality evidence.
"""
import argparse
import asyncio
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
from uuid import UUID

import httpx
import pytest
import yaml

import agentic_rag
import codeplus
from codeplus import __main__ as host
from codeplus.client import scoped_client
from agentic_rag.adapters.codeplus import policy as P
from agentic_rag.config import KnowledgeConfig

M = runpy.run_path(str(Path(__file__).with_name('test_run_modes.py')))
H = M['H']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--scenario', required=True, choices=['complete', 'partial', 'empty', 'failed', 'cancelled', 'invalid_config'])
    parser.add_argument('--command', choices=['ask', 'report', 'continue'])
    parser.add_argument('--body', default='Explain the certificate')
    args = parser.parse_args()
    root = Path(args.root); root.mkdir(parents=True)
    os.chdir(root)
    assert 'site-packages' in Path(codeplus.__file__).parts
    assert 'site-packages' in Path(agentic_rag.__file__).parts
    catalog, kb, base, rows, _ = M['R']['published'](root)
    settings = M['settings'](base, root)
    if args.scenario == 'partial':
        data = settings.knowledge.model_dump(mode='json'); data['budgets']['qa']['searches'] = 1
        settings = settings.model_copy(update={'knowledge': KnowledgeConfig.model_validate_json(json.dumps(data))})
    config_path = root/'development.json'; config_path.write_text(settings.model_dump_json(), encoding='utf-8')
    if args.scenario == 'invalid_config':
        config_path.write_text('{"unexpected_private_marker":"do-not-echo-this-value"}', encoding='utf-8')
    (root/'.codeplus').mkdir(exist_ok=True)
    (root/'.codeplus/config.yaml').write_text(yaml.safe_dump({'providers': [{'name': 'fixture',
        'protocol': 'openai-compat', 'base_url': 'https://fixture.invalid', 'model': 'fixture', 'api_key': 'synthetic'}],
        'knowledge_development_config': str(config_path)}, allow_unicode=True), encoding='utf-8')
    patch = pytest.MonkeyPatch(); M['host_dependencies'](patch, settings, rows, [])
    calls = []
    async def transport(request):
        body = json.loads(request.content); calls.append(body)
        if args.scenario == 'cancelled':
            asyncio.get_running_loop().call_later(.03, asyncio.current_task().cancel)
            await asyncio.Future()
        if args.scenario == 'failed':
            return httpx.Response(400, json={'error': {'message': 'controlled provider rejection', 'type': 'invalid_request_error'}})
        if args.scenario == 'empty':
            content = H['sse_text'](json.dumps({'markdown': 'No evidence.', 'citations': []}))
        elif not any(m['role'] == 'tool' and '<source ' in str(m['content']) for m in body['messages']) or args.scenario == 'partial' and len(calls) == 2:
            content = H['sse_text'](calls=('search'+str(len(calls)), 'knowledge_search', {'query': 'telescope certificate'}), terminal='tool_calls')
        else:
            source = next(m['content'] for m in body['messages'] if m['role'] == 'tool' and '<source ' in m['content'])
            item = json.JSONDecoder().raw_decode(source)[0]['items'][0]
            text = source.split('>\n', 1)[1].split('\n</source>', 1)[0]
            content = H['sse_text'](json.dumps({'markdown': 'Verified fact [^'+item['evidence_id']+']',
                'citations': [{'evidence_id': item['evidence_id'], 'spans': item['returned_spans'], 'quotes': [text]}]}))
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=content)
    patch.setattr(P, 'scoped_client', lambda parent: scoped_client(parent, transport=httpx.MockTransport(transport)))
    cli_options = ['--knowledge-library', str(kb), '--output-format', 'stream-json']
    parent = None
    if args.command == 'continue':
        # Seed a real persisted parent through the same installed main.
        seed = io.StringIO()
        sys.argv = ['codeplus', '-p', 'Initial certificate research', *cli_options]
        with redirect_stdout(seed):
            try:
                host.main()
            except SystemExit as error:
                assert error.code == 0
        (root/'parent.stdout').write_text(seed.getvalue(), encoding='utf-8')
        seed_result = next(json.loads(line) for line in seed.getvalue().splitlines()
                           if line.startswith('{') and json.loads(line)['type'] == 'result')
        assert seed_result['status'] == 'completed'
        parent = seed_result['run_id']
    prompt = args.body
    target = root/'report.md'
    if args.command:
        options = ' --output "'+str(target)+'"' if args.command == 'report' else ' --run '+parent if parent else ''
        prompt = '/knowledge '+args.command+options+' '+args.body
    sys.argv = ['codeplus', '-p', prompt, *cli_options,
                *(['--mode', 'acceptEdits'] if args.command == 'report' else [])]
    captured = io.StringIO()
    try:
        with redirect_stdout(captured):
            host.main()
    finally:
        output = captured.getvalue(); print(output, end='', flush=True)
        modules = {}
        for name, module in tuple(sys.modules.items()):
            path = getattr(module, '__file__', None)
            if path and (name.startswith('codeplus.') or name.startswith('agentic_rag.')):
                assert 'site-packages' in Path(path).parts
                modules[name] = {'path': path, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
        (root/'modules.json').write_text(json.dumps(modules, indent=2)+'\n', encoding='utf-8')
        (root/'requests.json').write_text(json.dumps({'scenario': args.scenario, 'calls': len(calls),
            'synthetic': True, 'kb_id': str(kb), 'command': args.command, 'body': args.body,
            'user_messages': [m['content'] for request in calls for m in request['messages'] if m['role'] == 'user']},
            indent=2)+'\n', encoding='utf-8')
        patch.undo()
        with catalog._db.transaction() as db:
            runs = list(db.execute('SELECT run_id,status,stop_reason FROM runs'))
        results = [json.loads(line) for line in output.splitlines() if line.startswith('{') and json.loads(line)['type'] == 'result']
        expected = {'complete': 'completed', 'partial': 'partial', 'empty': 'incomplete',
                    'failed': 'failed', 'cancelled': 'cancelled', 'invalid_config': 'failed'}[args.scenario]
        check = {'status': 'CHECKING', 'results': results, 'persisted_runs': runs, 'expected_status': expected}
        (root/'validation.json').write_text(json.dumps(check, indent=2)+'\n', encoding='utf-8')
        assert len(results) == 1 and results[0]['status'] == expected
        result = results[0]
        if args.scenario == 'invalid_config':
            assert not runs and result['stop_reason'] == 'startup_error'
            assert 'do-not-echo-this-value' not in output
        else:
            assert len(runs) == (2 if parent else 1)
            actual = next(row for row in runs if row[0] == result['run_id'])
            assert tuple(actual) == (result['run_id'], result['status'], result['stop_reason'])
            assert result['stop_reason'] != 'startup_error'
            if args.command:
                assert any(m['role'] == 'user' and m['content'] == args.body for request in calls for m in request['messages'])
                run = catalog.get_run(UUID(result['run_id']))
                assert run.kb_id == kb and run.revision_id == catalog.get_library(kb).current_revision_id
                assert str(run.parent_run_id) == parent if parent else run.parent_run_id is None
                assert catalog.get_pin(run.run_id).state == 'released'
                if args.command == 'report':
                    assert result['save']['status'] == 'saved' and target.is_file()
                check.update(body_preserved=True, parent_run_id=parent, revision_id=str(run.revision_id))
        check['status'] = 'PASS'
        (root/'validation.json').write_text(json.dumps(check, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
