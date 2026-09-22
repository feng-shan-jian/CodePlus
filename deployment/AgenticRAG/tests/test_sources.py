"""R11 exact source navigation/scope and shared tool budgets, no network/GPU."""
import json
from pathlib import Path
import runpy
from uuid import uuid4, UUID

import pytest

from agentic_rag.config import resolve_run
from agentic_rag.domain import RagError, RunStatus, Span
from agentic_rag.source_archive import read_ref
from agentic_rag.sources import SourceSession

H=runpy.run_path(str(Path(__file__).with_name('source_support.py')))


def test_nested_own_sections_navigation_and_full_coverage(tmp_path):
    raw=b'Preamble\n# A\nbody A\n## B\nbody B\n### C\nbody C\n## D\nbody D\n'
    catalog,lease,session,source,version,chunks,ref=H['fixture'](tmp_path,raw)
    try:
        token=session.issue_source(ref)
        sections=chunks.parsed.sections
        parts=[]
        for section in sections:
            H['discard_window'](session)
            result=session.open(token,section_id=section.section_id)
            item,=result.payload['items']
            assert item['text']==chunks.parsed.text[section.span.start:section.span.end]
            assert item['returned_spans']==[section.span.model_dump()]
            parts.append(item['text'])
        assert ''.join(parts)==chunks.parsed.text
        nav=session.directory(version.document_id,version.document_version_id)
        a,b,c,d=nav[1:]
        assert b['parent_section_id']==a['section_id'] and c['parent_section_id']==b['section_id']
        assert a['subtree_span']['end']==len(chunks.parsed.text)
        assert b['subtree_span']['end']==d['span']['start']
        default=session.open(session.issue_source(ref.model_copy(update={'section_id':sections[2].section_id})))
        assert default.payload['items'][0]['text']=='## B\nbody B\n'
    finally:lease.close()


@pytest.mark.parametrize('raw,suffix,body',[(b'plain root\n','.txt',True),(b'# Heading\n','.md',False),(b'   \n','.txt',False),(b'','.txt',False),(b'\xef\xbb\xbf','.txt',False)])
def test_root_navigation_only_and_empty(tmp_path,raw,suffix,body):
    catalog,lease,session,_,version,chunks,ref=H['fixture'](tmp_path,raw,suffix=suffix)
    try:
        nav=session.directory(version.document_id,version.document_version_id)
        if ref is None:
            assert nav==[] and chunks.parsed.text==''
        else:
            result=session.open(session.issue_source(ref))
            assert bool(result.candidate_ids)==body
            assert result.payload['status']==('ok' if body else 'navigation_only')
            if not body:assert result.payload['returned_spans']==[]
    finally:lease.close()


def test_long_unicode_paging_exact_progress_lines_and_other_section_cursor(tmp_path):
    raw=('\ufeff# 标题\r\n'+('😀 e\u0301 中文 repeated\r\n'*250)+'## Other\r\n'+('other words\r\n'*100)).encode()
    catalog,lease,session,_,_,chunks,ref=H['fixture'](tmp_path,raw,context_tokens=2300)
    try:
        token=session.issue_source(ref)
        for section in chunks.parsed.sections:
            H['discard_window'](session)
            result=session.open(token,section_id=section.section_id)
            text='';previous=section.span.start;pages=0
            while True:
                pages+=1
                item,=result.payload['items']; span=Span.model_validate(item['returned_spans'][0])
                assert span.start==previous and span.end>span.start
                assert item['text']==chunks.parsed.text[span.start:span.end]
                assert tuple(item['lines'])==chunks.parsed.source_map.lines(span)
                assert len(result.text.encode())<=2300
                text+=item['text'];previous=span.end
                if not result.payload['has_more']:
                    assert result.payload['next_cursor'] is None and result.payload['unread_spans']==[]
                    break
                assert result.payload['unread_spans']==[{'start':span.end,'end':section.span.end}]
                H['discard_window'](session)
                result=session.open(token,cursor=result.payload['next_cursor'])
            assert pages>1 and text==chunks.parsed.text[section.span.start:section.span.end]
    finally:lease.close()


