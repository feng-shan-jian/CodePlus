"""Actual interpreter exit at archive/precommit/postcommit processing boundaries."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import runpy
import sys
from uuid import UUID

from agentic_rag.ingestion import InputSelection, select_inputs, capture_inputs, process_inputs
from agentic_rag.storage import Catalog
from agentic_rag.storage import processing as store

mode,directory,kb,source,event=sys.argv[1:]
helper=runpy.run_path(str(Path(__file__).with_name('parsing_support.py')))
catalog=Catalog(directory)
owner=catalog.begin_import(UUID(kb),helper['snapshot'](),select_inputs((InputSelection(path=source),)))
raw,=capture_inputs(catalog,owner)
def terminate():
    Path(event).write_text(json.dumps({'pid':os.getpid(),'mode':mode,'batch_id':str(raw.batch_id),
        'item_id':str(raw.entry.item_id),'raw_hash':raw.raw.sha256}),encoding='utf-8')
    os._exit(91)
if mode=='orphan':
    put=catalog.archives.put;calls=0
    def hooked(*args,**kwargs):
        global calls
        obj=put(*args,**kwargs);calls+=1
        if calls==4:terminate()
        return obj
    catalog.archives.put=hooked
elif mode=='precommit':
    owned=catalog._owned
    @contextmanager
    def hooked(*args,**kwargs):
        with owned(*args,**kwargs) as connection:
            yield connection
            if connection.execute('SELECT count(*) FROM processing_items').fetchone()[0]:terminate()
    catalog._owned=hooked
else:
    persist=store.persist
    def hooked(*args,**kwargs):
        persist(*args,**kwargs)
        terminate()
    store.persist=hooked
process_inputs(catalog,owner,helper['tokenizer']())
raise AssertionError('exit boundary was not reached')
