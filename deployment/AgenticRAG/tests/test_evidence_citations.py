"""Core trusted receipt/citation checks. Confirmed fixtures are not HTTP proof."""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest

from agentic_rag.citations import CitationRegistry, footnote, open_citation
from agentic_rag.config import resolve_run
from agentic_rag.domain import ErrorCode, RagError, RunStatus, Span
from agentic_rag.evidence import DeliveryGateway, MappedSpan, read_evidence
from agentic_rag.sources import SourceSession

H=runpy.run_path(str(Path(__file__).with_name('source_support.py')))


@pytest.fixture
def source(tmp_path):
    value=H['fixture'](tmp_path,('0123456789'*30+'😀 e\u0301 identical identical').encode(),suffix='.txt')
    try:yield value
    finally:value[1].close()


@pytest.mark.parametrize('status',['not_sent','rejected','unknown','confirmed'])
def test_only_confirmed_exact_subset_has_qualification_and_duplicate_idempotent(source,status):
    catalog,lease,session,_,_,chunks,ref=source
    result=session.open(session.issue_source(ref));item=result.payload['items'][0]
    gateway=DeliveryGateway(session)
    permit,raw,mappings=H['prepared'](gateway,result,(Span(start=120,end=145),))
    identity=UUID(item['evidence_id'])
    with pytest.raises(RagError):read_evidence(catalog,lease.run.run_id,identity)
    before=session.usage()
    ids=gateway.settle(permit,status)
    assert gateway.settle(permit,status)==ids and session.usage()==before
    with pytest.raises(RagError):gateway.settle(permit,'unknown' if status=='confirmed' else 'confirmed')
    if status!='confirmed':
        assert ids==()
        with pytest.raises(RagError):read_evidence(catalog,lease.run.run_id,identity)
    else:
        evidence,_,_=read_evidence(catalog,lease.run.run_id,identity)
        assert evidence.spans==(Span(start=120,end=145),)
        assert evidence.text_hash==hashlib.sha256(chunks.parsed.text[120:145].encode()).hexdigest()
        registry=CitationRegistry(session)
        registry.validate(identity,(Span(start=120,end=145),),(chunks.parsed.text[120:145],))
        with pytest.raises(RagError):registry.validate(identity,(Span(start=119,end=145),),(chunks.parsed.text[119:145],))
    with catalog._db.transaction() as connection:
        row=connection.execute('SELECT status,payload FROM delivery_receipts WHERE request_id=?',(str(permit.request_id),)).fetchone()
    saved=json.loads(row[1]);assert row[0]==status and saved['body_sha256']==hashlib.sha256(raw).hexdigest()
    assert saved['mappings'][0]['body_span']=={'schema_version':1,'start':8,'end':33}


def test_compact_window_and_history_do_not_grant_new_evidence(source):
    catalog,lease,session,_,_,chunks,ref=source
    result=session.open(session.issue_source(ref));identity=UUID(result.payload['items'][0]['evidence_id'])
    gateway=DeliveryGateway(session)
    compact,_,_=H['prepared'](gateway,result,purpose='compact')
    assert gateway.settle(compact,'confirmed')==()
    with pytest.raises(RagError):read_evidence(catalog,lease.run.run_id,identity)
    final,_,_=H['prepared'](gateway,result,purpose='finalize')
    gateway.settle(final,'confirmed')
    assert gateway.window()['mappings']
    empty=gateway.prepare(b'{"messages":["summary without source mapping"]}',(),purpose='compact',protocol='compat')
    gateway.retain_prepared_window(empty)
    gateway.settle(empty,'confirmed')
    assert gateway.window()['mappings']==[]
    assert read_evidence(catalog,lease.run.run_id,identity)[0].evidence_id==identity
    registry=CitationRegistry(session)
    saved=registry.save(identity,(Span(start=0,end=4),),(chunks.parsed.text[:4],))
    newer=catalog.start_run(ref.kb_id,resolve_run(lease.run.resolved_config.knowledge,'qa'),parent_run_id=lease.run.run_id)
    try:
        with pytest.raises(RagError):CitationRegistry(SourceSession(catalog,newer,H['ControlledMeter']())).validate(identity,(Span(start=0,end=4),),('0123',))
        assert open_citation(catalog,UUID(saved['citation']['citation_id']))==saved
    finally:newer.close()


