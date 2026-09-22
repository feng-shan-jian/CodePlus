"""Inventory contract tests use explicit Docker transport fixtures, never Docker IO."""
import json
from pathlib import Path
import runpy

import pytest

PATH=Path(__file__).with_name('publication_resources.py')


def module():
    # Each case gets isolated globals for transport replacement.
    return runpy.run_path(str(PATH))


def fixture_container(identity,name,project=None,service=None,running=True):
    labels={'com.docker.compose.project':project,'com.docker.compose.service':service} if project is not None else None
    return {'Id':identity*64,'Name':'/'+name,'Image':'sha256:'+identity*64,
        'Config':{'Image':'example/'+name+':test','Labels':labels,'Env':['SECRET=must-not-record'],
                  'Cmd':['--token','must-not-record']},
        'State':{'Status':'running' if running else 'exited','Running':running,
            'StartedAt':'2026-09-22T00:00:00Z','FinishedAt':'0001-01-01T00:00:00Z',
            'Health':{'Status':'healthy'}},'RestartCount':0,'Mounts':[],
        'HostConfig':{'PortBindings':{}}}


def transport(monkeypatch,namespace,containers):
    calls=[]
    def command(*args):
        calls.append(args)
        if args==('docker','ps','-aq','--no-trunc'):
            return '\n'.join(c['Id'] for c in containers)
        if args[:2]==('docker','inspect'):
            assert args[2:]==tuple(c['Id'] for c in containers)
            return json.dumps(containers)
        if args[:3]==('docker','volume','inspect'):
            return json.dumps([{'Name':v,'Labels':{'com.docker.compose.project':namespace['PROJECT']}} for v in sorted(namespace['VOLUMES'])])
        raise AssertionError('unexpected Docker command: '+repr(args))
    monkeypatch.setitem(namespace['inventory'].__globals__,'command',command)
    return calls


def test_complete_inventory_includes_unlabelled_other_project_and_stopped(monkeypatch):
    namespace=module();project=namespace['PROJECT']
    owned=[fixture_container(str(i),name,project,name) for i,name in enumerate(('etcd','minio','standalone'),1)]
    owned[-1]['HostConfig']['PortBindings']={'19530/tcp':[{'HostIp':'127.0.0.1','HostPort':'19532'}],
        '9091/tcp':[{'HostIp':'127.0.0.1','HostPort':'9093'}]}
    foreign=[fixture_container('4','unlabelled'),fixture_container('5','other-project','user-project','db'),
        fixture_container('6','stopped-unlabelled',running=False)]
    calls=transport(monkeypatch,namespace,[*owned,*foreign])
    observed=namespace['inventory']()
    assert [c['id'] for c in observed['non_owned_container_details']]==[c['Id'] for c in foreign]
    assert observed['non_owned_containers']==[f"{c['Id'][:12]} {c['Name'][1:]} {c['Config']['Image']}" for c in foreign]
    assert len(observed['container_inventory'])==6
    assert observed['non_owned_container_details'][-1]['status']=='exited'
    assert 'must-not-record' not in json.dumps(observed)
    assert not any('--filter' in call for call in calls)


def test_inventory_without_owned_project_still_returns_all_containers(monkeypatch):
    namespace=module();foreign=[fixture_container('4','unlabelled'),fixture_container('5','other','user-project')]
    transport(monkeypatch,namespace,foreign)
    assert [c['id'] for c in namespace['container_inventory']()]==[c['Id'] for c in foreign]
    with pytest.raises(RuntimeError,match='R10 service inventory differs'):namespace['inventory']()


def test_disappearing_or_partial_inspect_is_not_empty_success(monkeypatch):
    namespace=module()
    def command(*args):
        if args[:2]==('docker','ps'):return '4'*64
        return '[]'
    monkeypatch.setitem(namespace['inventory'].__globals__,'command',command)
    with pytest.raises(RuntimeError,match='complete Docker container inventory differs'):
        namespace['container_inventory']()


def test_duplicate_owned_service_fails_closed(monkeypatch):
    namespace=module();project=namespace['PROJECT']
    owned=[fixture_container(str(i),name,project,name) for i,name in enumerate(('etcd','minio','standalone','standalone'),1)]
    transport(monkeypatch,namespace,owned)
    with pytest.raises(RuntimeError,match='R10 service inventory differs'):namespace['inventory']()
