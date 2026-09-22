"""Read-only workspace audit producing explicit R12 handoff manifests.

No Git index, user data, process, service or environment is changed. The two
output JSON files are formal acceptance records, excluded from their own hashes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import zipfile


def main(args):
    repo=Path(args.repo).resolve();records=repo/'deployment/AgenticRAG/docs/implementation-records'
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    baseline=json.loads((records/'R12-baseline.json').read_text(encoding='utf-8'))
    original=json.loads((records/'R00-protected-inputs.json').read_text(encoding='utf-8'))
    current_overrides={v['path']:v['current_sha256'] for v in baseline['authorized_current_differences']}
    drift=[];authorized_r12=[]
    for entry in original['entries']:
        path=entry['path'];expected=current_overrides.get(path,entry['sha256'])
        actual=digest(repo/path)
        if actual!=expected:
            item={'path':path,'expected':expected,'actual':actual}
            if path=='deployment/AgenticRAG/README.md':
                item['authorization']='R12-dispatch.md independent README development integration documentation'
                authorized_r12.append(item)
            else:drift.append(item)
    def git(*argv):
        return subprocess.check_output(['git','-c','core.safecrlf=false',*argv],cwd=repo,text=True,encoding='utf-8').strip()
    head=git('rev-parse','HEAD');index=git('diff','--cached','--name-only')
    assert head==baseline['base'] and not index and not drift,(head,index,drift)
    package=json.loads(Path(args.package_report).read_text(encoding='utf-8'))
    stale=[p for p,h in package['source_sha256'].items() if digest(repo/p)!=h]
    assert not stale,stale
    package_root=Path(package['installs']['wheel']['python']).parents[2]
    installed={}
    for label,environment,route,names in (
        ('wheel',package_root/'env-wheel','direct',('host','rag')),
        ('sdist',package_root/'env-sdist','rebuilt',('host','rag')),
        ('cuda',package_root.parent/'cuda','direct',('rag',)),
    ):
        actual_files={}
        for name in names:
            archive=next((package_root/route/name).glob('*.whl'))
            with zipfile.ZipFile(archive) as wheel:
                for member in wheel.namelist():
                    if not member.startswith(('codeplus/','agentic_rag/')) or member.endswith('/'):continue
                    expected=hashlib.sha256(wheel.read(member)).hexdigest()
                    actual=environment/'Lib/site-packages'/member
                    assert digest(actual)==expected,(label,str(actual))
                    source=repo/member if member.startswith('codeplus/') else repo/'deployment/AgenticRAG/src'/member
                    assert digest(source)==expected,(label,str(source))
                    actual_files[member]={'path':str(actual),'sha256':expected,'source':str(source.relative_to(repo)).replace('\\','/')}
        installed[label]={'environment':str(environment),'files':actual_files,'all_installed_equal_wheel_and_source':True}
    real_checks={}
    for action in ('cli','tui','completion','cancel'):
        report_path=records/f'R12-installed-{action}-candidate5.json'
        live=json.loads(report_path.read_text(encoding='utf-8'));run=live['runs'][-1]
        assert live['status']=='PASS' and run['pin']['state']=='released'
        route='sdist' if action=='tui' else 'wheel'
        for module,path in live['installed_modules'].items():
            assert Path(path)==package_root/f'env-{route}'/'Lib/site-packages'/module/'__init__.py'
        if action=='cancel':
            assert live['cancel_propagated'] and live['pin_after_consumer_cancel']['state']=='active'
            assert run['run']['status']=='cancelled' and not run['host']['artifact']
            assert live['worker_finish']['execution_finished'] and live['worker_finish']['completion_source']=='worker_finished'
            delivered_open=[]
        else:
            assert run['run']['status']=='completed' and run['host']['artifact']
            opened={r[0] for r in run['tool_calls'] if r[1]=='knowledge_open' and r[2]=='ok'}
            delivered={m['tool_call_id'] for r in run['delivery_receipts'] if r['status']=='confirmed' for m in r['payload']['mappings']}
            delivered_open=sorted(opened & delivered);assert delivered_open
            answer=run['host']['artifact']['markdown']
            if action=='cli':
                results=[e for e in live['parsed_events'] if e.get('type')=='result']
                assert len(results)==1 and results[0]['result']==answer and results[0]['status']=='completed'
                assert live['exit_code']==0 and not live['stderr']
            elif action=='tui':
                assert answer in live['rendered_markdown'] and live['selection_cleared'] and live['input_enabled']
                assert live['ordinary_agent_policy_after'] is None
            else:
                assert live['answer']==answer and all(h['finished'] and h['completion_source']=='worker_finished' for h in live['worker_handles'])
                for window in run['host']['detail']['window_transforms']:
                    request=next(r for r in run['requests'] if r['purpose']==window['purpose'])
                    receipt=next(r for r in run['delivery_receipts'] if r['id']==request['request_id'])
                    assert set(window['after_candidates'])=={m['candidate_id'] for m in receipt['payload']['mappings']}
        real_checks[action]={'report':str(report_path),'sha256':digest(report_path),'status':'PASS',
            'run_id':run['run']['run_id'],'installed_route':route,'successful_open_confirmed_body_call_ids':delivered_open,
            'artifact_and_entry_output_checked':action!='cancel','pin':'released','usage':run['run']['usage']}
    host={'.codeplus/config.yaml.example','codeplus/__main__.py','codeplus/agent.py','codeplus/app.py',
        'codeplus/client.py','codeplus/run_policy.py','codeplus/commands/handlers/__init__.py','codeplus/commands/handlers/knowledge.py',
        'codeplus/config.py','codeplus/context/manager.py','codeplus/conversation.py','codeplus/remote.py',
        'codeplus/serialization.py','codeplus/tools/base.py','codeplus/validator.py','tests/test_commands.py','tests/test_entrypoints.py'}
    paths=set(git('diff','--name-only',baseline['base']).splitlines()) | set(git('ls-files','--others','--exclude-standard').splitlines())
    excluded=set(baseline['leader_owned']) | {'deployment/AgenticRAG/docs/implementation-records/R12-delivery-manifest.json',
        'deployment/AgenticRAG/docs/implementation-records/R12-protection.json'}
    chosen=sorted(p for p in paths if p not in excluded and (p in host or p in {
        'deployment/AgenticRAG/README.md','deployment/AgenticRAG/.gitattributes','deployment/AgenticRAG/docs/codeplus-integration.md'}
        or p.startswith(('deployment/AgenticRAG/src/','deployment/AgenticRAG/tests/','deployment/AgenticRAG/docs/implementation-records/R12'))))
    files={p:{'sha256':digest(repo/p),'bytes':(repo/p).stat().st_size} for p in chosen}
    subprocess.run(['git','-c','core.safecrlf=false','diff','--check','--',*chosen],cwd=repo,check=True,capture_output=True)
    counts={};skipped=[]
    for path in sorted(records.glob('R12*.xml')):
        tree=ET.parse(path);suite=tree.getroot().find('testsuite')
        counts[path.name]={k:suite.get(k) for k in ('tests','failures','errors','skipped','time')}
        if path.name=='R12-full-b.xml':
            skipped=[{'test':n.get('classname')+'.'+n.get('name'),'reason':n.find('skipped').get('message')}
                     for n in tree.findall('.//testcase') if n.find('skipped') is not None]
    protection={'captured_at':datetime.now(timezone.utc).isoformat(),'base':head,'index_empty':True,
        'R00_protected_count':len(original['entries']),'authorized_pre_R12_differences':current_overrides,
        'authorized_R12_protected_changes':authorized_r12,
        'unexpected_protected_drift':drift,'final_package_report':str(Path(args.package_report).resolve()),
        'final_package_report_sha256':digest(Path(args.package_report)),'package_source_drift':stale,
        'installed_package_files':installed,
        'final_real_report_checks':real_checks,
        'input_changes':{p:{'baseline':h,'current':digest(repo/p)} for p,h in baseline['input_files'].items() if digest(repo/p)!=h},
        'permission_dialog_unchanged':digest(repo/'codeplus/permission_dialog.py')=='76edaa0d055d9c1a9dae4a3b3f4db8bd675785294b929088724fa0abc165bb90'}
    manifest={'captured_at':protection['captured_at'],'base':head,'branch':git('branch','--show-current'),
        'executor':'/root/r12_agent_integration','state':'DELIVERED_PENDING_LEADER_ACCEPTANCE_AND_INDEPENDENT_CLEANUP',
        'commands':{'argv':__import__('sys').argv,'cwd':str(Path.cwd())},'files':files,
        'separate_audit_outputs':['R12-delivery-manifest.json','R12-protection.json'],
        'leader_owned_excluded':baseline['leader_owned'],'pytest_results':counts,'full_b_skips':skipped,
        'final_package_source_sha256':package['source_sha256'],'stage_commit_push_performed':False}
    for name,value in [('R12-protection.json',protection),('R12-delivery-manifest.json',manifest)]:
        (records/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'files':len(files),'protected':len(original['entries']),'unexpected_drift':len(drift),
        'package_source_drift':len(stale),'index_empty':True}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',required=True);p.add_argument('--package-report',required=True)
    main(p.parse_args())
