"""R20 real host loops/permissions/files/SQLite; synthetic model and index IO."""
import asyncio
from contextlib import aclosing
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from codeplus.agent import Agent, PermissionRequest, PermissionResponse, StreamText
from codeplus.client import create_client, scoped_client
from codeplus.config import ProviderConfig
from codeplus.conversation import ConversationManager
from codeplus.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from codeplus.tools import create_default_registry
from codeplus.tools.base import ToolResult
from agentic_rag.adapters.codeplus import policy as P
from agentic_rag.adapters.codeplus.research import history
from agentic_rag.domain import RagError
from agentic_rag.citations import open_citation

M = runpy.run_path(str(Path(__file__).with_name('test_run_modes.py')))
R, H = M['R'], M['H']


class Research:
    def __init__(self, root, monkeypatch):
        self.root, self.monkeypatch = root, monkeypatch
        self.catalog, self.kb, base, self.rows, _ = R['published'](root)
        self.settings = M['settings'](base, root)
        self.provider = ProviderConfig('fixture','openai-compat','https://fixture.invalid','fixture','synthetic')
        self.parent = create_client(self.provider)
        self.registry = create_default_registry()
        self.calls = []

    async def run(self, *, completion=False, path=None, parent=None, effect='allow', approve=True,
                  bad=False, old_claim=None, stop=None, unknown_usage=False, omit_progress=False, cli=False,
                  preface=False,
                  request='中文问题：比较资料并说明局限'):
        M['host_dependencies'](self.monkeypatch, self.settings, self.rows, [])
        policy = P.KnowledgePolicy(self.settings, self.kb, self.provider, task_kind='report' if path else 'qa',
                                   report_path=str(path) if path else None, parent_run_id=parent)
        observed = []
        def transport(req):
            body = json.loads(req.content); observed.append(body)
            assert {t['function']['name'] for t in body.get('tools',[])} <= {'knowledge_search','knowledge_open'}
            if len(observed) == 1:
                content = H['sse_text'](calls=('search','knowledge_search',{'query':'telescope certificate comparison'}),terminal='tool_calls')
            else:
                source = next((m['content'] for m in reversed(body['messages']) if m['role']=='tool' and '<source ' in m['content']),None)
                if source is None:
                    return httpx.Response(200,headers={'content-type':'text/event-stream'},
                        content=H['sse_text'](json.dumps({'markdown':'当前资料缺少可核实依据。','citations':[]})))
                meta = json.JSONDecoder().raw_decode(source)[0]; item = meta['items'][0]
                text = source.split('>\n',1)[1].split('\n</source>',1)[0]
                claim = old_claim or {'evidence_id':item['evidence_id'],'spans':item['returned_spans'],'quotes':[text+'wrong' if bad else text]}
                if stop == 'budget':
                    policy.scope.finish_reason = 'search_limit'
                markdown = '# 研究报告\n\n## 结论\n资料结论 [^'+claim['evidence_id']+']\n\n## 比较与局限\n未找到完整对照。'
                answer = {'markdown':markdown,'citations':[claim],
                    'progress':{'covered':['已查 telescope'],'pending':['待查对照'],'findings':['上一轮的公开发现'],
                                'revised':[],'unverified':['对照未核实']}}
                if omit_progress:answer.pop('progress')
                public_text=json.dumps(answer)
                if preface:
                    assert not path.exists(), 'invalid prefaced draft must not be saved'
                    if len(observed)==2:
                        public_text='I have verified all the evidence. Let me compile the report.\n\n'+public_text
                content = H['sse_text'](public_text)
                self.last_claim = claim
            if unknown_usage:
                content = content.replace(b'"usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}',b'"usage": null')
            return httpx.Response(200, headers={'content-type':'text/event-stream'}, content=content)
        self.monkeypatch.setattr(P,'scoped_client',lambda c:scoped_client(c,transport=httpx.MockTransport(transport)))
        permissions=self.root/'permissions.yaml'
        permissions.write_text('- rule: "WriteFile(*)"\n  effect: '+effect+'\n',encoding='utf-8')
        checker=PermissionChecker(DangerousCommandDetector(),PathSandbox(str(self.root)),
            RuleEngine(project_rules_path=permissions),mode=PermissionMode.DEFAULT)
        agent=Agent(self.parent,self.registry,'openai-compat',work_dir=str(self.root),execution_policy=policy,
                    permission_checker=checker)
        emitted=[]; requested=[]
        try:
            if cli:
                from codeplus.__main__ import _run_prompt
                from codeplus.config import AppConfig
                self.monkeypatch.chdir(self.root)
                (self.root/'.codeplus').mkdir(exist_ok=True)
                (self.root/'.codeplus/permissions.local.yaml').write_bytes(permissions.read_bytes())
                self.monkeypatch.setattr(P,'load_policy',lambda *a,**kw:policy)
                self.monkeypatch.setattr('codeplus.client.create_client',lambda *_:self.parent)
                await _run_prompt(AppConfig(providers=[self.provider],enable_fork=False,
                    knowledge_development_config=str(self.root/'config.json')),PermissionMode.DEFAULT,None,request,'stream-json',
                    knowledge_library=str(self.kb),knowledge_report=str(path))
                agent.last_run_outcome=policy.scope.outcome
                text=''
            elif completion:
                text=await agent.run_to_completion(request)
            else:
                conv=ConversationManager();conv.add_user_message(request)
                async with aclosing(agent.run(conv)) as stream:
                    async for event in stream:
                        if isinstance(event,PermissionRequest):
                            requested.append(event)
                            if stop == 'cancel':
                                break
                            event.future.set_result(PermissionResponse.ALLOW if approve else PermissionResponse.DENY)
                        elif isinstance(event,StreamText):emitted.append(event.text)
                text=''.join(emitted)
        finally:
            self.calls.append(observed)
        self.policy,self.agent,self.requested=policy,agent,requested
        return agent.last_run_outcome,text