@pytest.mark.parametrize('fault',['unknown','extension','wrong_body','wrong_position','wrong_call','bad_path','overlap','uuid_permit','dict_permit'])
def test_final_body_mapping_and_capability_rejects_forgery(source,fault):
    catalog,lease,session,_,_,_,ref=source
    result=session.open(session.issue_source(ref));item=result.payload['items'][0]
    gateway=DeliveryGateway(session);gateway.bind_tool_result(result,'tool-actual-1')
    raw=b'{"messages":[{"role":"tool","content":"0123","tool_call_id":"tool-actual-1"}]}'
    mapping=MappedSpan(UUID(item['candidate_id']),Span(start=0,end=4),('messages',0,'content'),Span(start=0,end=4),('messages',0,'tool_call_id'))
    if fault=='unknown':mapping=MappedSpan(uuid4(),mapping.source_span,mapping.json_path,mapping.body_span,mapping.tool_call_id_path)
    if fault=='extension':mapping=MappedSpan(mapping.candidate_id,Span(start=1000,end=1004),mapping.json_path,mapping.body_span,mapping.tool_call_id_path)
    if fault=='wrong_body':raw=raw.replace(b'0123',b'0124')
    if fault=='wrong_position':mapping=MappedSpan(mapping.candidate_id,Span(start=1,end=5),mapping.json_path,mapping.body_span,mapping.tool_call_id_path)
    if fault=='wrong_call':raw=raw.replace(b'tool-actual-1',b'model-fake-call')
    if fault=='bad_path':mapping=MappedSpan(mapping.candidate_id,mapping.source_span,('missing',),mapping.body_span,mapping.tool_call_id_path)
    with pytest.raises(RagError):
        if fault=='uuid_permit':gateway.settle(uuid4(),'confirmed')
        elif fault=='dict_permit':gateway.settle({'request_id':str(uuid4())},'confirmed')
        else:gateway.prepare(raw,(mapping,mapping) if fault=='overlap' else (mapping,),purpose='explore',protocol='compat')
    with catalog._db.transaction() as conn:
        assert conn.execute('SELECT count(*) FROM delivered_evidence').fetchone()==(0,)


def test_multi_span_gap_exact_quotes_nfc_and_stable_historical_footnotes(source):
    catalog,lease,session,path,version,chunks,ref=source
    result=session.open(session.issue_source(ref));identity=UUID(result.payload['items'][0]['evidence_id'])
    gateway=DeliveryGateway(session)
    permit,_,_=H['prepared'](gateway,result,(Span(start=0,end=4),Span(start=8,end=12),Span(start=302,end=304)))
    gateway.settle(permit,'confirmed')
    registry=CitationRegistry(session)
    saved=registry.save(identity,(Span(start=0,end=4),Span(start=8,end=12)),('0123','8901'))
    assert registry.save(identity,(Span(start=0,end=4),Span(start=8,end=12)),('0123','8901'))==saved
    for spans,quotes in [((Span(start=2,end=10),),('23456789',)),
                         ((Span(start=0,end=4),),('0124',)),
                         ((Span(start=10,end=14),),('0123',)),
                         ((Span(start=302,end=304),),('é',)),
                         ((Span(start=8,end=12),Span(start=0,end=4)),('8901','0123'))]:
        with pytest.raises(RagError):registry.validate(identity,spans,quotes)
    with pytest.raises(RagError):registry.validate(uuid4(),(Span(start=0,end=4),),('0123',))
    value=footnote(saved)
    assert '[0,4)' in value and '[8,12)' in value and '234567' not in value
    path.unlink()
    lease.finish(RunStatus.COMPLETED,'finished')
    assert open_citation(catalog,UUID(saved['citation']['citation_id']))==saved
    child=subprocess.run([sys.executable,'-I','-B',str(Path(__file__).with_name('citation_history_helper.py')),
        str(catalog._directory.root),saved['citation']['citation_id']],capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)=={'citation':saved,'forbidden_modules_loaded':[]}
    with pytest.raises((RagError,OSError)):registry.validate(identity,(Span(start=0,end=4),),('0123',))
    # Corrupt only this fixture archive, never user/model data.
    obj=catalog.archives.verify(version.parsed_hash)
    # Archive layout comes from the verified object, not a guessed source path.
    assert obj.sha256==version.parsed_hash
    actual=catalog.archives._path(version.parsed_hash)
    actual.write_bytes(b'corrupted')
    with pytest.raises(RagError):open_citation(catalog,UUID(saved['citation']['citation_id']))


