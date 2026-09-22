"""Read-only ownership inventory for independent R12 cleanup, never a cleaner."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from agentic_rag.models.identity import process_birth


def main(args):
    root=Path(args.root).resolve();repo=Path(args.repo).resolve()
    assert root.name=='codeplus-r12-executor-20260922'
    records=repo/'deployment/AgenticRAG/docs/implementation-records'
    def command(argv):
        result=subprocess.run(argv,capture_output=True,text=True,encoding='utf-8',check=True)
        return result.stdout.strip()
    workers={}
    for path in sorted(records.glob('R12*.json')):
        if path.name in {'R12-resources.json','R12-delivery-manifest.json','R12-protection.json'}:continue
        record=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(record,dict):continue
        worker=record.get('worker_identity')
        if not worker:continue
        key=str(worker['pid'])+':'+str(worker['process_birth'])
        actual=process_birth(worker['pid'])
        workers[key]={'pid':worker['pid'],'process_birth':worker['process_birth'],'runtime_dir':worker.get('runtime_dir'),
            'identity':worker.get('identity'),'actual_device':worker.get('actual_device'),
            'same_process_alive':str(actual)==str(worker['process_birth']) if actual is not None else False,
            'current_pid_birth':actual,'evidence_file':path.name,'handles':record.get('worker_handles',record.get('worker_finish'))}
    project='codeplus-r12-acceptance'
    containers=[json.loads(v) for v in command(['docker','ps','-a','--filter','label=com.docker.compose.project='+project,'--format','{{json .}}']).splitlines()]
    volumes=command(['docker','volume','ls','--filter','label=com.docker.compose.project='+project,'--format','{{.Name}}']).splitlines()
    networks=command(['docker','network','ls','--filter','label=com.docker.compose.project='+project,'--format','{{.Name}}']).splitlines()
    script="Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine | ConvertTo-Json -Depth 3 -Compress"
    processes=json.loads(command(['pwsh','-NoProfile','-Command',script]))
    owned=[p for p in processes if str(root).lower() in (p.get('CommandLine') or '').replace('/', '\\').lower()
           and p['ProcessId']!=__import__('os').getpid() and 'r12_resource_inventory' not in (p.get('CommandLine') or '')]
    default_temp=root.parent/'pytest-of-18221'
    defaults=[]
    for name in ('pytest-17','pytest-18','pytest-19'):
        path=default_temp/name
        if path.exists():defaults.append({'path':str(path),'attribution':'exclusive R12 writer interval; pre-basetemp R12 test runs',
            'children':[p.name for p in sorted(path.iterdir())],'created_ns':path.stat().st_ctime_ns})
    report={'captured_at':datetime.now(timezone.utc).isoformat(),'executor':'/root/r12_agent_integration',
        'root':str(root),'argv':sys.argv,'cwd':str(Path.cwd()),'root_children':[p.name for p in sorted(root.iterdir())],
        'root_ownership':'Created exclusively for R12; all children may be removed only after Leader acceptance and independent cleanup authorization',
        'workers':list(workers.values()),'own_processes_at_capture':owned,
        'compose':{'project':project,'file':str(root/'compose.yaml'),'sha256':hashlib.sha256((root/'compose.yaml').read_bytes()).hexdigest(),
            'containers':containers,'volumes':volumes,'networks':networks,'ports':[19534,9095]},
        'default_pytest_owned':defaults,'default_pytest_current_link':str(default_temp/'pytest-current'),
        'protected_nonowned':['C:/Users/18221/.cache/codeplus-agenticrag/models',str(default_temp/'pytest-2'),
            'All other default Temp paths','Existing SonarQube/Postgres containers, volumes and network','Shared worker coordination directory and unrelated active workers'],
        'removed_owned_scratch':['fix_agent_cleanup.py','fix_client_names.py','rewrite_agent.py','rewrite_client.py','rewrite_entrypoints.py'],
        'cleanup_status':'PENDING_SEPARATE_CLEANUP_SESSION','environments_and_services_retained_for_independent_acceptance':True}
    (records/'R12-resources.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps({'containers':len(containers),'volumes':len(volumes),'workers':len(workers),
        'same_process_workers_alive':sum(v['same_process_alive'] for v in workers.values()),'own_processes':len(owned)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);parser.add_argument('--repo',required=True)
    main(parser.parse_args())
