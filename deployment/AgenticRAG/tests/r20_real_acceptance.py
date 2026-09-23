"""Opt-in R20 installed-host acceptance with real GPU, Milvus and answer API.

The small English fixture tests constraints and source lifecycle, not R23 quality.
No response or query is synthesized by this driver. Provider secrets stay in memory.
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
from uuid import UUID, uuid4

import agentic_rag
import codeplus
from agentic_rag.adapters.codeplus.policy import DevelopmentConfig, load_policy
from agentic_rag.citations import open_citation
from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, WorkerExecutionConfig
from agentic_rag.ingestion import InputSelection, begin_changes, build_changes
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models import FrozenTokenizer, LocalModelClient
from agentic_rag.storage import Catalog

H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
HOST=runpy.run_path(str(Path(__file__).with_name('r12_real_acceptance.py')))
QUESTION=('请仅比较2024年 Atlas 与 Beacon 望远镜的安全认证、登记存放地点以及夜间作业资格，'
          '不要把2023年的记录纳入2024年结论。用中文写结构化 Markdown 研究报告，说明结论、比较、分歧、推断与局限。'
          '针对英文资料生成英文检索 query，并打开搜索返回的 source_ref 核对原文。'
          '直接引文保留英文；引用偏移不确定时引用整个返回正文并使用其 returned_spans，不猜测偏移。')
SOURCES={
    'atlas.md':'# Atlas final register\nIn 2024, the Atlas telescope blue safety certificate was approved and archived in the ocean registry. '
               'Atlas was not certified for night operation in 2024.\n',
    'beacon.md':'# Beacon final register\nIn 2024, the Beacon telescope red safety certificate was approved and archived in the hill registry. '
                'Beacon was certified for night operation in 2024.\n',
    'audit.md':'# Atlas audit\nThe preliminary 2024 register listed Atlas night operation as approved. '
               'The final 2024 audit corrected that entry: Atlas was not certified for night operation in 2024.\n',
    'old.md':'# Previous year\nIn 2023, Atlas had a green draft certificate in the river registry. '
             'This entry does not describe its 2024 certification.\n'}


def write(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def change(root,settings,paths=(),deleted=()):
    catalog=Catalog(settings.knowledge.storage.data_dir)
    state=json.loads((root/'state.json').read_text(encoding='utf-8'))
    provider=LocalModelClient(settings.worker)
    backend=MilvusRevisionIndex(settings.knowledge.storage,catalog)
    try:
        with begin_changes(catalog,UUID(state['kb_id']),ProcessingSnapshot.capture(uuid4(),settings.knowledge),
                tuple(InputSelection(path=str(root/name)) for name in paths),delete_document_ids=tuple(deleted)) as owner:
            return build_changes(catalog,owner,provider,backend,FrozenTokenizer(settings.knowledge.embedding,settings.worker.model_cache))
    finally:provider.close();backend.close()


def prepare(args,report):
    root=Path(args.root);root.mkdir(parents=True)
    data=H['configuration'](root/'data',args.endpoint).model_dump(mode='json')
    data['retrieval'].update(mode='auto',route='hybrid',rerank=True,rerank_candidates=24,context_chunks=24,context_tokens=50000)
    for kind,total,duration in [('qa',200000,300000),('report',400000,600000)]:
        data['budgets'][kind].update(searches=10,opens=10,total_tokens=total,duration_ms=duration,
                                   finish_reserve_tokens=34000,finish_reserve_ms=30000)
    config=KnowledgeConfig.model_validate_json(json.dumps(data))
    settings=DevelopmentConfig(knowledge=config,worker=WorkerExecutionConfig(executable=args.cuda_python,
        model_cache=args.model_cache,runtime_dir=str(root/'worker'),idle_timeout_ms=1000),
        answer_tokenizer=args.answer_tokenizer,explore_output_cap=4096,finish_input_upper=12000,
        finalize_output_cap=4096,repair_output_cap=4096,compact_output_cap=2048,
        max_iterations=12,max_tool_attempts=30,cleanup_grace_ms=1000)
    catalog=Catalog(root/'data');kb=catalog.create_library('R20 English constraint fixture').kb_id
    write(root/'state.json',{'kb_id':str(kb),'runs':[],'reports':[]})
    write(root/'development.json',settings.model_dump(mode='json'))
    for name,body in SOURCES.items():(root/name).write_text(body,encoding='utf-8')
    report['build']=change(root,settings,tuple(SOURCES))
    report['source_sha256']={name:sha(root/name) for name in SOURCES}


async def answer(root,settings,provider,state,request,target,parent,report):
    from contextlib import aclosing
    from codeplus.agent import Agent, StreamText
    from codeplus.client import create_client
    from codeplus.conversation import ConversationManager
    from codeplus.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
    from codeplus.tools import create_default_registry
    client=create_client(provider)
    policy=load_policy(str(root/'development.json'),state['kb_id'],provider,task_kind='report' if target else 'qa',
                       report_path=str(target) if target else None,parent_run_id=parent)
    start=policy.start
    async def observed_start(context):
        scope=await start(context)
        model_control=scope.model_control
        def observed_control(purpose):
            control=model_control(purpose)
            before_send=control.before_send
            async def observed_send(request):
                body=json.loads(request.raw_body)
                report.setdefault('request_contracts',[]).append({'purpose':purpose,
                    'request_id':request.request_id,'body_sha256':hashlib.sha256(request.raw_body).hexdigest(),
                    'model':body.get('model'),'response_format':body.get('response_format'),
                    'tool_count':len(body.get('tools',[]))})
                return await before_send(request)
            control.before_send=observed_send
            return control
        scope.model_control=observed_control
        assess=scope.assess_output
        async def observed_assess(text,stop_reason):
            # Only public answer text supplied to the real validator. No
            # thinking events or additional model requests are collected.
            draft={'run_id':scope.run_id,'purpose':scope.purpose,'stop_reason':stop_reason,
                   'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest()}
            report.setdefault('answer_system_prompt',scope.system_prompt)
            try:
                value=json.loads(text)
                draft.update(json_object=isinstance(value,dict),keys=list(value) if isinstance(value,dict) else [])
            except json.JSONDecodeError as error:
                draft.update(json_object=False,json_error=str(error))
            report.setdefault('public_drafts',[]).append(draft)
            decision=await assess(text,stop_reason)
            draft['decision']=decision.action
            return decision
        scope.assess_output=observed_assess
        return scope
    policy.start=observed_start
    checker=PermissionChecker(DangerousCommandDetector(),PathSandbox(str(root)),RuleEngine(),mode=PermissionMode.ACCEPT_EDITS)
    agent=Agent(client,create_default_registry(),provider.protocol,work_dir=str(root),execution_policy=policy,
                permission_checker=checker)
    try:
        if report.get('entrypoint','completion')=='stream':
            conversation=ConversationManager();conversation.add_user_message(request)
            pieces=[]
            async with aclosing(agent.run(conversation)) as stream:
                async for event in stream:
                    if isinstance(event,StreamText):pieces.append(event.text)
            report['answer']=''.join(pieces)
        else:report['answer']=await agent.run_to_completion(request)
        report['outcome']=asdict(agent.last_run_outcome)
    finally:await client._client.close()


def live(args,report):
    from codeplus.config import load_config
    root=Path(args.root);state=json.loads((root/'state.json').read_text(encoding='utf-8'))
    settings=DevelopmentConfig.model_validate_json((root/'development.json').read_text(encoding='utf-8'))
    provider=next(p for p in load_config(Path(args.provider_config)).providers if p.protocol=='openai-compat'
        and p.model in {'deepseek-chat','deepseek-reasoner'}
        and p.base_url.rstrip('/') in {'https://api.deepseek.com','https://api.deepseek.com/v1'})
    report['provider']={'name':provider.name,'model':provider.model,'protocol':provider.protocol,'base_url':provider.base_url}
    parent=state['runs'][-1] if state['runs'] else None
    target=root/('report-'+args.action+'-'+uuid4().hex[:8]+'.md') if args.action!='delete' else None
    request=QUESTION if args.action=='report' else '继续研究，优先补查并核对 Atlas 与 Beacon 在2024年的夜间作业资格，区分历史待核线索和当前证据；用中文回答并保留英文原文引文。请搜索并打开返回的原文。'
    catalog=Catalog(settings.knowledge.storage.data_dir)
    if args.action=='update':
        (root/'atlas.md').write_text('# Atlas revised final register\nThe final 2024 Atlas certification was revised. '
            'Atlas is now certified for night operation in 2024; its blue safety certificate remains in the ocean registry.\n',encoding='utf-8')
        (root/'audit.md').write_text('# Atlas revised audit\nA later 2024 audit supersedes the earlier prohibition. '
            'Atlas is now certified for night operation in 2024.\n',encoding='utf-8')
        report['build']=change(root,settings,('atlas.md','audit.md'))
        request+=' 当前库已发布修订，请重新查证并明确标识发生变化的旧结论。'
    if args.action=='delete':
        with catalog._db.transaction() as db:
            rows=list(db.execute('SELECT d.document_id,d.original_name FROM documents d WHERE d.kb_id=?',(state['kb_id'],)))
        removed=[UUID(row[0]) for row in rows if row[1] in {'atlas.md','audit.md','old.md'}]
        report['removed_document_ids']=list(map(str,removed))
        report['build']=change(root,settings,deleted=removed)
        request+=' 用户已删除 Atlas 相关来源。请说明本轮还能核实什么、哪些历史发现无法在当前资料中核实，不要用历史原文补回当前证据。'
    report['request']=request
    if args.action=='cli':
        work=root/'cli';(work/'.codeplus').mkdir(parents=True,exist_ok=True)
        write(work/'.codeplus/config.yaml',{'providers':[{'name':provider.name,'protocol':provider.protocol,'base_url':provider.base_url,
            'model':provider.model,'api_key':'','thinking':provider.thinking}],'enable_fork':False,
            'knowledge_development_config':str(root/'development.json')})
        env=dict(os.environ,OPENAI_API_KEY=provider.resolve_api_key(),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
        env.pop('PYTHONPATH',None)
        # Inside the selected working directory, the real host acceptEdits mode
        # supplies its ordinary path/write policy; no fabricated save callback.
        target=work/('continued-report-'+uuid4().hex[:8]+'.md')
        argv=[sys.executable,'-I','-B','-X','utf8','-m','codeplus','-p',request,'--knowledge-library',state['kb_id'],
            '--knowledge-mode','fixed','--knowledge-report',str(target),'--knowledge-continue',parent,
            '--mode','acceptEdits','--output-format','stream-json']
        done=subprocess.run(argv,cwd=work,env=env,capture_output=True,text=True,encoding='utf-8',timeout=630)
        report.update(child_argv=argv,child_cwd=str(work),exit_code=done.returncode,stdout=done.stdout,stderr=done.stderr)
        assert done.returncode==0
        events=[json.loads(line) for line in done.stdout.splitlines() if line.startswith('{')]
        report['result']=next(e for e in reversed(events) if e['type']=='result')
    else:
        report['entrypoint']=getattr(args,'entrypoint','completion')
        asyncio.run(answer(root,settings,provider,state,request,target,parent,report))
    runs=HOST['inspect_runs'](settings);latest=runs[-1];report['runs']=runs
    current=latest['run'];rid=current['run_id'];state['runs'].append(rid)
    # Preserve the trace even when an assertion fails, and do not overwrite a
    # previously generated report when rerunning an action.
    if target and target.exists():state['reports'].append({'path':str(target),'sha256':sha(target)})
    write(root/'state.json',state)
    with catalog._db.transaction() as db:
        report['retrieval_traces']=[json.loads(r[0]) for r in db.execute(
            'SELECT t.payload FROM retrieval_traces t JOIN source_calls c ON t.call_id=c.call_id WHERE c.run_id=? ORDER BY t.rowid',(rid,))]
        report['evidence_source_versions']=[json.loads(r[0]) for r in db.execute('SELECT payload FROM delivered_evidence WHERE run_id=?',(rid,))]
        report['source_calls']=[dict(zip(('call_id','kind','status','error'),r)) for r in db.execute(
            'SELECT call_id,kind,status,error FROM source_calls WHERE run_id=? ORDER BY rowid',(rid,))]
        report['delivered_open_evidence']=[r[0] for r in db.execute(
            "SELECT e.evidence_id FROM delivered_evidence e JOIN source_candidates s ON s.evidence_id=e.evidence_id "
            "JOIN source_calls c ON c.call_id=s.call_id WHERE e.run_id=? AND c.kind='open' AND c.status='ok'",(rid,))]
    assert current['parent_run_id']==parent and current['usage']['searches']>=1
    assert latest['pin']['state']=='released' and all(not r['violation'] for r in latest['requests'])
    for saved in state['reports']:assert sha(saved['path'])==saved['sha256']
    for run in runs:
        artifact=run['host'].get('artifact')
        if artifact:
            for cid in artifact['citation_ids']:assert open_citation(catalog,UUID(cid))
    report['historical_artifacts_preserved']=True
    if args.action=='delete':
        assert not {p['evidence']['source_ref']['document_id'] for p in report['evidence_source_versions']}.intersection(report['removed_document_ids'])
    # Every normal action, including delete, requires completion and a body
    # actually delivered from a successful open; attempt counts are not proof.
    assert current['status']=='completed',current['stop_reason']
    assert current['usage']['opens']>=1
    assert report['delivered_open_evidence'], 'no successful open body was delivered'
    if args.action!='delete':
        saved=latest['host']['detail']['save'];assert saved['status']=='saved'
        assert saved['sha256']==sha(target)
        assert target.read_text(encoding='utf-8')==latest['host']['artifact']['markdown']
        report['actual_file']={'path':str(target),'sha256':sha(target),'size_bytes':target.stat().st_size}
    report['boundary']='Real installed Agent, network answer provider, CUDA models and Milvus. Small synthetic English corpus; no R23 quality claim.'


def main(args):
    report={'status':'RUNNING','action':args.action,'started':time.time(),'argv':sys.argv,'python':sys.executable,
            'installed_modules':{'codeplus':codeplus.__file__,'agentic_rag':agentic_rag.__file__}}
    assert sys.flags.isolated and all('site-packages' in Path(p).parts for p in report['installed_modules'].values())
    try:
        (prepare if args.action=='prepare' else live)(args,report);report['status']='PASS'
    except BaseException as error:
        report.update(status='FAIL',error_type=type(error).__name__,error=str(error));raise
    finally:
        report['finished']=time.time();write(args.report,report)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','report','cli','update','delete'])
    p.add_argument('--entrypoint',choices=['completion','stream'],default='completion')
    for name in ('root','report'):p.add_argument('--'+name,required=True)
    for name in ('endpoint','cuda-python','model-cache','answer-tokenizer','provider-config'):p.add_argument('--'+name)
    main(p.parse_args())