def test_shared_window_requires_explicit_trusted_crop(source):
    catalog,lease,session,_,version,chunks,ref=source
    H['dense_fixture'](session,chunks,version,ref)
    result=session.search('source');token=result.payload['items'][0]['source_ref']
    returned=session.usage()['returned_tokens']
    for _ in range(20):
        try:session.open(token)
        except RagError as exc:
            assert exc.error.code==ErrorCode.BUDGET_EXHAUSTED
            break
    else:raise AssertionError('cumulative window was reset between calls')
    before=session.usage()
    H['discard_window'](session)
    after=session.usage()
    assert after['returned_tokens']==before['returned_tokens']>=returned
    assert after['window_fragments']==0 and after['window_tokens']>0
    assert session.open(token).payload['items']
    with catalog._db.transaction() as connection:
        assert connection.execute('SELECT count(*) FROM delivered_evidence').fetchone()==(0,)


@pytest.mark.parametrize('protocol',['compat','responses','anthropic'])
def test_three_protocols_only_legal_tool_text_can_confirm(source,protocol):
    catalog,lease,session,_,_,_,ref=source
    result=session.open(session.issue_source(ref));gateway=DeliveryGateway(session)
    permit,raw,mappings=H['prepared'](gateway,result,protocol=protocol)
    assert gateway.settle(permit,'confirmed')
    original=json.loads(raw)
    for mode in ('system','user_text','assistant','metadata','borrowed_id','error_or_nontext'):
        body=copy.deepcopy(original);m=mappings[0]
        if mode=='metadata':
            body['metadata']=result.payload['items'][0]['text']
            changed=MappedSpan(m.candidate_id,m.source_span,('metadata',),Span(start=0,end=m.source_span.end-m.source_span.start),m.tool_call_id_path)
        elif mode=='borrowed_id':
            body['borrowed']='tool-actual-1'
            changed=MappedSpan(m.candidate_id,m.source_span,m.json_path,m.body_span,('borrowed',))
        else:
            changed=m
            if protocol=='compat':
                body['messages'][0]['role']={'system':'system','user_text':'user','assistant':'assistant','error_or_nontext':'user'}[mode]
            elif protocol=='responses':
                body['input'][0]['type']='message';body['input'][0]['role']='user'
            elif mode=='error_or_nontext':
                body['messages'][0]['content'][0]['is_error']=True
            elif mode=='user_text':
                body['messages'][0]['content'][0]['type']='text'
            else:
                body['messages'][0]['role']=mode
        with pytest.raises(RagError):gateway.prepare(json.dumps(body).encode(),(changed,),purpose='explore',protocol=protocol)


