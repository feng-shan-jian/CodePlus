"""Opt-in actual installed CUDA RPC load; no answer-model or retrieval claims."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import runpy
import subprocess
import sys
import threading
import time

from agentic_rag.config import WorkerExecutionConfig
from agentic_rag.models.identity import process_birth

H=runpy.run_path(str(Path(__file__).with_name('scheduling_support.py')))


class Host:
    def __init__(self, root, config, index):
        self.stderr=(root/f'host-{index}.stderr').open('w',encoding='utf-8')
        self.argv=[sys.executable,'-I','-B',str(Path(__file__).with_name('scheduling_process_helper.py')),
                   str(config),str(root/f'host-{index}.jsonl')]
        self.process=subprocess.Popen(self.argv,cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=self.stderr,text=True,encoding='utf-8')
        self.lines=queue.Queue()
        def read():
            for line in self.process.stdout:self.lines.put(line)
            self.lines.put(None)
        self.reader=threading.Thread(target=read);self.reader.start()
        self.ready=self.receive()

    def receive(self):
        line=self.lines.get(timeout=150)
        assert line is not None,f'host exited {self.process.poll()}'
        return json.loads(line)

    def ask(self, command):
        self.process.stdin.write(json.dumps(command)+'\n');self.process.stdin.flush()
        return self.receive()

    def close(self):
        metadata=None
        try:
            if self.process.poll() is None:
                response=self.ask({'op':'close'});assert response['closed'];metadata=response['metadata']
            assert self.process.wait(30)==0
            self.reader.join(5)
        finally:self.stderr.close()
        return metadata


def metric(values):
    ordered=sorted(values)
    return {'count':len(values),'min':ordered[0],'p50':ordered[math.ceil(len(values)*.5)-1],
            'p95':ordered[math.ceil(len(values)*.95)-1],'max':ordered[-1],'mean':sum(values)/len(values)}


def analyze(rows, submitted):
    def event(row, kind):
        return next(x['observed_ns'] for x in row['events'] if x['type']==kind or x.get('phase')==kind)
    ordered=sorted(rows,key=lambda r:event(r,'validation'))
    assert len(rows)==60 and all(r['status']=='completed' and r['execution_finished'] for r in rows)
    assert len({r['worker']['instance_id'] for r in rows})==1
    assert len({r['model_status']['model_instance_id'] for r in rows})==1
    assert {r['model_status']['load_count'] for r in rows}=={1}
    assert len({r['profile_fingerprint'] for r in rows})==1
    expected={x['id']:x for x in submitted}
    participants={}
    for group in ('front','back'):
        selected=[r for r in ordered if expected[r['id']]['group']==group]
        assert [r['id'] for r in selected]==[
            r['id'] for r in submitted if r['group']==group]
        owners={r['owner_id'] for r in selected}
        hosts={event['host_pid'] for r in selected for event in r['events']}
        assert len(owners)==len(hosts)==2
        if group=='back':assert all(r['input_tokens']==[2048,2048] for r in selected)
        participants[group]={'owner_ids':sorted(owners),'host_pids':sorted(hosts),
            'actual_input_token_shapes':sorted({tuple(r['input_tokens']) for r in selected})}
    # Count only a background backlog observed before the preceding batch's
    # completion; it was therefore admitted before the next scheduling choice.
    checked=[];streak=0;seen=set();previous=None
    for row in ordered:
        group=expected[row['id']]['group']
        known_back=[r['id'] for r in rows if expected[r['id']]['group']=='back' and r['id'] not in seen
            and previous is not None and event(r,'queued') < event(previous,'finished')-2_000_000]
        if group=='front' and known_back:
            checked.append({'request_id':row['request_id'],'foreground_streak_before':streak,'known_back':known_back})
            assert streak < 4,checked[-1]
        streak=streak+1 if group=='front' else 0
        seen.add(row['id']);previous=row
    assert len(checked)>=16,len(checked)
    groups={}
    for group in ('front','back'):
        selected=[r for r in rows if expected[r['id']]['group']==group]
        groups[group]={'requests':len(selected),
            'observed_queue_to_validation_ms':metric([(event(r,'validation')-event(r,'queued'))/1e6 for r in selected]),
            'submit_to_validation_ms':metric([(event(r,'validation')-event(r,'submitted'))/1e6 for r in selected]),
            'submit_to_finished_ms':metric([(event(r,'finished')-event(r,'submitted'))/1e6 for r in selected]),
            'reported_queue_and_validation_ms':metric([r['timings']['queue_ms'] for r in selected]),
            'load_ms':metric([r['timings']['load_ms'] for r in selected]),
            'inference_ms':metric([r['timings']['inference_ms'] for r in selected]),
            'peak_allocated_mib':max(r['model_status']['peak_allocated_mib'] for r in selected)}
    first=min(event(r,'submitted') for r in rows);last=max(event(r,'finished') for r in rows)
    background=[r for r in ordered if expected[r['id']]['group']=='back']
    groups['elapsed_ms']=(last-first)/1e6
    groups['background_documents_per_second']=24/((last-first)/1e9)
    groups['background_completion_gap_ms']=metric([(event(b,'finished')-event(a,'finished'))/1e6
                                                   for a,b in zip(background,background[1:])])
    return {'metrics':groups,'fifo_both_classes':True,'quota_checked_choices':checked,
        'actual_participants_and_tokens':participants,'profile_fingerprint':rows[0]['profile_fingerprint'],
        'execution_order':[r['id'] for r in ordered],'instance':rows[0]['worker'],
        'model_instance_id':rows[0]['model_status']['model_instance_id'],
        'clock_boundary':'host receive timestamps; queued-to-validation excludes input validation but includes phase delivery jitter',
        'quota':'at most four foreground batches when a background backlog is already observed; same-class FIFO',
        'inputs':'background complete 2 x 2048 tokens; foreground short query; 48 front and 12 back requests'}


def main(args):
    assert os.environ.get('R22_REAL')=='1','explicit actual GPU opt-in required'
    root=Path(args.root);root.mkdir(parents=True,exist_ok=False)
    report={'status':'RUNNING','started':time.time(),'scope':'actual CUDA RPC stress only; no Agent/report or semantic quality claim',
            'submissions':[],'results':[],'hosts':[]}
    config=WorkerExecutionConfig(executable=args.cuda_python,model_cache=args.model_cache,
        runtime_dir=str(root/'worker'),idle_timeout_ms=500)
    H['write'](root/'worker-config.json',config.model_dump(mode='json'))
    hosts=[];metadata=None
    try:
        for index in range(2):hosts.append(Host(root,root/'worker-config.json',index))
        report['hosts']=[{'ready':h.ready,'argv':h.argv} for h in hosts]
        warm=[]
        for index,host in enumerate(hosts):
            name='warm'+str(index)
            host.ask({'op':'submit','id':name,'purpose':'qa'})
            warm.append(host.ask({'op':'wait','id':name}))
        report['warmup']=warm
        assert warm[0]['worker']['instance_id']==warm[1]['worker']['instance_id']
        assert warm[0]['model_status']['model_instance_id']==warm[1]['model_status']['model_instance_id']
        assert {r['model_status']['load_count'] for r in warm}=={1}
        totals={'front':48,'back':12};counts={'front':0,'back':0};pending={}
        def submit(group, phase=None):
            number=counts[group];name=group+str(number).zfill(3)
            owner=len(report['submissions'])%2
            purpose=('qa','report')[number%2] if group=='front' else ('import','rebuild')[number%2]
            started=time.monotonic_ns()
            ack=hosts[owner].ask({'op':'submit','id':name,'purpose':purpose,'long':group=='back','wait_phase':phase})
            row={'id':name,'group':group,'host_index':owner,'dispatch_ns':started,
                 'ack_ns':time.monotonic_ns(),'request_id':ack['request_id']}
            report['submissions'].append(row);pending[name]=group;counts[group]+=1
        submit('back','inference_started')
        for _ in range(2):submit('back')
        for _ in range(8):submit('front')
        while pending or any(counts[g]<totals[g] for g in totals):
            for host in hosts:
                for row in host.ask({'op':'drain'})['completed']:
                    assert row['status']=='completed',row
                    assert row['id'] in pending
                    pending.pop(row['id']);report['results'].append(row)
            for group,target in (('front',8),('back',3)):
                while counts[group]<totals[group] and sum(g==group for g in pending.values())<target:
                    submit(group)
            H['write'](root/'rpc.json',report)
            if pending:time.sleep(.005)
        report['analysis']=analyze(report['results'],report['submissions'])
        report['status']='PASS'
    except BaseException as exc:
        report.update(status='FAIL',error=repr(exc));raise
    finally:
        for host in hosts:
            current=host.close()
            metadata=current or metadata
        if metadata:
            end=time.monotonic()+30
            while process_birth(metadata['pid'])==metadata['process_birth'] and time.monotonic()<end:time.sleep(.03)
            assert process_birth(metadata['pid'])!=metadata['process_birth']
            report['worker_idle_exit']={k:metadata[k] for k in ('pid','process_birth','instance_id')}
        report['host_exit_codes']=[h.process.returncode for h in hosts]
        report['finished']=time.time()
        H['write'](root/'rpc.json',report)
    print(json.dumps({'status':report['status'],'analysis':report['analysis']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--cuda-python',required=True)
    p.add_argument('--model-cache',required=True);main(p.parse_args())
