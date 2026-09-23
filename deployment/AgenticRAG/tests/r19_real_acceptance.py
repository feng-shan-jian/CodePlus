"""Opt-in installed R19 GPU/Milvus and existing network Agent acceptance.

Core failure is an actual frozen-tokenizer input refusal. Answer-model runs use
the normal policy and SDK; their outputs are never constructed by this helper.
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
from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, WorkerExecutionConfig, resolve_run
from agentic_rag.domain import ErrorCode, RagError
from agentic_rag.ingestion import InputSelection, begin_changes, build_changes
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models import LocalModelClient, FrozenTokenizer
from agentic_rag.retrieval import RetrievalSearch
from agentic_rag.sources import SourceSession
from agentic_rag.storage import Catalog

H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
S=runpy.run_path(str(Path(__file__).with_name('source_support.py')))
HOST=runpy.run_path(str(Path(__file__).with_name('r12_real_acceptance.py')))
PROMPT=('Which telescope certificate is archived in the ocean registry? '
        'Search the library and then open the returned source_ref to verify the original text. '
        'Give a concise answer with exact source citations. If offsets are uncertain, quote the whole returned body and its returned_spans.')


def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def core(args,report):
    root=Path(args.root);root.mkdir(parents=True)
    data=H['configuration'](root/'data',args.endpoint).model_dump(mode='json')
    data['retrieval'].update(mode='auto',route='hybrid',rerank=True,rerank_candidates=8,context_chunks=8,context_tokens=50000)
    for kind,total,duration in [('qa',200000,300000),('report',400000,600000)]:
        data['budgets'][kind].update(searches=10,opens=10,total_tokens=total,duration_ms=duration,
                                   finish_reserve_tokens=32000,finish_reserve_ms=30000)
    config=KnowledgeConfig.model_validate_json(json.dumps(data))
    worker=WorkerExecutionConfig(executable=args.cuda_python,model_cache=args.model_cache,
        runtime_dir=str(root/'worker'),idle_timeout_ms=1000)
    settings=DevelopmentConfig(knowledge=config,worker=worker,answer_tokenizer=args.answer_tokenizer,
        explore_output_cap=2048,finish_input_upper=12000,finalize_output_cap=2048,repair_output_cap=2048,
        compact_output_cap=2048,max_iterations=12,max_tool_attempts=30,cleanup_grace_ms=1000)
    catalog=Catalog(root/'data');provider=LocalModelClient(worker);backend=MilvusRevisionIndex(config.storage,catalog)
    kb=catalog.create_library('R19 actual modes').kb_id
    source=root/'source.md'
    source.write_text('# One\nThe blue telescope certificate is archived in the ocean registry.\n'
        '# Two\nThe red telescope certificate is archived in the hill registry.\n'
        '# Three\nThe amber telescope certificate is archived in the river registry.\n',encoding='utf-8')
    report['source_sha256']=hashlib.sha256(source.read_bytes()).hexdigest();report['runs']=[]
    try:
        with begin_changes(catalog,kb,ProcessingSnapshot.capture(uuid4(),config),(InputSelection(path=str(source)),)) as owner:
            report['build']=build_changes(catalog,owner,provider,backend,FrozenTokenizer(config.embedding,args.model_cache))
        for mode in ('auto','fixed'):
            chosen=config.model_copy(update={'retrieval':config.retrieval.model_copy(update={'mode':mode,'route':'bm25'})})
            with catalog.start_run(kb,resolve_run(chosen,'qa')) as lease:
                session=SourceSession(catalog,lease,S['ControlledMeter'](),dense=RetrievalSearch(catalog,lease.run.run_id,provider,backend))
                traces=[]
                try:session.search('telescope '*2500,**({'strategy':'bm25','rerank':True} if mode=='auto' else {}))
                except RagError as error:
                    assert error.error.code==ErrorCode.INPUT_TOO_LONG
                    failure=error.error.call_id;traces.append(session.retrieval_trace(failure))
                else:raise AssertionError('complete rerank input was silently truncated')
                with catalog._db.transaction() as db:assert db.execute('SELECT count(*) FROM source_candidates WHERE call_id=?',(failure,)).fetchone()==(0,)
                selections=[('bm25',False),('dense',False),('hybrid',True)] if mode=='auto' else [(None,None)]
                for strategy,rerank in selections:
                    S['discard_window'](session)
                    found=session.search('Which telescope certificate is in the ocean registry?',
                        **({'strategy':strategy,'rerank':rerank} if strategy else {}))
                    traces.append(session.retrieval_trace(found.payload['call_id']))
                    assert found.payload['status']=='ok'
                    assert found.payload['route']=={'strategy':strategy or 'bm25','rerank':rerank if strategy else True}
                assert traces[1]['preceding_call_id']==failure
                assert all(t['revision_id']==str(lease.run.revision_id) for t in traces)
                assert traces[0]['rerank']['ranking']==[]
                assert session.usage()['searches']==len(traces)
                report['runs'].append({'mode':mode,'run_id':str(lease.run.run_id),'traces':traces,'source_usage':session.usage()})
        with catalog._db.transaction() as db:assert db.execute('SELECT count(*) FROM delivered_evidence').fetchone()==(0,)
        report['confirmed_evidence']=0
        report['boundary']='Real local GPU and Milvus; no answer HTTP in core action. Window removal is a controlled trusted-host fixture.'
        write(root/'development.json',settings.model_dump(mode='json'))
        write(root/'state.json',{'kb_id':str(kb),'revision_id':str(catalog.get_library(kb).current_revision_id)})
    finally:provider.close();backend.close()


async def completion(root,provider,state,report):
    from codeplus.agent import Agent
    from codeplus.client import create_client
    from codeplus.tools import ToolRegistry
    client=create_client(provider)
    policy=load_policy(str(root/'development.json'),state['kb_id'],provider,task_kind='report')
    agent=Agent(client,ToolRegistry(),provider.protocol,work_dir=str(root),execution_policy=policy)
    try:
        answer=await agent.run_to_completion(PROMPT+' For your first search explicitly select strategy bm25 and rerank false.')
        report.update(answer=answer,outcome=asdict(agent.last_run_outcome))
    finally:await client._client.close()


def live(args,report):
    from codeplus.config import load_config
    root=Path(args.root);state=json.loads((root/'state.json').read_text(encoding='utf-8'))
    settings=DevelopmentConfig.model_validate_json((root/'development.json').read_text(encoding='utf-8'))
    providers=load_config(Path(args.provider_config)).providers
    provider=next(p for p in providers if p.protocol=='openai-compat' and p.model in {'deepseek-chat','deepseek-reasoner'}
                  and p.base_url.rstrip('/') in {'https://api.deepseek.com','https://api.deepseek.com/v1'})
    report['provider']={'name':provider.name,'protocol':provider.protocol,'model':provider.model,'base_url':provider.base_url}
    if args.action=='completion':asyncio.run(completion(root,provider,state,report))
    else:
        work=root/'cli';(work/'.codeplus').mkdir(parents=True,exist_ok=True)
        write(work/'.codeplus/config.yaml',{'providers':[{'name':provider.name,'protocol':provider.protocol,'base_url':provider.base_url,
            'model':provider.model,'api_key':'','thinking':provider.thinking}],'enable_fork':False,
            'knowledge_development_config':str(root/'development.json')})
        env=dict(os.environ,OPENAI_API_KEY=provider.resolve_api_key(),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
        argv=[sys.executable,'-I','-B','-X','utf8','-m','codeplus','-p',PROMPT,'--knowledge-library',state['kb_id'],
              '--knowledge-mode','fixed','--output-format','stream-json']
        result=subprocess.run(argv,cwd=work,env=env,capture_output=True,text=True,encoding='utf-8',timeout=330)
        report.update(child_argv=argv,child_cwd=str(work),exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr)
        assert result.returncode==0
    report['runs']=HOST['inspect_runs'](settings)
    latest=report['runs'][-1]
    assert latest['run']['status']=='completed', latest['run']['stop_reason']
    assert latest['run']['usage']['searches']>=1 and latest['run']['usage']['opens']>=1
    assert latest['pin']['state']=='released' and latest['host']['artifact']
    assert all(not request['violation'] for request in latest['requests'])
    expected={'value':'auto','source':'configured'} if args.action=='completion' else {'value':'fixed','source':'explicit'}
    assert latest['host']['frozen']['mode']==expected
    assert latest['run']['resolved_config']['task_kind']==('report' if args.action=='completion' else 'qa')
    catalog=Catalog(settings.knowledge.storage.data_dir)
    with catalog._db.transaction() as db:
        report['retrieval_traces']=[json.loads(row[0]) for row in db.execute('SELECT t.payload FROM retrieval_traces t JOIN source_calls c ON t.call_id=c.call_id WHERE c.run_id=? ORDER BY t.rowid',(latest['run']['run_id'],))]
    assert report['retrieval_traces']
    if args.action=='completion':assert any(t['selection']['requested']=={'strategy':'bm25','rerank':False} for t in report['retrieval_traces'])
    else:assert all(t['route']=='hybrid' and t['rerank']['enabled'] for t in report['retrieval_traces'])


def main(args):
    report={'status':'RUNNING','action':args.action,'started':time.time(),'argv':sys.argv,'python':sys.executable,
            'installed_modules':{'codeplus':codeplus.__file__,'agentic_rag':agentic_rag.__file__}}
    assert sys.flags.isolated and all('site-packages' in Path(p).parts for p in report['installed_modules'].values())
    try:
        (core if args.action=='core' else live)(args,report)
        report['status']='PASS'
    except BaseException as error:
        report.update(status='FAIL',error_type=type(error).__name__,error=str(error));raise
    finally:
        report['finished']=time.time();write(args.report,report)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['core','completion','cli'])
    for name in ('root','report'):p.add_argument('--'+name,required=True)
    for name in ('endpoint','cuda-python','model-cache','answer-tokenizer','provider-config'):p.add_argument('--'+name)
    main(p.parse_args())