def test_old_window_permit_cannot_discard_later_source_reservations(source):
    catalog,lease,session,_,_,_,ref=source
    token=session.issue_source(ref);gateway=DeliveryGateway(session)
    stale=gateway.prepare(b'{"messages":[]}',(),purpose='explore',protocol='compat')
    session.open(token)
    with pytest.raises(RagError):gateway.retain_prepared_window(stale)
    fresh=gateway.prepare(b'{"messages":[]}',(),purpose='explore',protocol='compat')
    gateway.retain_prepared_window(fresh)
    gateway.retain_prepared_window(fresh)
    session.open(token)
    with pytest.raises(RagError):gateway.retain_prepared_window(fresh)


def test_provisional_evidence_markers_resolve_to_exact_saved_footnotes(source):
    catalog,lease,session,_,_,_,ref=source
    result=session.open(session.issue_source(ref));gateway=DeliveryGateway(session)
    permit,_,_=H['prepared'](gateway,result)
    gateway.settle(permit,'confirmed')
    registry=CitationRegistry(session)
    identity=UUID(result.payload['items'][0]['evidence_id'])
    saved=registry.save(identity,(Span(start=0,end=4),),('0123',))
    draft='Exact excerpt 0123 '+saved['evidence_marker']+'.'
    artifact=registry.render_markdown(draft,(saved,))
    assert saved['evidence_marker'] not in artifact['markdown']
    assert artifact['markdown'].count(saved['citation_marker'])==2
    assert hashlib.sha256(artifact['markdown'].encode()).hexdigest()==artifact['sha256']
    for bad in ('No citations',draft+' [^unknown]',draft+'\n'+saved['evidence_marker']+': forged footnote'):
        with pytest.raises(RagError):registry.render_markdown(bad,(saved,))
    forged=copy.deepcopy(saved);forged['quotes']=['0124']
    with pytest.raises(RagError):registry.render_markdown(draft,(forged,))


@pytest.mark.parametrize('protocol',['compat','responses','anthropic'])
@pytest.mark.parametrize('operation',['search','open'])
def test_public_rendered_tool_result_roundtrips_exact_source_in_final_http_json(tmp_path,protocol,operation):
    raw=('\ufeff# 标题\r\n"quoted" \\ slash 😀 e\u0301\r\nrepeated text\r\nrepeated text\r\n').encode()
    catalog,lease,session,_,version,chunks,ref=H['fixture'](tmp_path,raw)
    try:
        H['dense_fixture'](session,chunks,version,ref)
        result=session.search('quoted') if operation=='search' else session.open(session.issue_source(ref))
        gateway=DeliveryGateway(session);gateway.bind_tool_result(result,'actual-call')
        if protocol=='compat':
            body={'messages':[{'role':'tool','tool_call_id':'actual-call','content':result.text}]}
            path=('messages',0,'content');callpath=('messages',0,'tool_call_id')
        elif protocol=='responses':
            body={'input':[{'type':'function_call_output','call_id':'actual-call','output':result.text}]}
            path=('input',0,'output');callpath=('input',0,'call_id')
        else:
            body={'messages':[{'role':'user','content':[{'type':'tool_result','tool_use_id':'actual-call',
                  'content':[{'type':'text','text':result.text}]}]}]}
            path=('messages',0,'content',0,'content',0,'text');callpath=('messages',0,'content',0,'tool_use_id')
        mappings=tuple(MappedSpan(m.candidate_id,m.source_span,path,m.body_span,callpath) for m in result.body_mappings)
        request=json.dumps(body,ensure_ascii=True).encode('utf-8')
        permit=gateway.prepare(request,mappings,purpose='explore',protocol=protocol)
        ids=gateway.settle(permit,'confirmed');assert ids
        for identity in ids:
            evidence,archive,_=read_evidence(catalog,lease.run.run_id,identity)
            assert ''.join(archive.text[s.start:s.end] for s in evidence.spans)==chunks.parsed.text
        assert session.usage()['returned_tokens']==len(result.text.encode('utf-8'))
    finally:lease.close()


