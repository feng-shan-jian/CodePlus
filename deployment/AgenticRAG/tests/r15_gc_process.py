"""Installed, real-process R15 collector/run/history helper; gates are explicit."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import runpy
import sys
import time
from uuid import UUID, uuid4

import agentic_rag
from agentic_rag.config import KnowledgeConfig, resolve_run
from agentic_rag.domain import ErrorCode, RagError
from agentic_rag.models.identity import process_birth
from agentic_rag.storage import Catalog, publication


def write(path, value):
    path = Path(path)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(pending, path)


def identity():
    return {'pid': os.getpid(), 'birth': process_birth(os.getpid()), 'python': sys.executable,
            'package': agentic_rag.__file__, 'time': time.time(), 'argv': sys.argv}


def wait(path, seconds=240):
    deadline = time.monotonic() + seconds
    while not Path(path).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('actual parent gate did not open: ' + str(path))
        time.sleep(.02)


def main(args):
    root = Path(args.root)
    settings = json.loads((root/'settings.json').read_text(encoding='utf-8'))
    config = KnowledgeConfig.model_validate_json(json.dumps(settings['config']))
    report = {'status':'RUNNING', 'identity':identity(), 'drop_calls':[]}
    catalog = Catalog(config.storage.data_dir)
    backend = lease = None
    prefix = args.name
    def save(stage, **data):
        value = {'stage':stage, 'identity':identity(), **data}
        report.setdefault('events', []).append(value)
        write(root/(prefix+'-'+stage+'.json'), value)
    try:
        if args.mode == 'history':
            from agentic_rag.citations import open_citation
            from agentic_rag.source_archive import read_version
            ids = settings['history']
            parser_calls = []
            def trace(frame, event, arg):
                if event == 'call' and frame.f_code.co_name == 'parse_document':
                    parser_calls.append(frame.f_code.co_filename)
            sys.setprofile(trace)
            assert all(not Path(path).exists() for path in settings.get('original_paths', []))
            report['citation'] = open_citation(catalog, UUID(ids['citation_id']))
            report['versions'] = []
            for item in ids['versions']:
                source = read_version(catalog, *(UUID(item[k]) for k in ('kb_id','revision_id','document_id','version_id')))
                assert source.text == item['text']
                assert not Path(source.version.source_uri.removeprefix('file:///')).is_file()
                report['versions'].append({'identity':item, 'text':source.text,
                    'sections':[section.model_dump(mode='json') for section in source.chunks.parsed.sections],
                    'raw_hash':source.version.raw_hash})
            assert report['citation'] == ids['citation']
            sys.setprofile(None)
            assert not parser_calls
            report['parser_calls'] = parser_calls
            assert not any(k == 'pymilvus' or k.startswith('pymilvus.') or k == 'torch' or k.startswith('torch.') for k in sys.modules)
            report['external_service_or_gpu_imports'] = []
        elif args.mode == 'pin':
            # The gate is inside the actual BEGIN IMMEDIATE used by start_run.
            original = catalog._db.transaction
            run_id = uuid4()
            @contextmanager
            def transaction(*, write=False):
                with original(write=write) as db:
                    yield db
                    if write and args.stage == 'before_pin_commit' and db.execute('SELECT 1 FROM run_pins WHERE run_id=?', (str(run_id),)).fetchone():
                        save('gate', position='before_pin_commit', run_id=str(run_id))
                        wait(root/(prefix+'-release'))
            if args.stage == 'before_pin_commit':
                catalog._db.transaction = transaction
            save('ready')
            if args.barrier: wait(root/args.barrier)
            lease = catalog.start_run(UUID(settings['kb_id']), resolve_run(config, 'qa'), run_id=run_id)
            catalog._db.transaction = original
            save('pinned', run=lease.run.model_dump(mode='json'), pin=lease.pin.model_dump(mode='json'))
            wait(root/(prefix+'-finish'))
            lease.close()
            report['pin_after_close'] = catalog.get_pin(run_id).model_dump(mode='json')
        else:
            from agentic_rag.indexes.milvus import MilvusRevisionIndex
            # Fault/competition drivers call the exact production explicit
            # coordinator, pausing automatic admission only in this child.
            automatic = catalog._maintain_indexes
            if args.mode == 'collect': catalog._maintain_indexes = lambda: []
            backend = MilvusRevisionIndex(config.storage, catalog, timeout=5)
            drop = backend.client.drop_collection
            def tracked(*values, **kwargs):
                report['drop_calls'].append({'name':values[0], 'started':time.time()})
                result = drop(*values, **kwargs)
                report['drop_calls'][-1]['returned'] = time.time()
                return result
            backend.client.drop_collection = tracked
            save('ready')
            if args.barrier: wait(root/args.barrier)
            def observe(stage, value):
                if stage == args.stage:
                    save('gate', position=stage, artifact=value, drop_calls=report['drop_calls'])
                    wait(root/(prefix+'-release'))
            if args.mode == 'collect':
                try:
                    report['results'] = catalog.maintain_indexes(limit=32, backend=backend, observer=observe)
                except RagError as exc:
                    if exc.error.code != ErrorCode.LIBRARY_BUSY:
                        raise
                    report['coordination_error'] = exc.error.model_dump(mode='json')
                    report['results'] = []
            else:
                deadline = time.monotonic()+45
                while publication.artifact(catalog, UUID(settings['old_revision']))['state'] != 'RECLAIMED':
                    if time.monotonic() >= deadline: raise TimeoutError('startup automatic reclamation did not finish')
                    time.sleep(.05)
                report['automatic_reclaimed'] = publication.artifact(catalog, UUID(settings['old_revision']))
            if args.mode != 'collect': catalog._maintain_indexes = automatic
        report['status'] = 'PASS'
    except BaseException as exc:
        report.update(status='FAIL', error=repr(exc))
        raise
    finally:
        if lease: lease.close()
        if backend:
            if args.mode == 'collect': backend._close_transport()
            else: backend.close()
            report['transport_closed'] = backend.wait_closed(30)
        report['finished'] = time.time()
        write(root/(prefix+'-result.json'), report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('pin','collect','startup','history'))
    parser.add_argument('--root', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--stage', default='')
    parser.add_argument('--barrier', default='')
    main(parser.parse_args())