def test_scope_forgery_cursor_conflicts_and_terminal_reject(tmp_path):
    catalog,lease,session,_,_,chunks,ref=H['fixture'](tmp_path,b'# H\n'+b'abcdefgh '*500,context_tokens=1800)
    second=catalog.start_run(ref.kb_id,resolve_run(lease.run.resolved_config.knowledge,'qa'))
    try:
        token=session.issue_source(ref)
        result=session.open(token);cursor=result.payload['next_cursor'];assert cursor
        foreign=SourceSession(catalog,second,H['ControlledMeter']())
        for action in [lambda:session.open('../source.md'),lambda:session.open(ref.model_dump()),
                       lambda:session.open(token,cursor='cur_forged'),lambda:foreign.open(token),
                       lambda:session.open(token,section_id=ref.section_id,cursor=cursor),
                       lambda:session.open(token,section_id=uuid4()),
                       lambda:session.open(session.issue_source(ref),cursor=cursor),
                       lambda:session.issue_source(ref.model_copy(update={'kb_id':uuid4()})),
                       lambda:session.issue_source(ref.model_copy(update={'revision_id':uuid4()})),
                       lambda:session.issue_source(ref.model_copy(update={'document_version_id':uuid4()}))]:
            with pytest.raises((RagError,ValueError)):action()
        # Explicitly corrupt persisted cursor range fixture; invalid is not EOF.
        with catalog._db.transaction(write=True) as conn:
            state={'source_token':token,'ref':ref.model_dump(mode='json'),'next_cp':len(chunks.parsed.text),'range_end':len(chunks.parsed.text)}
            conn.execute('INSERT INTO source_handles VALUES(?,?,?,?)',('cur_test_out_of_bounds',str(lease.run.run_id),'cursor',json.dumps(state)))
        with pytest.raises(RagError):session.open(token,cursor='cur_test_out_of_bounds')
        lease.finish(RunStatus.COMPLETED,'finished')
        with pytest.raises((RagError,OSError)):session.open(token)
    finally:lease.close();second.close()


def test_archive_source_deleted_and_current_revision_changes(tmp_path):
    catalog,lease,session,path,version,chunks,ref=H['fixture'](tmp_path)
    try:
        token=session.issue_source(ref)
        path.write_text('Changed external content',encoding='utf-8');path.unlink()
        with catalog._db.transaction(write=True) as conn:
            conn.execute('UPDATE libraries SET current_revision_id=NULL WHERE kb_id=?',(str(ref.kb_id),))
        assert session.open(token).payload['items'][0]['text']==chunks.parsed.text
        assert read_ref(catalog,ref).version.source_metadata.original_name==version.source_metadata.original_name
    finally:lease.close()


def test_shared_search_open_usage_failure_retry_and_session_recreation(tmp_path):
    catalog,lease,session,_,version,chunks,ref=H['fixture'](tmp_path,opens=2,searches=2)
    try:
        H['dense_fixture'](session,chunks,version,ref)
        search=session.search('source');token=search.payload['items'][0]['source_ref']
        opened=session.open(token);assert opened.payload['items']
        with pytest.raises(RagError):session.open('forged')
        recreated=SourceSession(catalog,lease,H['ControlledMeter'](),dense=session.dense)
        with pytest.raises(RagError):recreated.open(token)
        with pytest.raises(RagError) as invalid_query:recreated.search('')
        assert invalid_query.value.error.code.value=='INVALID_INPUT' and invalid_query.value.error.call_id
        with pytest.raises(RagError):recreated.search('source')
        usage=recreated.usage()
        assert usage['opens']==2 and usage['searches']==2 and usage['rejected']==2
        assert usage['returned_tokens']==len(search.text.encode())+len(opened.text.encode())
        finished=lease.finish(RunStatus.COMPLETED,'finished')
        assert finished.usage.opens==2 and finished.usage.searches==2
    finally:lease.close()


