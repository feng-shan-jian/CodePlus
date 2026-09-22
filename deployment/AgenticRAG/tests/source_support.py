"""Real archive/parser/SQLite fixtures; index transport is explicitly synthetic.

Empty/pure-heading fixtures seal metadata directly because these source-only
tests do not claim Milvus publication or semantic retrieval acceptance.
"""
import json
from pathlib import Path
import runpy
from uuid import uuid4

from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, resolve_run
from agentic_rag.domain import IndexState, KnowledgeRevision, RevisionMember, SourceRef
from agentic_rag.ingestion import InputSelection, select_inputs, capture_inputs, process_inputs, read_processed
from agentic_rag.sources import SourceSession
from agentic_rag.storage import Catalog

H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))


class ControlledMeter:
    """One test token per UTF-8 byte; explicitly not a production model meter."""
    identity='controlled-test-utf8-byte-unit-v1'
    def count(self,text):
        return len(text.encode('utf-8'))


def fixture(root,raw=b'# Heading\nA source body with repeated text.\n',*,suffix='.md',context_tokens=8000,opens=100,searches=100,total_tokens=1000000):
    root.mkdir(parents=True,exist_ok=True)
    catalog=Catalog(root/'data')
    source=root/('source'+suffix);source.write_bytes(raw)
    config=H['configuration'](catalog._directory.root)
    data=config.model_dump(mode='json')
    data['retrieval'].update(context_tokens=context_tokens)
    data['budgets']['qa'].update(opens=opens,searches=searches,total_tokens=total_tokens,
                              finish_reserve_tokens=100,duration_ms=3600000,finish_reserve_ms=1000)
    config=KnowledgeConfig.model_validate_json(json.dumps(data))
    kb=catalog.create_library('source fixture')
    owner=catalog.begin_import(kb.kb_id,ProcessingSnapshot.capture(uuid4(),config),select_inputs((InputSelection(path=str(source)),)))
    captured,=capture_inputs(catalog,owner)
    process_inputs(catalog,owner,H['HELPER']['tokenizer']())
    item,version,chunks=read_processed(catalog,owner.token.batch_id,captured.entry.item_id)
    revision=KnowledgeRevision(revision_id=uuid4(),kb_id=kb.kb_id,processing_snapshot_id=catalog.get_batch(owner.token.batch_id).processing_snapshot_id,
                               manifest_hash='a'*64,index_state=IndexState.PREPARING)
    member=RevisionMember(revision_id=revision.revision_id,document_id=version.document_id,document_version_id=version.document_version_id,
                          chunk_set_hash=next(x.sha256 for x in item.output_hashes if x.kind=='chunks'))
    catalog.add_candidate(owner,revision,(member,))
    with catalog._owned(owner) as connection:
        connection.execute("UPDATE revisions SET index_state='READY' WHERE revision_id=?",(str(revision.revision_id),))
        connection.execute('UPDATE libraries SET current_revision_id=? WHERE kb_id=?',(str(revision.revision_id),str(kb.kb_id)))
    owner.abandon()
    lease=catalog.start_run(kb.kb_id,resolve_run(config,'qa'))
    session=SourceSession(catalog,lease,ControlledMeter())
    ref=None
    if chunks.parsed.sections:
        ref=SourceRef(kb_id=kb.kb_id,revision_id=revision.revision_id,document_id=version.document_id,
                      document_version_id=version.document_version_id,section_id=chunks.parsed.sections[0].section_id)
    return catalog,lease,session,source,version,chunks,ref


def dense_fixture(session,chunks,version,ref):
    class Search:
        catalog=session.catalog
        run_id=session.run.run_id
        def search(self,query,**kwargs):
            if not query.strip(): raise ValueError('empty query')
            return {'hits':[{'chunk_id':str(i.chunk.chunk_id),'kb_id':str(ref.kb_id),'revision_id':str(ref.revision_id),
                'document_id':str(ref.document_id),'document_version_id':str(ref.document_version_id),'section_id':str(i.chunk.section_id),
                'text':chunks.parsed.text[i.chunk.spans[0].start:i.chunk.spans[0].end]} for i in chunks.inputs]}
    session.dense=Search()


def prepared(gateway,result,spans=None,*,purpose='explore',protocol='compat',request_id=None):
    from agentic_rag.domain import Span
    from agentic_rag.evidence import MappedSpan
    from uuid import UUID
    item=result.payload['items'][0]
    source_start=item['returned_spans'][0]['start']
    gateway.bind_tool_result(result,'tool-actual-1')
    spans=spans or (Span.model_validate(item['returned_spans'][0]),)
    parts=[item['text'][s.start-source_start:s.end-source_start] for s in spans]
    if protocol=='compat':
        body={'messages':[{'role':'tool','tool_call_id':'tool-actual-1','content':'wrapper:'+part+':tail'} for part in parts]}
        paths=[(('messages',i,'content'),('messages',i,'tool_call_id')) for i in range(len(parts))]
    elif protocol=='responses':
        body={'input':[{'type':'function_call_output','call_id':'tool-actual-1','output':'wrapper:'+part+':tail'} for part in parts]}
        paths=[(('input',i,'output'),('input',i,'call_id')) for i in range(len(parts))]
    else:
        body={'messages':[{'role':'user','content':[{'type':'tool_result','tool_use_id':'tool-actual-1',
              'content':[{'type':'text','text':'wrapper:'+part+':tail'} for part in parts]}]}]}
        paths=[(('messages',0,'content',0,'content',i,'text'),('messages',0,'content',0,'tool_use_id')) for i in range(len(parts))]
    mappings=tuple(MappedSpan(UUID(item['candidate_id']),s,path,Span(start=8,end=8+len(part)),callpath)
                   for s,part,(path,callpath) in zip(spans,parts,paths))
    raw=json.dumps(body,ensure_ascii=False).encode('utf-8')
    return gateway.prepare(raw,mappings,purpose=purpose,protocol=protocol,request_id=request_id),raw,mappings


def discard_window(session):
    """Controlled trusted-host crop fixture, never a model-facing operation."""
    from agentic_rag.evidence import DeliveryGateway
    gateway=DeliveryGateway(session)
    permit=gateway.prepare(b'{"messages":[]}',(),purpose='explore',protocol='compat')
    gateway.retain_prepared_window(permit)
    gateway.settle(permit,'not_sent')
