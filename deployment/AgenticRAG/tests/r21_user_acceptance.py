"""Opt-in installed R21 CLI/Remote lifecycle with actual GPU/Milvus/DeepSeek.

Uses a small new fixture, not an answer-quality evaluation. No synthetic network
or retrieval responses. Credentials are passed only in the child environment.
"""
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from uuid import UUID

import agentic_rag
import codeplus
from agentic_rag.adapters.codeplus.policy import DevelopmentConfig
from agentic_rag.config import KnowledgeConfig, WorkerExecutionConfig
from agentic_rag.storage import Catalog
from codeplus.config import load_config

H = runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
QUESTION = ('请核实 Vega 望远镜在 2025 年的安全证书颜色和保管地点，用中文简短回答。'
            '生成英文检索 query，打开返回的来源核对。仅回答此问题并保留英文原文引文。')


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(args):
    root = Path(args.root); root.mkdir(parents=True)
    data = H['configuration'](root/'data', args.endpoint).model_dump(mode='json')
    data['storage']['namespace'] = 'r21_user_acceptance'
    data['retrieval'].update(mode='auto', route='hybrid', rerank=True,
        rerank_candidates=24, context_chunks=24, context_tokens=50000)
    # Same approved small live budget as R20; no increase for a retry.
    for kind, total, duration in [('qa', 200000, 300000), ('report', 400000, 600000)]:
        data['budgets'][kind].update(searches=10, opens=10, total_tokens=total, duration_ms=duration,
            finish_reserve_tokens=34000, finish_reserve_ms=30000)
    config = KnowledgeConfig.model_validate_json(json.dumps(data))
    settings = DevelopmentConfig(knowledge=config, worker=WorkerExecutionConfig(executable=args.cuda_python,
        model_cache=args.model_cache, runtime_dir=str(root/'worker'), idle_timeout_ms=1000),
        answer_tokenizer=args.answer_tokenizer, explore_output_cap=4096, finish_input_upper=12000,
        finalize_output_cap=4096, repair_output_cap=4096, compact_output_cap=2048,
        max_iterations=12, max_tool_attempts=30, cleanup_grace_ms=1000)
    write(root/'development.json', settings.model_dump(mode='json'))
    write(root/'state.json', {'runs': []})
    (root/'中文 source.md').write_text('# Vega certificate\nIn 2025, the Vega telescope has a blue safety certificate. '
        'The blue certificate is held in the harbor registry.\n', encoding='utf-8')
    (root/'broken.md').write_bytes(b'\xff invalid utf8')