def test_full_metadata_wrapper_tokens_and_cross_call_remaining(tmp_path):
    catalog,lease,session,_,_,_,ref=H['fixture'](tmp_path,total_tokens=3000)
    try:
        token=session.issue_source(ref)
        first=session.open(token)
        assert session.usage()['returned_tokens']==len(first.text.encode())>len(first.payload['items'][0]['text'].encode())
        before=session.usage()['returned_tokens']
        # Repeat consumes more source-return budget even when it is the same text.
        try:session.open(token)
        except RagError:pass
        assert session.usage()['returned_tokens']>=before
        with pytest.raises(RagError):session.open(token)
        assert session.usage()['returned_tokens']<=2900
    finally:lease.close()


def test_actual_dense_search_object_survives_usage_and_open(tmp_path):
    """Actual R10 service, synthetic provider/transport; not GPU/Milvus proof."""
    from agentic_rag.capabilities import EmbeddingResponse,EmbeddingResult,ModelTimings
    from agentic_rag.retrieval import DenseSearch
    from agentic_rag.storage import Catalog,publication
    catalog=Catalog(tmp_path/'data')
    owner,config=H['H']['processed'](catalog,tmp_path/'source.md')
    prepared,_,_,backend,_=H['H']['unit_ready'](catalog,owner,config)
    publication.publish(catalog,owner,prepared.revision_id);owner.close()
    class Provider:
        owner_id=uuid4()
        def embed_query(self,item,profile,context):
            return EmbeddingResponse(request_id=context.request_id,profile_fingerprint=profile.identity,
                results=(EmbeddingResult(item_id=item.item_id,vector=tuple(H['H']['VECTOR']),input_tokens=9),),
                timings=ModelTimings(queue_ms=0,load_ms=0,inference_ms=0))
    with catalog.start_run(owner.token.kb_id,resolve_run(config,'qa')) as lease:
        dense=DenseSearch(catalog,lease.run.run_id,Provider(),backend)
        session=SourceSession(catalog,lease,H['ControlledMeter'](),dense=dense)
        first=session.search('immutable')
        assert catalog.get_run(lease.run.run_id).usage.searches==1
        opened=session.open(first.payload['items'][0]['source_ref'])
        assert opened.payload['items'][0]['text']==first.payload['items'][0]['text']
        second=session.search('immutable')
        assert second.payload['items'][0]['text']==first.payload['items'][0]['text']
        assert session.dense is dense and catalog.get_run(lease.run.run_id).usage.searches==2


def test_scope_cursor_budget_and_archive_errors_have_distinct_codes_and_call_ids(tmp_path):
    from agentic_rag.domain import ErrorCode
    catalog,lease,session,_,version,_,ref=H['fixture'](tmp_path,opens=4)
    try:
        token=session.issue_source(ref)
        for action,code in [(lambda:session.open('forged'),ErrorCode.SCOPE_MISMATCH),
                            (lambda:session.open(token,cursor='forged'),ErrorCode.INVALID_CURSOR)]:
            with pytest.raises(RagError) as caught:action()
            assert caught.value.error.code==code and caught.value.error.call_id
        path=catalog.archives._path(version.parsed_hash);path.write_bytes(b'bad archive')
        with pytest.raises(RagError) as caught:session.open(token)
        assert caught.value.error.code==ErrorCode.CHECKPOINT_INVALID and caught.value.error.call_id
        with pytest.raises(RagError):session.open(token)
        with pytest.raises(RagError) as caught:session.open(token)
        assert caught.value.error.code==ErrorCode.BUDGET_EXHAUSTED and caught.value.error.call_id
    finally:lease.close()


@pytest.mark.parametrize('token', ['x'*101, 123, {'token':'forged'}])
@pytest.mark.parametrize('kind', ['source','cursor'])
def test_malformed_opaque_capability_preserves_error_category(tmp_path,token,kind):
    from agentic_rag.domain import ErrorCode
    _,lease,session,_,_,_,ref=H['fixture'](tmp_path)
    try:
        source=session.issue_source(ref)
        with pytest.raises(RagError) as caught:
            session.open(token) if kind=='source' else session.open(source,cursor=token)
        assert caught.value.error.code==(ErrorCode.SCOPE_MISMATCH if kind=='source' else ErrorCode.INVALID_CURSOR)
        assert caught.value.error.stage==('source_scope' if kind=='source' else 'source_cursor')
        assert caught.value.error.call_id
    finally:lease.close()


