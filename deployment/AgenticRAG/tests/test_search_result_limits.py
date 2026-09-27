"""Each source search has its own return limits."""
from pathlib import Path
import runpy

import pytest

from agentic_rag.domain import RagError
from agentic_rag.sources import SourceSession

S=runpy.run_path(str(Path(__file__).with_name('source_support.py')))


def test_legacy_limits_default_and_run_configuration_hash_are_unchanged(tmp_path):
    catalog,lease,legacy,_,version,chunks,ref=S['fixture'](tmp_path,raw=b'# Source\n'+b'long original body. '*500,context_tokens=50000)
    try:
        frozen=lease.run.resolved_config.model_dump(mode='json')
        limited=SourceSession(catalog,lease,legacy.meter,search_result_chunks=2,search_result_upper=6000)
        assert legacy.search_result_chunks is None and legacy.search_result_upper is None
        assert lease.run.resolved_config.model_dump(mode='json')==frozen
        S['dense_fixture'](limited,chunks,version,ref)
        result=limited.search('original source')
        assert 0<len(result.payload['items'])<=2 and len(result.text.encode())<=6000
        assert any(result.payload['limited'].values())
        trace=limited.retrieval_trace(result.payload['call_id'])['context']
        assert trace['search_result_limits']=={'chunks':2,'meter_upper':6000}
        assert trace['fragment_allowance']==2
        assert trace['serialized_token_allowance']==6000
    finally:lease.close()


def test_metadata_too_large_for_local_cap_returns_error_without_candidates(tmp_path):
    catalog,lease,legacy,_,version,chunks,ref=S['fixture'](tmp_path,context_tokens=8000)
    try:
        limited=SourceSession(catalog,lease,legacy.meter,search_result_upper=1)
        S['dense_fixture'](limited,chunks,version,ref)
        with pytest.raises(RagError) as raised:limited.search('original source')
        assert raised.value.error.stage=='search_result_limit'
        assert limited.usage()['returned_tokens']==limited.usage()['returned_fragments']==0
        assert limited.usage()['searches']==1
        with catalog._db.transaction() as db:
            assert db.execute('SELECT count(*) FROM source_candidates').fetchone()==(0,)
            assert db.execute('SELECT count(*) FROM delivered_evidence').fetchone()==(0,)
    finally:lease.close()