def provider(args):
    return next(p for p in load_config(Path(args.provider_config)).providers if p.protocol == 'openai-compat'
        and p.model in {'deepseek-chat', 'deepseek-reasoner'}
        and p.base_url.rstrip('/') in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'})


def host_config(root, value):
    work = root/'host'; (work/'.codeplus').mkdir(parents=True, exist_ok=True)
    # JSON is valid YAML. No key is ever persisted.
    write(work/'.codeplus/config.yaml', {'providers': [{'name': value.name, 'protocol': value.protocol,
        'base_url': value.base_url, 'model': value.model, 'api_key': '', 'thinking': value.thinking}],
        'enable_fork': False, 'knowledge_development_config': str(root/'development.json')})
    return work


def cli(root, report, value, text, *, library=None, expect=0, options=()):
    work = host_config(root, value)
    env = dict(os.environ, OPENAI_API_KEY=value.resolve_api_key(), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    argv = [sys.executable, '-I', '-B', '-X', 'utf8', '-m', 'codeplus', '-p', text,
            '--output-format', 'stream-json', *options]
    if library:
        argv += ['--knowledge-library', str(library)]
    started = time.time()
    result = subprocess.run(argv, cwd=work, env=env, capture_output=True, text=True, encoding='utf-8', timeout=660)
    name = report['action']+'-'+str(len(report.setdefault('commands', [])))
    out, err = root/(name+'.stdout'), root/(name+'.stderr')
    out.write_text(result.stdout, encoding='utf-8'); err.write_text(result.stderr, encoding='utf-8')
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    final = next((event for event in reversed(events) if event['type'] == 'result'), None)
    report['commands'].append({'argv': argv, 'cwd': str(work), 'started': started, 'finished': time.time(),
        'exit_code': result.returncode, 'expected_exit': expect, 'stdout': str(out), 'stderr': str(err),
        'stdout_sha256': sha(out), 'stderr_sha256': sha(err), 'result': final})
    write(root/(report['action']+'.json'), report)
    print(name, result.returncode, final and final.get('status'), flush=True)
    assert result.returncode == expect, (result.returncode, expect)
    assert final is not None
    return final


def lifecycle(root, report, value):
    state = json.loads((root/'state.json').read_text(encoding='utf-8'))
    command = lambda text, **kw: cli(root, report, value, text, **kw)
    kb = command('/knowledge create "中文 library A"')['data']['kb_id']
    state['kb_id'] = kb
    state['other_library'] = command('/knowledge create "library B"')['data']['kb_id']
    write(root/'state.json', state)
    use = lambda text, **kw: command('/knowledge '+text, library=kb, **kw)
    imported = use(f'import "{root / "中文 source.md"}" "{root / "broken.md"}"', expect=2)
    state['initial_revision'] = imported['data']['receipt']['revision_id']
    batch = imported['data']['summary']['batch_id']
    state['document_id'] = use('sources')['data'][0]['document_id']
    (root/'broken.md').write_text('# Repair\nThe Vega registry was checked in 2025.\n', encoding='utf-8')
    use('retry '+batch, expect=1)
    pending = use('status')['data']['pending_mutation_id']
    use('recover '+pending, expect=3)
    use('abandon '+pending)
    use('retry '+batch+' --accept-input-changes')
    use('import', expect=1)
    use('use wrong-uuid', expect=1)
    use('off')
    write(root/'state.json', state)


def task(root, report, value, kind):
    state = json.loads((root/'state.json').read_text(encoding='utf-8'))
    options = ['--knowledge-mode', 'fixed']
    if kind == 'report':
        target = root/'host/vega-report.md'
        text = '请生成简短研究报告，包含结论、证据和局限。'+QUESTION
        options += ['--knowledge-report', str(target), '--mode', 'acceptEdits']
    else:
        text = QUESTION
    result = cli(root, report, value, text, library=state['kb_id'], options=options)
    state['runs'].append(result['run_id'])
    catalog = Catalog(root/'data')
    with catalog._db.transaction() as db:
        citations = [r[0] for r in db.execute('SELECT citation_id FROM saved_citations WHERE run_id=?', (result['run_id'],))]
    assert citations
    state.setdefault('citations', []).extend(citations)
    if kind == 'report':
        assert target.is_file()
        report['report_file'] = {'path': str(target), 'sha256': sha(target), 'bytes': target.stat().st_size}
    write(root/'state.json', state)


def update(root, report, value):
    state = json.loads((root/'state.json').read_text(encoding='utf-8'))
    command = lambda text, **kw: cli(root, report, value, '/knowledge '+text, library=state['kb_id'], **kw)
    moved = root/'中文 changed source.md'
    moved.write_text('# Vega revised certificate\nIn 2025, the Vega telescope has a red safety certificate. '
        'The red certificate is held in the mountain registry.\n', encoding='utf-8')
    command(f'reimport {state["document_id"]} "{moved}"')
    settings = json.loads((root/'development.json').read_text(encoding='utf-8'))
    settings['knowledge']['model_profiles'][0]['instruction'] = 'Retrieve passages for the requested scientific question'
    write(root/'development.json', settings)
    proposal = command('model', expect=3)['data']['proposal_id']
    command('model '+proposal+' --choice retry', expect=3)
    command('model '+proposal+' --choice confirm')
    command('model '+proposal+' --choice confirm')
    command('sources --revision '+state['initial_revision'])
    history = command('open '+state['citations'][0])
    assert 'blue' in json.dumps(history['data'])
    write(root/'state.json', state)


async def remote(root, report, value):
    from websockets.asyncio.server import serve
    from websockets.asyncio.client import connect
    from codeplus.remote import RemoteServer
    state = json.loads((root/'state.json').read_text(encoding='utf-8'))
    work = host_config(root, value); os.chdir(work)
    config = load_config(work/'.codeplus/config.yaml'); config.providers = [value]
    server = RemoteServer(config.providers, config=config, addr='127.0.0.1', port=0)
    server._init_agent()
    events = []
    async def command(ws, content):
        await ws.send(json.dumps({'type': 'user_message', 'data': {'content': content}}))
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), 330)); events.append(event)
            if event['type'] == 'command_done':
                return
    try:
        async with serve(server._ws_handler, '127.0.0.1', 0) as running:
            port = running.sockets[0].getsockname()[1]; report['port'] = port
            async with connect(f'ws://127.0.0.1:{port}') as ws:
                await command(ws, '/knowledge use '+state['kb_id'])
                await command(ws, '/knowledge continue --run '+state['runs'][-1]+' '+QUESTION+'请重新核实修订后的当前记录。')
                report['outcome'] = asdict(server.last_knowledge_outcome)
                assert report['outcome']['status'] == 'completed'
                assert sum(e['type'] == 'loop_complete' for e in events) == 1
                assert not server._streaming and not server._knowledge_active
                state['runs'].append(server.last_knowledge_run_id)
                await command(ws, '/session')
                await command(ws, '/knowledge off')
                write(root/'state.json', state)
    finally:
        server._cancel_active()
        if server._message_tasks:
            await asyncio.gather(*tuple(server._message_tasks), return_exceptions=True)
        if server._ui_tasks:
            await asyncio.gather(*server._ui_tasks, return_exceptions=True)
        await server.agent.client._client.close()
        server.session.close()
        report['events'] = events