@pytest.fixture
def research(tmp_path,monkeypatch):
    instance=Research(tmp_path,monkeypatch)
    yield instance
    asyncio.run(instance.parent._client.close())


@pytest.mark.parametrize('completion',[False,True])
@pytest.mark.parametrize('effect,approve,expected',[
    ('allow',True,'saved'),('deny',True,'failed'),('ask',True,'saved'),('ask',False,'failed')])
def test_report_uses_host_permissions_and_actual_multiline_bytes(research,completion,effect,approve,expected):
    async def run():
        path=research.root/'reports/report.md'
        outcome,text=await research.run(completion=completion,path=path,effect=effect,approve=approve)
        if effect=='ask' and completion: wanted='failed'
        else:wanted=expected
        assert outcome.save.status==wanted
        assert outcome.run_id==outcome.artifact.run_id
        assert path.exists()==(wanted=='saved')
        assert ('Report saved:' in text)==(wanted=='saved')
        if wanted=='saved':
            assert path.read_text(encoding='utf-8')==outcome.artifact.markdown
            assert outcome.save.sha256==hashlib.sha256(path.read_bytes()).hexdigest()
            assert outcome.save.size_bytes==len(path.read_bytes())
            assert outcome.status=='completed'
        else:
            assert outcome.status=='incomplete' and outcome.reason=='report_save_failed'
        with research.catalog._db.transaction() as db:
            detail=json.loads(db.execute('SELECT detail FROM host_runs WHERE run_id=?',(outcome.run_id,)).fetchone()[0])
            assert detail['save']==asdict(outcome.save) and detail['answer_status']=='completed'
            assert db.execute("SELECT count(*) FROM host_tool_calls WHERE tool_name='WriteFile'").fetchone()==(0,)
    asyncio.run(run())


