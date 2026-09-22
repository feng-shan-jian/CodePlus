"""Fresh process archive/citation read; deliberately never opens Milvus/GPU."""
import json
import sys
from uuid import UUID

from agentic_rag.citations import open_citation
from agentic_rag.storage import Catalog

if __name__=='__main__':
    catalog=Catalog(sys.argv[1])
    citation=open_citation(catalog,UUID(sys.argv[2]))
    forbidden=[name for name in ('pymilvus','torch','transformers','codeplus') if name in sys.modules]
    assert not forbidden
    print(json.dumps({'citation':citation,'forbidden_modules_loaded':forbidden},ensure_ascii=False))
