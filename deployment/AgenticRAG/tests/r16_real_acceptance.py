"""Installed GPU/Milvus switch, first old read, actual outage/retry and keep."""

import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import agentic_rag
from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, WorkerExecutionConfig
from agentic_rag.ingestion import InputSelection, begin_changes, build_changes
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.model_switch import inspect_model_switch, apply_model_switch
from agentic_rag.models import FrozenTokenizer, LocalModelClient
from agentic_rag.retrieval.dense import DenseSearch
from agentic_rag.storage import Catalog, publication

H = runpy.run_path(str(Path(__file__).with_name('publication_support.py')))


def main(args):
    root = Path(args.root).resolve(); root.mkdir(parents=True)
    report = {'status':'RUNNING', 'started':time.time(), 'package':agentic_rag.__file__,
        'python':sys.executable, 'commands':[], 'scope':'real GPU and Milvus; synthetic named documents; no quality claim'}
    report['index_timeouts_seconds'] = {'normal':30, 'injected_outage':3}
    def save(): Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    def compose(*parts):
        command = ['docker','compose','-f',args.compose,'-p',args.project,*parts]
        done = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
            env={**os.environ, 'R14_PROJECT':args.project, 'R14_MILVUS_PORT':str(urlsplit(args.endpoint).port),
                 'R14_HEALTH_PORT':args.health_port})
        report['commands'].append({'argv':command, 'exit_code':done.returncode, 'stdout':done.stdout, 'stderr':done.stderr})
        save(); done.check_returncode()
    assert 'site-packages' in Path(agentic_rag.__file__).parts and sys.flags.isolated
    config = H['configuration'](root/'data', args.endpoint)
    data = config.model_dump(mode='json')
    data['storage']['namespace'] = 'r16_acceptance'
    config = KnowledgeConfig.model_validate_json(json.dumps(data))
    catalog = Catalog(root/'data')
    worker = WorkerExecutionConfig(executable=args.cuda_python, model_cache=args.model_cache,
        runtime_dir=str(root/'worker'), idle_timeout_ms=1000)
    provider = LocalModelClient(worker)
    backend = MilvusRevisionIndex(config.storage, catalog, timeout=30)
    kb = catalog.create_library('R16 owned real model switch').kb_id
    old = None
    def runtime(frozen):
        return FrozenTokenizer(frozen.embedding, args.model_cache), provider, backend
    def target_of(original, instruction):
        data = original.model_dump(mode='json'); data['model_profiles'][0]['instruction'] = instruction
        return KnowledgeConfig.model_validate_json(json.dumps(data))
    def choose(plan, target, choice, **kwargs):
        return apply_model_switch(catalog, kb, plan['proposal_id'], target, choice=choice,
            runtime_factory=runtime, request_seconds=120, index_timeout=90, **kwargs)
    def wait_for_original(receipt):
        # Container health precedes QueryNode channel recovery after a real stop.
        # These are infrastructure probes; the product query below still runs once.
        artifact = publication.artifact(catalog, UUID(receipt['revision_id']), published=True)
        check = {'collection':artifact['collection_name'], 'limit_seconds':180,
                 'rpc_timeout_seconds':5, 'observations':[]}
        report.setdefault('service_readiness', []).append(check)
        started = time.monotonic(); end = started + check['limit_seconds']
        while True:
            sample = {'elapsed_seconds':time.monotonic()-started}
            ready = False
            try:
                sample['server'] = backend.client.get_server_version(timeout=min(5, max(.001, end-time.monotonic())))
                state = backend.client.get_load_state(artifact['collection_name'],
                    timeout=min(5, max(.001, end-time.monotonic())))
                sample['load_state'] = str(state['state'])
                if sample['load_state'] == 'Loaded':
                    sample['query'] = backend.client.query(artifact['collection_name'],
                        filter='', output_fields=['count(*)'], consistency_level='Strong',
                        timeout=min(5, max(.001, end-time.monotonic())))
                    ready = sample['query'][0]['count(*)'] > 0
            except Exception as exc:
                sample['error'] = {'type':type(exc).__name__, 'message':str(exc)}
            check['observations'].append(sample); save()
            if ready:
                check['ready_after_seconds'] = time.monotonic()-started; save(); return
            if time.monotonic() >= end:
                raise TimeoutError('owned Milvus original collection did not recover within 180 seconds')
            time.sleep(min(1, max(0, end-time.monotonic())))
    try:
        paths = [root/'telescope.md', root/'ocean.md']
        texts = ['# Telescope\nThe archived telescope certificate remains blue.\n',
                 '# Ocean\nThe archived ocean certificate remains green.\n']
        for path,text in zip(paths,texts): path.write_text(text, encoding='utf-8')
        with begin_changes(catalog, kb, ProcessingSnapshot.capture(uuid4(), config),
                           tuple(InputSelection(path=str(p)) for p in paths)) as owner:
            first = build_changes(catalog, owner, provider, backend, runtime(config)[0])
        report['initial'] = first; report['worker'] = provider.metadata; save()
        target = target_of(config, 'Retrieve scientific passages with evidence relevant to the question')
        old = catalog.start_current_run(kb, target, 'qa')
        assert old.run.resolved_config.knowledge.embedding.identity == config.embedding.identity
        proposal = inspect_model_switch(catalog, kb, target)
        assert proposal['state'] == 'WAITING_CONFIRMATION'
        assert choose(proposal, target, None)['state'] == 'WAITING_CONFIRMATION'
        for path in paths: path.unlink()
        switched = choose(proposal, target, 'confirm')
        report['success'] = switched; save()
        assert switched['state'] == 'PUBLISHED', switched
        assert switched['build']['metrics']['documents'] == len(paths)
        old_query = DenseSearch(catalog, old.run.run_id, provider, backend).search('telescope certificate')
        assert old_query['revision_id'] == first['receipt']['revision_id']
        assert old_query['profile_fingerprint'] == config.embedding.identity
        with catalog.start_current_run(kb, target, 'qa') as lease:
            new_query = DenseSearch(catalog, lease.run.run_id, provider, backend).search('telescope certificate')
        assert new_query['revision_id'] == switched['receipt']['revision_id']
        assert new_query['profile_fingerprint'] == target.embedding.identity
        report['first_old_read_after_switch'] = old_query
        report['new_read_after_switch'] = new_query
        report['deleted_originals'] = {str(path):not path.exists() for path in paths}
        old_artifact = publication.artifact(catalog, old.run.revision_id, published=True)
        assert backend.client.has_collection(old_artifact['collection_name'])
        old.close(); old = None
        end = time.monotonic()+30
        while True:
            catalog.maintain_indexes(limit=4, backend=backend)
            if not backend.client.has_collection(old_artifact['collection_name']): break
            if time.monotonic() > end: raise TimeoutError('old revision was not reclaimed after its last pin')
            time.sleep(.1)
        report['old_index_gc_after_run'] = old_artifact['collection_name']; save()
        # A real SDK create fails while only this project's standalone is down.
        target2 = target_of(target, 'Retrieve the archival passage that supports the requested certificate')
        failed_plan = inspect_model_switch(catalog, kb, target2)
        def stop_before_create(stage, value):
            if stage == 'prepared':
                backend.timeout = 3
                compose('stop','standalone')
        def fail(plan, desired, choice, actual, receipt):
            try:
                failed = choose(plan, desired, choice, observer=stop_before_create)
            finally:
                compose('up','-d','--wait','--wait-timeout','180')
                backend.timeout = 30
            assert failed['state'] == 'WAITING_RECOVERY' and failed['errors'], failed
            report.setdefault('fault_attempts', []).append(failed); save()
            assert catalog.get_library(kb).current_revision_id == UUID(receipt['revision_id'])
            wait_for_original(receipt)
            with catalog.start_current_run(kb, desired, 'qa') as lease:
                query = DenseSearch(catalog, lease.run.run_id, provider, backend).search('ocean certificate')
            assert query['profile_fingerprint'] == actual.embedding.identity
            report.setdefault('old_queries_after_failure', []).append(query); save()
            return failed
        first_failure = fail(failed_plan, target2, 'confirm', target, switched['receipt'])
        report['retry_failure'] = first_failure; save()
        retried = choose(failed_plan, target2, 'retry')
        report['retry_success'] = retried; save()
        assert retried['state'] == 'PUBLISHED' and retried['batch_id'] == first_failure['batch_id']
        assert retried['attempts'] == 2 and retried['build']['metrics']['model_calls'] == []
        target3 = target_of(target2, 'Retrieve passages establishing the exact certificate and its archived source')
        failed_plan = inspect_model_switch(catalog, kb, target3)
        failures = []
        for choice in ('confirm','retry'):
            failures.append(fail(failed_plan, target3, choice, target2, retried['receipt']))
            report['failures'] = failures; save()
        assert failures[0]['batch_id'] == failures[1]['batch_id'] and failures[1]['attempts'] == 2
        kept = choose(failed_plan, target3, 'keep_original')
        assert kept['state'] == 'KEPT_ORIGINAL'
        reopened = Catalog(root/'data')
        state = inspect_model_switch(reopened, kb, target3)
        assert state['state'] == 'KEPT_ORIGINAL' and state['proposal_id'] == kept['proposal_id']
        assert state['actual']['embedding']['instruction'] == target2.embedding.instruction
        assert reopened.get_library(kb).pending_mutation_id is None
        report['kept_after_restart'] = state
        report['status'] = 'PASS'
    except BaseException as exc:
        report.update(status='FAIL', error=repr(exc), error_message=str(exc)); raise
    finally:
        if old is not None: old.close()
        provider.close(); backend.close()
        report['finished'] = time.time(); save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root','cuda-python','model-cache','endpoint','health-port','report','compose','project'):
        parser.add_argument('--'+name, required=True)
    main(parser.parse_args())