@pytest.mark.parametrize('completion',[False,True])
def test_invalid_whole_citation_set_never_calls_writer(research,completion):
    path=research.root/'invalid.md'
    outcome,text=asyncio.run(research.run(completion=completion,path=path,bad=True))
    assert outcome.status=='incomplete' and outcome.reason=='citation_invalid'
    assert outcome.artifact is None and outcome.save is None and not path.exists()
    assert len(research.calls[-1])==3 and not research.requested


@pytest.mark.parametrize('completion',[False,True])
def test_public_preamble_requires_existing_single_repair_before_save(research,completion):
    path=research.root/'prefaced-report.md'
    outcome,_=asyncio.run(research.run(completion=completion,path=path,preface=True))
    assert len(research.calls[-1])==3
    assert research.policy.scope.validation_errors==['invalid_json offset=0 line=1 column=1']
    assert outcome.status=='completed' and outcome.save.status=='saved'
    assert path.read_text(encoding='utf-8')==outcome.artifact.markdown


@pytest.mark.parametrize('state',['unread','read','changed','io_error'])
def test_existing_file_cache_and_write_error_are_not_bypassed(research,state):
    async def run():
        path=research.root/'existing.md'
        path.write_text('original',encoding='utf-8')
        if state=='io_error':path=path/'child.md'
        if state in {'read','changed'}:
            tool=research.registry.get('ReadFile')
            result=await tool.execute(tool.params_model(file_path=str(path)))
            assert not result.is_error
        if state=='changed':
            path.write_text('changed after read',encoding='utf-8')
            import os
            os.utime(path,ns=(path.stat().st_atime_ns,path.stat().st_mtime_ns+1000000000))
        outcome,_=await research.run(path=path)
        assert outcome.save.status==('saved' if state=='read' else 'failed')
        if state=='io_error':
            assert 'Error writing file' in outcome.save.message
            assert path.parent.read_text()=='original'
        if state not in {'read','io_error'}:assert not path.read_text().startswith('# 研究')
    asyncio.run(run())


def test_cancel_permission_revokes_future_and_preserves_progress(research):
    outcome,_=asyncio.run(research.run(path=research.root/'cancel.md',effect='ask',stop='cancel'))
    assert outcome.status=='cancelled' and outcome.save.status=='interrupted'
    assert research.requested[0].future.cancelled()
    assert not (research.root/'cancel.md').exists()
    assert outcome.research['rounds'][0]['status']=='cancelled'


@pytest.mark.parametrize('completion',[False,True])
def test_write_then_deadline_retains_observed_save(research,completion):
    writer=research.registry.get('WriteFile');original=writer.execute
    async def delayed(params):
        result=await original(params)
        # Synchronous WriteFile has finished before the scope's next check.
        import time
        agent_scope = next(scope for scope in [research.active_policy.scope] if scope)
        agent_scope.owner.deadline=time.monotonic()-1
        return result
    original_start=P.KnowledgePolicy.start
    async def start(policy,ctx):
        research.active_policy=policy
        return await original_start(policy,ctx)
    research.monkeypatch.setattr(P.KnowledgePolicy,'start',start)
    writer.execute=delayed
    outcome,_=asyncio.run(research.run(completion=completion,path=research.root/'late.md'))
    assert outcome.status=='incomplete' and outcome.reason=='time_budget'
    assert outcome.save.status=='saved' and outcome.artifact
    assert outcome.save.sha256==hashlib.sha256((research.root/'late.md').read_bytes()).hexdigest()


@pytest.mark.parametrize('completion',[False,True])
def test_cancel_after_write_keeps_file_and_cancelled_run(research,completion):
    writer=research.registry.get('WriteFile');original=writer.execute
    captured={}
    original_start=P.KnowledgePolicy.start
    async def start(policy,ctx):
        captured['policy']=policy
        return await original_start(policy,ctx)
    research.monkeypatch.setattr(P.KnowledgePolicy,'start',start)
    async def write_and_cancel(params):
        result=await original(params)
        asyncio.get_running_loop().call_soon(captured['outer'].cancel)
        return result
    writer.execute=write_and_cancel
    async def run():
        captured['outer']=asyncio.current_task()
        with pytest.raises(asyncio.CancelledError):
            await research.run(completion=completion,path=research.root/'cancel-after.md')
        outcome=captured['policy'].scope.outcome
        assert outcome.status=='cancelled' and outcome.save.status=='saved' and outcome.artifact
        assert outcome.save.sha256==hashlib.sha256((research.root/'cancel-after.md').read_bytes()).hexdigest()
        assert research.catalog.get_run(UUID(outcome.run_id)).status.value=='cancelled'
    asyncio.run(run())


