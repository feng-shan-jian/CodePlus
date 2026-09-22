"""Generate an actual processed catalog using the exact installed R08 wheel."""
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID

from agentic_rag._schema import fingerprint
from agentic_rag.config import ProcessingSnapshot,RunConfiguration
from agentic_rag.ingestion import InputSelection,select_inputs,capture_inputs,process_inputs,read_processed
from agentic_rag.models import FrozenTokenizer
from agentic_rag.storage import Catalog

root,fixture,cache=map(Path,sys.argv[1:])
record=json.loads(fixture.read_text(encoding='utf-8'))
snapshot=ProcessingSnapshot.model_validate_json(json.dumps(record['snapshot']))
run=RunConfiguration.model_validate_json(json.dumps(record['run_configuration']))
catalog=Catalog(root/'old-data');kb=catalog.create_library('R08 historical fixture')
source=root/'old-source.md';source.write_bytes(b'# Old checkpoint\r\n\r\nImmutable content from the preceding installed revision.\r\n')
with catalog.begin_import(kb.kb_id,snapshot,select_inputs((InputSelection(path=str(source)),))) as owner:
    raw,=capture_inputs(catalog,owner)
    processed,=process_inputs(catalog,owner,FrozenTokenizer(snapshot.resolved_config.embedding,cache))
    source.unlink()
    outcome=read_processed(catalog,raw.batch_id,raw.entry.item_id)
report={'batch_id':str(raw.batch_id),'item_id':str(raw.entry.item_id),'snapshot_id':str(snapshot.snapshot_id),
    'processing':processed.model_dump(mode='json'),'snapshot':snapshot.model_dump(mode='json'),
    'run_identity':run.identity,'run_json':run.model_dump(mode='json'),
    'data_hashes':{str(path.relative_to(root/'old-data')).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in (root/'old-data').rglob('*') if path.is_file()}}
(root/'old-state.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'created':True,'config_fingerprint':snapshot.config_fingerprint,'run_identity':run.identity}))
