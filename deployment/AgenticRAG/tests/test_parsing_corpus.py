"""Full 609-file runtime corpus through the production snapshot/parse/checkpoint path.

This driver extracts only corpus_paths from R01's runtime-input contract. It never
imports scoring, answers, gold, retrieval or question execution code.
"""
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import runpy
import re
import sys
import time

from agentic_rag.ingestion import InputSelection, select_inputs, capture_inputs, process_inputs, read_processed
from agentic_rag.ingestion.chunking import coverage
from agentic_rag.storage import Catalog

HELPER = runpy.run_path(str(Path(__file__).with_name('parsing_support.py')))


def test_all_609_frozen_corpus_files_have_exact_coverage_and_complete_tokens(tmp_path):
    package=Path(__file__).resolve().parents[1]
    boundary=runpy.run_path(str(package/'eval/runtime_inputs.py'))
    paths=boundary['load_runtime_inputs']()['corpus_paths']
    assert len(paths)==609
    tokenizer=HELPER['tokenizer']()
    catalog=Catalog(tmp_path/'corpus-data');kb=catalog.create_library('r08 corpus acceptance')
    snapshot=HELPER['snapshot']()
    manifest=select_inputs(tuple(InputSelection(path=p) for p in paths))
    report={'runtime_input_sha256':boundary['INPUT_SHA256'],'expected_files':609,
            'python':sys.executable,'cwd':str(Path.cwd()),'model_cache':str(HELPER['cache']()),
            'versions':{n:metadata.version(n) for n in ('pydantic','apsw','markdown-it-py','tokenizers')},
            'snapshot':snapshot.model_dump(mode='json'),'input_manifest_hash':manifest.identity,
            'files':[],'failures':[],'started_monotonic':time.monotonic()}
    try:
        with catalog.begin_import(kb.kb_id,snapshot,manifest) as owner:
            captured=capture_inputs(catalog,owner)
            processed=process_inputs(catalog,owner,tokenizer)
            by_document={i.document_id:i for i in processed}
            for path,raw in zip(paths,captured,strict=True):
                file={'path':Path(path).name,'capture_stage':raw.stage}
                try:
                    assert raw.stage=='captured',raw.error
                    original=catalog.archives.read(raw.raw.sha256)
                    assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==raw.raw.sha256
                    file.update(input_sha256=raw.raw.sha256,input_bytes=len(original))
                    item=by_document[raw.document_id]
                    assert item.stage=='chunked',item.error
                    checked,version,result=read_processed(catalog,raw.batch_id,raw.entry.item_id)
                    assert checked==item
                    parsed=result.parsed
                    assert parsed.text.strip() and result.inputs, 'frozen corpus file must contain indexable body'
                    assert parsed.text==original.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
                    # Independently check *every* canonical codepoint against raw bytes.
                    mapping=parsed.source_map
                    for index,char in enumerate(parsed.text):
                        start,end=mapping.byte_boundaries[index:index+2]
                        assert original[start:end].decode('utf-8').replace('\r\n','\n').replace('\r','\n')==char
                    chunks=[]
                    for entry in result.inputs:
                        span,=entry.chunk.spans
                        body=parsed.text[span.start:span.end]
                        sequence=tokenizer.document(body,entry.index_title)
                        assert sequence.token_count==entry.complete_embedding_tokens<=512
                        assert sequence.ids[-1]==151643
                        chunks.append({'start':span.start,'end':span.end,'text_hash':entry.chunk.text_hash,
                            'complete_embedding_tokens':sequence.token_count,'overlap_codepoints':entry.overlap_codepoints,
                            'overlap_tokens':entry.overlap_tokens,'split_structures':entry.split_structures})
                    audit=coverage(result)
                    assert audit['eligible_codepoints']==len(parsed.text), 'this frozen corpus has body in every section'
                    assert audit['covered_codepoints']==len(parsed.text) and audit['excluded_spans']==[], 'frozen corpus must have full canonical coverage'
                    for excluded in audit['excluded_spans']:
                        span=excluded['span']
                        excluded_text=parsed.text[span['start']:span['end']]
                        # Independent source grammar, not ChunkSet-derived eligibility.
                        without_headings=re.sub(r'(?m)^ {0,3}#{1,6}(?:[ \t]+[^\n]*)?(?:\n|$)', '', excluded_text)
                        assert not without_headings.strip(), 'excluded real corpus body text'
                    file.update(result='PASS',parsed_sha256=version.parsed_hash,source_map_sha256=version.source_map_hash,
                        chunk_set_sha256=next(a.sha256 for a in item.output_hashes if a.kind=='chunks'),
                        section_count=len(parsed.sections),block_count=len(parsed.blocks),chunk_count=len(chunks),
                        coverage=audit,max_complete_embedding_tokens=max((c['complete_embedding_tokens'] for c in chunks),default=0),
                        chunks=chunks)
                except Exception as exc:
                    file.update(result='FAIL',error=f'{type(exc).__name__}: {exc}')
                    report['failures'].append(file['path'])
                report['files'].append(file)
            owner.abandon()
        report.update(files_checked=len(report['files']),result='FAIL' if report['failures'] else 'PASS',
            elapsed_seconds=time.monotonic()-report.pop('started_monotonic'),
            total_chunks=sum(f.get('chunk_count',0) for f in report['files']),
            max_complete_embedding_tokens=max(f.get('max_complete_embedding_tokens',0) for f in report['files']))
        assert report['files_checked']==609 and not report['failures'],report['failures']
    finally:
        if os.environ.get('R08_CORPUS_REPORT'):
            Path(os.environ['R08_CORPUS_REPORT']).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