def history_after_delete(root, report, value):
    state = json.loads((root/'state.json').read_text(encoding='utf-8'))
    cli(root, report, value, '/knowledge remove '+state['document_id'], library=state['kb_id'])
    sources = cli(root, report, value, '/knowledge sources', library=state['kb_id'])
    assert all(row['document_id'] != state['document_id'] for row in sources['data'])
    old = cli(root, report, value, '/knowledge open '+state['citations'][0], library=state['kb_id'])
    assert 'blue' in json.dumps(old['data'])
    cli(root, report, value, '/knowledge open '+state['citations'][0], library=state['other_library'], expect=1)
    cli(root, report, value, '/knowledge status', library=state['kb_id'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'lifecycle', 'qa', 'report', 'update', 'remote', 'delete'])
    parser.add_argument('--root', required=True); parser.add_argument('--provider-config')
    parser.add_argument('--endpoint'); parser.add_argument('--cuda-python'); parser.add_argument('--model-cache')
    parser.add_argument('--answer-tokenizer'); args = parser.parse_args()
    root = Path(args.root).resolve()
    assert 'site-packages' in Path(codeplus.__file__).parts and 'site-packages' in Path(agentic_rag.__file__).parts
    report = {'action': args.action, 'argv': sys.argv, 'cwd': os.getcwd(), 'started': time.time(),
        'synthetic': False, 'status': 'RUNNING'}
    try:
        if args.action == 'prepare':
            prepare(args)
        else:
            value = provider(args)
            report['provider'] = {key: getattr(value, key) for key in ('name', 'protocol', 'base_url', 'model')}
            if args.action == 'lifecycle': lifecycle(root, report, value)
            elif args.action in ('qa', 'report'): task(root, report, value, args.action)
            elif args.action == 'update': update(root, report, value)
            elif args.action == 'remote': asyncio.run(remote(root, report, value))
            elif args.action == 'delete': history_after_delete(root, report, value)
        report['status'] = 'PASS'
    except BaseException as error:
        report.update(status='FAIL', error=type(error).__name__)
        raise
    finally:
        report['finished'] = time.time()
        report['modules'] = {}
        for name, module in tuple(sys.modules.items()):
            path = getattr(module, '__file__', None)
            if path and (name.startswith('codeplus.') or name.startswith('agentic_rag.')):
                assert 'site-packages' in Path(path).parts
                report['modules'][name] = {'path': path, 'sha256': sha(path)}
        write(root/(args.action+'.json'), report)


if __name__ == '__main__':
    main()
