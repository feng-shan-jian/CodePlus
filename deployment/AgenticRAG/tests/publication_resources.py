"""Owned R10 fixture resource inventory/restart. Never manages other projects."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import time

PROJECT='codeplus-r10-acceptance'
VOLUMES={PROJECT+'_'+name for name in ('etcd','minio','milvus')}


def command(*args):
    result=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
    if result.returncode:raise RuntimeError(result.stdout+result.stderr)
    return result.stdout.strip()


def _read_containers():
    # The former label=...!=... ps filter omitted expected foreign containers.
    # Enumerate every container (including stopped/unlabelled), then partition.
    ids=command('docker','ps','-aq','--no-trunc').splitlines()
    containers=json.loads(command('docker','inspect',*ids)) if ids else []
    if (len(ids)!=len(set(ids)) or len(containers)!=len(ids) or
            {c['Id'] for c in containers}!=set(ids)):
        raise RuntimeError('complete Docker container inventory differs from inspected IDs')
    return sorted(containers,key=lambda c:c['Id'])


def _summary(container):
    labels=container['Config'].get('Labels') or {}
    state=container['State']
    # Do not persist Config.Env, command arguments, auth or arbitrary labels.
    return {'id':container['Id'],'name':container['Name'].lstrip('/'),
        'image':container['Config']['Image'],'image_id':container['Image'],
        'project':labels.get('com.docker.compose.project'),
        'service':labels.get('com.docker.compose.service'),
        'status':state['Status'],'running':state['Running'],
        'started_at':state['StartedAt'],'finished_at':state['FinishedAt'],
        'restart_count':container['RestartCount']}


def container_inventory():
    """Read-only complete inventory, also usable when no R10 project is running."""
    return [_summary(c) for c in _read_containers()]


def inventory():
    all_containers=_read_containers()
    containers=[c for c in all_containers if (c['Config'].get('Labels') or {}).get('com.docker.compose.project')==PROJECT]
    by_service={c['Config']['Labels'].get('com.docker.compose.service'):c for c in containers}
    if len(containers)!=3 or set(by_service)!={'etcd','minio','standalone'}:
        raise RuntimeError('R10 service inventory differs')
    for c in containers:
        if c['Config']['Labels'].get('com.docker.compose.project')!=PROJECT:raise RuntimeError('foreign project')
        for m in c['Mounts']:
            if m['Type']!='volume' or m['Name'] not in VOLUMES:raise RuntimeError('unexpected mount ownership')
    port=by_service['standalone']['HostConfig']['PortBindings']
    if port!={'19530/tcp':[{'HostIp':'127.0.0.1','HostPort':'19532'}], '9091/tcp':[{'HostIp':'127.0.0.1','HostPort':'9093'}]}:raise RuntimeError('unexpected R10 ports')
    volumes=json.loads(command('docker','volume','inspect',*sorted(VOLUMES)))
    if any(v['Labels'].get('com.docker.compose.project')!=PROJECT for v in volumes):raise RuntimeError('foreign volume')
    owned_ids={c['Id'] for c in containers}
    other=[_summary(c) for c in all_containers if c['Id'] not in owned_ids]
    return {'at_utc':datetime.now(timezone.utc).isoformat(), 'project':PROJECT,'containers':{
        name:{'id':c['Id'],'name':c['Name'],'image':c['Config']['Image'],'image_id':c['Image'],
              'started_at':c['State']['StartedAt'],'health':c['State'].get('Health',{}).get('Status'),
              'mounts':c['Mounts'],'ports':c['HostConfig']['PortBindings']} for name,c in by_service.items()},
        'volumes':volumes,
        'inventory_method':'unfiltered docker ps -aq --no-trunc; inspect all IDs; exact project label partition in Python',
        'container_inventory':[_summary(c) for c in all_containers],
        'non_owned_containers':[f"{c['id'][:12]} {c['name']} {c['image']}" for c in other],
        'non_owned_container_details':other}


def measure():
    result=inventory()
    result['volume_allocated_kib']={v:int(command('docker','run','--rm','--network','none','--mount',
        f'type=volume,source={v},target=/measure,readonly','alpine:3.22','du','-sk','/measure').split()[0]) for v in sorted(VOLUMES)}
    result['space_scope']='whole owned service volumes including WAL/metadata/tombstones/background work; not per-collection net size'
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['measure','restart']);parser.add_argument('report');args=parser.parse_args()
    result=measure()
    if args.action=='restart':
        start=time.perf_counter();identity=result['containers']['standalone']['id']
        command('docker','restart',identity)
        deadline=time.monotonic()+120
        while True:
            after=inventory()
            if after['containers']['standalone']['health']=='healthy':break
            if time.monotonic()>deadline:raise TimeoutError('R10 restart health deadline')
            time.sleep(1)
        assert after['containers']['standalone']['started_at']!=result['containers']['standalone']['started_at']
        result.update(after_health=after,health_seconds=time.perf_counter()-start)
    Path(args.report).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
