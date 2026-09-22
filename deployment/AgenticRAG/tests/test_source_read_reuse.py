"""Search-local source reads preserve each hit and run authorization boundary."""

from pathlib import Path
import runpy
from uuid import uuid4

import pytest

from agentic_rag.domain import ErrorCode, RagError, RunStatus
from agentic_rag import sources

H = runpy.run_path(str(Path(__file__).with_name('source_support.py')))


def fixture(tmp_path):
    result = H['fixture'](tmp_path, b'# A\nFirst telescope section.\n# B\nSecond ocean section.\n# C\nThird mountain section.\n',
                          context_tokens=20000)
    _,_,session,_,version,chunks,ref = result
    H['dense_fixture'](session,chunks,version,ref)
    assert len(chunks.inputs) == 3
    return result


def assert_failed_call(catalog, session, error):
    assert error.error.call_id
    with catalog._db.transaction() as db:
        assert db.execute('SELECT status FROM source_calls WHERE call_id=?',
                          (error.error.call_id,)).fetchone() == ('error',)
        assert db.execute('SELECT count(*) FROM source_candidates WHERE call_id=?',
                          (error.error.call_id,)).fetchone() == (0,)
    assert session.usage()['searches'] == 1
    assert session.usage()['returned_fragments'] == 0


def test_each_search_reads_one_complete_version_and_public_issuance_rereads(tmp_path, monkeypatch):
    catalog,lease,session,_,version,chunks,ref = fixture(tmp_path)
    reads, opens = [], []
    read = sources.read_ref
    path_open = Path.open
    archive_paths = {catalog.archives._path(value) for value in (
        version.raw_hash,version.parsed_hash,version.source_map_hash)}
    with catalog._db.transaction() as db:
        chunk_hash, = db.execute('SELECT chunk_set_hash FROM revision_members WHERE document_version_id=?',
                                 (str(version.document_version_id),)).fetchone()
    archive_paths.add(catalog.archives._path(chunk_hash))
    def counted_read(*args):
        reads.append(args[1])
        return read(*args)
    def counted_open(path, *args, **kwargs):
        if path in archive_paths:
            opens.append(path)
        return path_open(path, *args, **kwargs)
    monkeypatch.setattr(sources, 'read_ref', counted_read)
    monkeypatch.setattr(Path, 'open', counted_open)
    try:
        for count in (1,2):
            result = session.search('sections')
            assert len(result.candidate_ids) == 3
            assert {item['section_id'] for item in result.payload['items']} == {
                str(entry.chunk.section_id) for entry in chunks.inputs}
            assert len(reads) == count and len(opens) == 4 * count
            assert set(opens) == archive_paths
        session.issue_source(ref)
        assert len(reads) == 3 and len(opens) == 12
    finally:
        lease.close()


@pytest.mark.parametrize('damage', ['section', 'section_mismatch', 'chunk', 'text', 'revision', 'library', 'document', 'version'])
def test_later_hit_is_checked_even_when_first_hit_read_the_version(tmp_path, damage):
    catalog,lease,session,_,_,_,_ = fixture(tmp_path)
    original = session.dense.search
    def hits(*args, **kwargs):
        result = original(*args, **kwargs)
        hit = result['hits'][1]
        key = {'section':'section_id','chunk':'chunk_id','revision':'revision_id',
               'library':'kb_id','document':'document_id','version':'document_version_id'}.get(damage)
        if key:
            hit[key] = str(uuid4())
        elif damage == 'section_mismatch':
            hit['section_id'] = result['hits'][0]['section_id']
        else:
            hit['text'] += ' forged'
        return result
    session.dense.search = hits
    try:
        with pytest.raises(RagError) as caught:
            session.search('sections')
        assert_failed_call(catalog,session,caught.value)
    finally:
        lease.close()


@pytest.mark.parametrize('action', ['search', 'open', 'issue'])
def test_next_operation_rejects_archive_corrupted_after_search(tmp_path, action):
    catalog,lease,session,_,version,_,ref = fixture(tmp_path)
    try:
        result = session.search('sections')
        token = result.payload['items'][0]['source_ref']
        catalog.archives._path(version.raw_hash).write_bytes(b'corrupt after successful search')
        with pytest.raises(RagError) as caught:
            if action == 'search':
                session.search('sections')
            elif action == 'open':
                session.open(token)
            else:
                session.issue_source(ref)
        assert caught.value.error.code == ErrorCode.CHECKPOINT_INVALID
    finally:
        lease.close()


@pytest.mark.parametrize('boundary', ['issuance', 'result_commit'])
def test_successful_archive_read_does_not_replace_current_run_authorization(tmp_path, monkeypatch, boundary):
    catalog,lease,session,_,_,_,_ = fixture(tmp_path)
    if boundary == 'issuance':
        read = sources.read_ref
        def revoked(*args):
            result = read(*args)
            lease.finish(RunStatus.COMPLETED, 'finished')
            return result
        monkeypatch.setattr(sources, 'read_ref', revoked)
    else:
        commit = session._commit_result
        def revoked(*args):
            lease.finish(RunStatus.COMPLETED, 'finished')
            return commit(*args)
        monkeypatch.setattr(session, '_commit_result', revoked)
    try:
        with pytest.raises(RagError) as caught:
            session.search('sections')
        assert caught.value.error.code == ErrorCode.SCOPE_MISMATCH
        assert_failed_call(catalog,session,caught.value)
    finally:
        lease.close()