def test_same_version_continuation_reacquires_evidence_and_new_budgets(research):
    async def run():
        first,_=await research.run(path=research.root/'first.md',stop='budget')
        old=research.last_claim; before=(research.root/'first.md').read_bytes()
        assert first.status=='partial'
        second,_=await research.run(parent=UUID(first.run_id),request='继续研究，优先补查对照')
        chain=second.research['rounds']
        assert len(chain)==2 and chain[-1]['parent_run_id']==first.run_id
        assert chain[0]['revision_id']==chain[1]['revision_id']
        assert [r['task_kind'] for r in chain]==['report','qa']
        assert [r['usage']['searches'] for r in chain]==[1,1]
        assert second.research['total_usage']['total_tokens']==80
        assert 'unverified_historical_leads' in json.dumps(research.calls[-1][0])
        assert '待查对照' in json.dumps(research.calls[-1][0],ensure_ascii=False)
        assert old['evidence_id']!=research.last_claim['evidence_id']
        assert (research.root/'first.md').read_bytes()==before
        third,_=await research.run(parent=UUID(second.run_id),old_claim=old)
        assert third.reason=='citation_invalid' and third.artifact is None
        with pytest.raises(ValueError,match='new report path'):
            await research.run(parent=UUID(second.run_id),path=research.root/'first.md')
    asyncio.run(run())


def test_latest_published_update_and_removed_source_not_restored(research):
    async def run():
        first,_=await research.run(path=research.root/'old.md')
        old_revision=first.research['rounds'][0]['revision_id']
        old_artifact=first.artifact
        # Production publication with a controlled index transport.
        owner,base=R['H']['processed'](research.catalog,research.root/'doc.md',kb_id=research.kb,
                                     text='# Updated\nThe earlier telescope certificate is no longer available.\n')
        prepared,_,research.rows,_,_=R['H']['unit_ready'](research.catalog,owner,base)
        R['publication'].publish(research.catalog,owner,prepared.revision_id);owner.close()
        second,_=await research.run(parent=UUID(first.run_id))
        assert second.research['rounds'][-1]['revision_id']!=old_revision
        assert 'no longer available' in second.artifact.markdown
        assert 'Telescope ocean.' not in second.artifact.markdown
        assert (research.root/'old.md').read_text(encoding='utf-8')==old_artifact.markdown
        for cid in old_artifact.citation_ids:
            assert open_citation(research.catalog,UUID(cid))
        # Delete through the production mutation protocol, then start again.
        mutations=runpy.run_path(str(Path(__file__).with_name('test_mutations.py')))
        from agentic_rag.config import ProcessingSnapshot
        from agentic_rag.ingestion import begin_changes,build_changes
        with research.catalog._db.transaction() as db:
            doc=UUID(db.execute('SELECT document_id FROM revision_members WHERE revision_id=?',
                                (str(prepared.revision_id),)).fetchone()[0])
        backend=mutations['backend'](research.catalog,base)
        with begin_changes(research.catalog,research.kb,ProcessingSnapshot.capture(uuid4(),base),(),delete_document_ids=(doc,)) as owner:
            deleted=build_changes(research.catalog,owner,mutations['Model'](),backend,R['H']['HELPER']['tokenizer']())
        assert deleted['summary']['published_deleted']==1
        research.rows=[]
        third,_=await research.run(parent=UUID(second.run_id))
        assert third.reason=='no_evidence' and third.artifact is None
        assert third.research['rounds'][-1]['revision_id']!=second.research['rounds'][-1]['revision_id']
        for cid in old_artifact.citation_ids:assert open_citation(research.catalog,UUID(cid))
    asyncio.run(run())


