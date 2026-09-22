"""Real one-row indexes, without padding/duplication; normal zero-token BM25."""
import json
import os
from pathlib import Path
import runpy
import pytest

from agentic_rag.storage import Catalog,publication
from agentic_rag.indexes.milvus import MilvusRevisionIndex

H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
pytestmark=pytest.mark.skipif(os.environ.get('R10_REAL')!='1',reason='owned real Milvus required')


@pytest.mark.parametrize('text',['Telescope ocean immutable source.','!!! ??? ...','🫨 🫨 🫨'],ids=['words','punctuation','unicode'])
def test_real_single_chunk_publish_and_analyzer_empty(tmp_path,text):
    report={'scope':'one real production index row; synthetic normalized 1024D protocol vector','text':text}
    catalog=Catalog(tmp_path/'data');owner,config=H['processed'](catalog,tmp_path/'short.txt',text=text)
    prepared,artifact,expected=H['synthetic_encoded'](catalog,owner)
    backend=MilvusRevisionIndex(config.storage,catalog)
    try:
        assert len(expected)==1
        backend.create(artifact,owner)
        backend.insert(artifact,[{**expected[0],'dense':H['VECTOR']}],owner)
        report['finalize']=backend.finalize(artifact,1,owner)
        report['validation']=publication.validate(catalog,owner,prepared.revision_id,backend)
        report['receipt']=publication.publish(catalog,owner,prepared.revision_id)
        assert report['validation']['rows_checked']==1
        assert all(i['indexed_rows']==i['total_rows']==1 for i in report['validation']['indexes'].values())
        report['result']='PASS'
    finally:
        owner.close();backend.client.drop_collection(backend._name(artifact));backend.close()
        if os.environ.get('R10_SMALL_REPORT_DIR'):
            label='words' if text.startswith('Telescope') else 'punctuation' if text.startswith('!') else 'unicode'
            Path(os.environ['R10_SMALL_REPORT_DIR'],f'R10-small-{label}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=list)+'\n',encoding='utf-8')
