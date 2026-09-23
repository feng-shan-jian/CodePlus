"""Build committed schema8, seed with actual GPU/Milvus, delete old source, upgrade."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tarfile
import time

from agentic_rag.config import WorkerExecutionConfig
from agentic_rag.models.identity import process_birth


def main(args):
    root = Path(args.root).resolve(); root.mkdir(parents=True)
    repo = Path(args.repo).resolve()
    report = {'status':'RUNNING', 'baseline':args.baseline, 'commands':[], 'started':time.time()}
    def save(): Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    def command(argv, cwd=root):
        done = subprocess.run(list(map(str, argv)), cwd=cwd, env={k:v for k,v in os.environ.items() if k != 'PYTHONPATH'},
                              capture_output=True, text=True, encoding='utf-8')
        report['commands'].append({'argv':list(map(str, argv)), 'cwd':str(cwd), 'exit_code':done.returncode,
                                   'stdout':done.stdout, 'stderr':done.stderr}); save()
        done.check_returncode(); return done.stdout
    try:
        command(['git', 'archive', '--format=tar', '--output', root/'baseline.tar', args.baseline, 'deployment/AgenticRAG'], cwd=repo)
        with tarfile.open(root/'baseline.tar') as archive: archive.extractall(root/'baseline', filter='data')
        command(['uv', 'build', root/'baseline/deployment/AgenticRAG', '--wheel', '--out-dir', root/'schema8-wheel',
                 '--python', sys.executable, '--no-build-isolation', '--no-create-gitignore'])
        command(['uv', 'venv', root/'env', '--python', sys.executable])
        py = root/'env/Scripts/python.exe'
        command(['uv', 'pip', 'sync', '--python', py, '--require-hashes', repo/'deployment/AgenticRAG/docs/implementation-records/R12-windows-integration-lock.txt'])
        old = next((root/'schema8-wheel').glob('*.whl'))
        command(['uv', 'pip', 'install', '--python', py, '--no-deps', old])
        command(['uv', 'pip', 'install', '--python', args.cuda_python, '--no-deps', '--reinstall', old])
        H = runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
        config = H['configuration'](root/'data', args.endpoint)
        worker = WorkerExecutionConfig(executable=args.cuda_python, model_cache=args.model_cache,
                                       runtime_dir=str(root/'worker'), idle_timeout_ms=10000)
        (root/'settings.json').write_text(json.dumps({'config':config.model_dump(mode='json'), 'worker':worker.model_dump(mode='json')}), encoding='utf-8')
        helper = Path(__file__).with_name('r15_upgrade_process.py').resolve()
        def child(mode): return json.loads(command([py, '-I', '-B', helper, '--root', root, '--mode', mode, '--model-cache', args.model_cache]))
        before = child('seed'); report['before'] = before
        assert before['schema'] == 8 and before['row_counts']['publications'] == 1
        assert before['row_counts']['artifact_ownership_proofs'] == 2 and before['row_counts']['saved_citations'] == 1
        observed = before['seed_worker']; end = time.monotonic()+60
        while process_birth(observed['pid']) == observed['process_birth']:
            if time.monotonic() > end: raise TimeoutError('old worker did not exit through configured idle lifetime')
            time.sleep(.1)
        report['old_worker_exited'] = {'pid':observed['pid'], 'birth':observed['process_birth'], 'observed':time.time(), 'reason':'normal idle exit'}
        shutil.copytree(root/'data', root/'rollback-data')
        old_source = (root/'baseline').resolve()
        assert old_source.parent == root and old_source.name == 'baseline' and not old_source.is_symlink()
        shutil.rmtree(old_source)
        report['old_source_deleted_before_upgrade'] = {'path':str(old_source), 'absent':not old_source.exists()}; save()
        command(['uv', 'pip', 'install', '--python', py, '--no-deps', '--reinstall', args.wheel])
        command(['uv', 'pip', 'install', '--python', args.cuda_python, '--no-deps', '--reinstall', args.wheel])
        fault = child('fault'); report['rollback'] = fault
        assert fault['schema'] == 8 and fault['row_hashes'] == before['row_hashes'] and fault['migrations'] == before['migrations']
        after = child('read'); report['after'] = after
        assert after['schema'] == 9 and after['migrations'][:8] == before['migrations']
        assert all(after['row_hashes'][key] == value for key,value in before['row_hashes'].items())
        assert all(after['triggers'][key] == value for key,value in before['triggers'].items())
        for key in ('citation','typed_run','archives_verified','publication_fk'):
            assert after[key] == before[key]
        assert after['integrity'] == [['ok']] and after['foreign_keys'] == []
        report['resumed'] = child('resume'); assert report['resumed']['status'] == 'PASS'
        report.update(status='PASS', old_wheel={'path':str(old), 'sha256':hashlib.sha256(old.read_bytes()).hexdigest()},
                      new_wheel={'path':args.wheel, 'sha256':hashlib.sha256(Path(args.wheel).read_bytes()).hexdigest()})
    except BaseException as exc:
        report.update(status='FAIL', error=repr(exc)); raise
    finally:
        report['finished'] = time.time(); save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('root','repo','baseline','wheel','cuda-python','model-cache','endpoint','report'):
        parser.add_argument('--'+key, required=True)
    main(parser.parse_args())