@pytest.mark.parametrize('field',['evidence_id','run_id','revision_id','section_range','delivery_id','undelivered'])
def test_persisted_evidence_corruption_does_not_rebind_authority(source,field):
    """Direct DB corruption fixture, not an ability given to model parameters."""
    catalog,lease,session,_,_,chunks,ref=source
    result=session.open(session.issue_source(ref));gateway=DeliveryGateway(session)
    permit,_,_=H['prepared'](gateway,result,(Span(start=0,end=4),))
    identity,=gateway.settle(permit,'confirmed')
    with catalog._db.transaction(write=True) as connection:
        value=json.loads(connection.execute('SELECT payload FROM delivered_evidence WHERE evidence_id=?',(str(identity),)).fetchone()[0])
        if field in ('evidence_id','run_id'):value['evidence'][field]=str(uuid4())
        elif field=='revision_id':value['evidence']['source_ref']['revision_id']=str(uuid4())
        elif field=='delivery_id':
            fake=str(uuid4());value['evidence']['delivery_id']=fake;value['deliveries']=[fake]
        elif field=='undelivered':
            value['evidence']['spans']=[{'start':10,'end':14}]
            value['evidence']['text_hash']=hashlib.sha256(chunks.parsed.text[10:14].encode()).hexdigest()
        else:
            value['evidence']['spans']=[{'start':0,'end':len(chunks.parsed.text)+1}]
        connection.execute('UPDATE delivered_evidence SET payload=? WHERE evidence_id=?',(json.dumps(value),str(identity)))
    with pytest.raises(RagError):read_evidence(catalog,lease.run.run_id,identity)


@pytest.mark.parametrize('field,value',[('file_name','wrong.txt'),('source_uri','file:///wrong.txt'),
                                      ('section_path',['invented']),('lines',[[999,1000]])])
def test_historical_display_metadata_is_rechecked_against_archive(source,field,value):
    """An explicitly inserted corrupt derived row; no model database access."""
    catalog,lease,session,_,_,_,ref=source
    result=session.open(session.issue_source(ref));gateway=DeliveryGateway(session)
    permit,_,_=H['prepared'](gateway,result,(Span(start=0,end=4),))
    identity,=gateway.settle(permit,'confirmed')
    registry=CitationRegistry(session)
    saved=registry.save(identity,(Span(start=0,end=4),),('0123',))
    damaged=copy.deepcopy(saved);damaged[field]=value;new_id=uuid4()
    damaged['citation']['citation_id']=str(new_id);damaged['citation_marker']='[^'+str(new_id)+']'
    with catalog._db.transaction(write=True) as connection:
        connection.execute('INSERT INTO saved_citations VALUES(?,?,?,?)',
            (str(new_id),str(lease.run.run_id),str(identity),json.dumps(damaged)))
    with pytest.raises(RagError,match='display metadata'):open_citation(catalog,new_id)
    for span,quote,reason in [(Span(start=0,end=5),'01234','citation_range_unconfirmed'),
                             (Span(start=0,end=4),'0124','citation_quote_mismatch')]:
        with pytest.raises(RagError,match=reason) as caught:registry.validate(identity,(span,),(quote,))
        assert str(identity) in caught.value.error.message and 'span=[' in caught.value.error.message


def test_duplicate_confirmation_callbacks_are_atomic_and_idempotent(source):
    catalog,lease,session,_,_,_,ref=source
    result=session.open(session.issue_source(ref));gateway=DeliveryGateway(session)
    permit,_,_=H['prepared'](gateway,result,(Span(start=0,end=4),))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(gateway.settle,permit,'confirmed') for _ in range(2)]
        outcomes=[f.result(timeout=20) for f in futures]
    assert outcomes[0]==outcomes[1]
    record,_,data=read_evidence(catalog,lease.run.run_id,outcomes[0][0])
    assert data['deliveries']==[str(permit.request_id)] and record.spans==(Span(start=0,end=4),)