def test_hundreds_of_sections_have_bounded_navigation_and_complete_traversal(tmp_path):
    raw=''.join(f'# Chapter {i}\nbody {i}\n' for i in range(240)).encode()
    catalog,lease,session,_,_,chunks,ref=H['fixture'](tmp_path,raw,context_tokens=2100,opens=300)
    try:
        token=session.issue_source(ref);section_id=None;parts=[];seen=set()
        while True:
            H['discard_window'](session)
            result=session.open(token,section_id=section_id)
            state=result.payload['navigation_status']
            assert len(result.payload['navigation'])==1 and len(result.text.encode())<=2100
            assert state['total_sections']==240 and state['position']==len(parts)
            identity=result.payload['section_id'];assert identity not in seen;seen.add(identity)
            assert not result.payload['has_more']
            parts.append(result.payload['items'][0]['text'])
            section_id=state['next_section_id']
            if section_id is None:break
        assert len(parts)==240 and ''.join(parts)==chunks.parsed.text
    finally:lease.close()


@pytest.mark.parametrize('suffix,anchor_kind',[('.md','middle'),('.txt','middle'),('.txt','last')])
def test_hit_anchored_first_window_then_before_and_after_cover_exactly(tmp_path,suffix,anchor_kind):
    raw=(('# Heading\n' if suffix=='.md' else '')+'repeated 中文 😀 words\n'*220).encode()
    catalog,lease,session,_,_,chunks,ref=H['fixture'](tmp_path,raw,suffix=suffix,context_tokens=2500,opens=200)
    try:
        section=chunks.parsed.sections[0]
        anchor=chunks.inputs[len(chunks.inputs)//2 if anchor_kind=='middle' else -1].chunk.spans[0]
        token=session.issue_source(ref,anchor_span=anchor)
        first=session.open(token)
        first_span=Span.model_validate(first.payload['returned_spans'][0])
        assert first_span.start==anchor.start and first_span.start>section.span.start
        assert first.payload['previous_cursor'] and first.payload['has_more']
        assert any(s['start']==section.span.start for s in first.payload['unread_spans'])
        spans=[first_span];work=[]
        if first.payload['next_cursor']:work.append(first.payload['next_cursor'])
        work.append(first.payload['previous_cursor'])
        # Follow each bounded range exactly once. A previous link is only used
        # from the first anchored window; subsequent continuation stays in-range.
        for cursor in work:
            while cursor:
                H['discard_window'](session)
                page=session.open(token,cursor=cursor)
                spans.append(Span.model_validate(page.payload['returned_spans'][0]))
                cursor=page.payload['next_cursor']
        ordered=sorted(spans,key=lambda s:s.start)
        assert ordered[0].start==section.span.start and ordered[-1].end==section.span.end
        assert all(a.end==b.start for a,b in zip(ordered,ordered[1:]))
        assert ''.join(chunks.parsed.text[s.start:s.end] for s in ordered)==chunks.parsed.text
        # Another repeated-text chunk has its own trustworthy anchor/capability.
        other=chunks.inputs[0].chunk.spans[0]
        other_token=session.issue_source(ref,anchor_span=other)
        with pytest.raises(RagError):session.open(other_token,cursor=first.payload['previous_cursor'])
        H['discard_window'](session)
        explicit=session.open(token,section_id=ref.section_id)
        assert explicit.payload['returned_spans'][0]['start']==section.span.start
    finally:lease.close()


def test_search_issues_last_chunk_anchor_without_model_offsets(tmp_path):
    raw=('重复 same sentence.\n'*300).encode()
    catalog,lease,session,_,version,chunks,ref=H['fixture'](tmp_path,raw,suffix='.txt',context_tokens=5000)
    try:
        H['dense_fixture'](session,chunks,version,ref)
        original=session.dense.search
        session.dense.search=lambda *args,**kwargs:{'hits':[original(*args,**kwargs)['hits'][-1]]}
        result=session.search('last chunk')
        token=result.payload['items'][0]['source_ref']
        opened=session.open(token)
        assert opened.payload['returned_spans'][0]['start']==chunks.inputs[-1].chunk.spans[0].start
        assert opened.payload['previous_cursor'] and opened.payload['unread_spans']
    finally:lease.close()