@pytest.mark.parametrize('omit_progress',[False,True])
def test_three_round_handoff_survives_failed_middle_round(research,omit_progress):
    async def run():
        first,_=await research.run(omit_progress=omit_progress)
        first_claim=research.last_claim
        failed,_=await research.run(parent=UUID(first.run_id),bad=True,request='仅比较 Atlas 和 Beacon 的2024年夜间资格，不要纳入2023年；本轮未能完成')
        assert failed.artifact is None
        third,_=await research.run(parent=UUID(failed.run_id),bad=True,request='再次继续')
        initial=json.dumps(research.calls[-1][0],ensure_ascii=False)
        if omit_progress:assert '未找到完整对照' in initial and 'historical citation' in initial
        assert first_claim['evidence_id'] not in initial and '<source ' not in initial
        if not omit_progress:
            assert '上一轮的公开发现' in initial and '待查对照' in initial
        assert len(third.research['rounds'])==3
        repaired=json.dumps(research.calls[-1][-1],ensure_ascii=False)
        assert '仅比较 Atlas 和 Beacon 的2024年夜间资格，不要纳入2023年' in repaired
        assert not research.calls[-1][-1].get('tools')
    asyncio.run(run())


def test_missing_cross_library_and_unknown_cumulative_usage(research):
    async def run():
        with pytest.raises(ValueError,match='missing'):
            await research.run(parent=uuid4())
        first,_=await research.run(unknown_usage=True)
        second,_=await research.run(parent=UUID(first.run_id))
        assert second.research['total_usage']['total_tokens'] is None
        assert second.research['rounds'][-1]['usage']['total_tokens']==40
        foreign=research.catalog.create_library('other').kb_id
        research.kb=foreign
        with pytest.raises(ValueError,match='another library'):
            await research.run(parent=UUID(first.run_id))
        research.kb=UUID(second.research['rounds'][-1]['kb_id'])
        with research.catalog._db.transaction(write=True) as db:
            db.execute("UPDATE host_runs SET detail='{}' WHERE run_id=?",(first.run_id,))
        with pytest.raises(ValueError,match='progress is missing'):
            await research.run(parent=UUID(first.run_id))
    asyncio.run(run())


def test_tui_explicit_report_and_continue_with_quoted_windows_path():
    from codeplus.commands.handlers.knowledge import handle_knowledge
    calls=[];messages=[];parent=str(uuid4())
    ui=SimpleNamespace(knowledge_feature_available=True,knowledge_library='kb',
        last_knowledge_outcome=SimpleNamespace(run_id=parent),
        send_knowledge_message=lambda *a,**kw:calls.append((a,kw)),add_system_message=messages.append)
    async def run():
        for args in ['report --output "D:\\Reports\\one report.md" --mode auto 比较两份材料','continue 补查缺口']:
            await handle_knowledge(SimpleNamespace(args=args,ui=ui))
    asyncio.run(run())
    assert not messages and len(calls)==2
    assert calls[0][1]['report_path']=='D:\\Reports\\one report.md'
    assert calls[1][1]['parent_run_id']==parent and calls[1][1]['task_kind']=='qa'


@pytest.mark.parametrize('effect',['allow','ask'])
def test_actual_prompt_report_result_exposes_run_and_real_save(research,capsys,effect):
    path=research.root/'cli.md'
    outcome,_=asyncio.run(research.run(cli=True,path=path,effect=effect))
    result=json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['type']=='result' and result['run_id']==outcome.run_id
    assert result['save']['status']==('saved' if effect=='allow' else 'failed')
    assert path.exists()==(effect=='allow')
    assert result['research']['rounds'][0]['run_id']==outcome.run_id
