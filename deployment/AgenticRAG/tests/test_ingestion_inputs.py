"""Manual ranges, identity, published-baseline comparison and raw-only capture."""

import io
import json
from pathlib import Path
import runpy
from uuid import uuid4

import pytest

from agentic_rag.config import ProcessingSnapshot
from agentic_rag.domain import DocumentVersion, IndexState, KnowledgeRevision, RevisionMember, RagError
from agentic_rag.ingestion import InputSelection, capture_inputs, read_input, select_inputs, verify_inputs
from agentic_rag.storage import Catalog

HELPER = runpy.run_path(str(Path(__file__).with_name('storage_process_helper.py')))


def new_catalog(tmp_path):
    catalog = Catalog(tmp_path / 'data')
    return catalog, catalog.create_library('inputs'), HELPER['snapshot']()


def ingest(catalog, kb, snap, *paths, document_id=None):
    manifest = select_inputs(tuple(InputSelection(path=str(p), document_id=document_id) for p in paths))
    with catalog.begin_import(kb.kb_id, snap, manifest) as owner:
        items = capture_inputs(catalog, owner)
        owner.abandon()
    return items


def synthetic_publish(catalog, kb, snapshot, path):
    """Explicit synthetic metadata fixture, NOT parsed data or Milvus publication."""
    manifest = select_inputs((InputSelection(path=str(path)),))
    with catalog.begin_import(kb.kb_id, snapshot, manifest) as owner:
        item, = capture_inputs(catalog, owner)
        parsed = catalog.archives.put(io.BytesIO(b'SYNTHETIC PARSED FIXTURE'))
        mapping = catalog.archives.put(io.BytesIO(b'SYNTHETIC MAP FIXTURE'))
        version = DocumentVersion(document_version_id=uuid4(), document_id=item.document_id,
            raw_hash=item.raw.sha256, parsed_hash=parsed.sha256, source_map_hash=mapping.sha256,
            parser_fingerprint='f'*64, source_uri=item.raw.source_uri, captured_at=item.raw.captured_at,
            source_metadata=item.raw.metadata)
        catalog.add_version(owner, version)
        revision = KnowledgeRevision(revision_id=uuid4(), kb_id=kb.kb_id, manifest_hash=manifest.identity,
            base_revision_id=catalog.get_batch(owner.token.batch_id).base_revision_id,
            processing_snapshot_id=snapshot.snapshot_id, index_state=IndexState.PREPARING)
        catalog.add_candidate(owner, revision, (RevisionMember(revision_id=revision.revision_id,
            document_id=item.document_id, document_version_id=version.document_version_id, chunk_set_hash='f'*64),))
        with catalog._db.transaction(write=True) as connection:
            connection.execute("UPDATE revisions SET index_state='READY' WHERE revision_id=?", (str(revision.revision_id),))
            connection.execute('UPDATE libraries SET current_revision_id=? WHERE kb_id=?', (str(revision.revision_id),str(kb.kb_id)))
        owner.abandon()
    return item, version, revision


def test_frozen_recursive_order_errors_and_missing_files_never_delete(tmp_path):
    catalog, kb, snap = new_catalog(tmp_path)
    root = tmp_path / 'selected'
    (root / 'b-dir').mkdir(parents=True)
    for name in ('z.txt', 'a.md', 'b-dir/inside.txt', '.hidden.txt', 'bad.pdf'):
        (root / name).write_text(name, encoding='utf-8')
    manifest = select_inputs((InputSelection(path=str(root)),))
    assert [Path(e.requested_path).name for e in manifest.entries] == ['.hidden.txt','a.md','inside.txt','bad.pdf','z.txt']
    assert manifest.entries[3].error and 'unsupported' in manifest.entries[3].error.message
    (root / 'new.txt').write_bytes(b'not in original batch')
    with catalog.begin_import(kb.kb_id, snap, manifest) as owner:
        items = capture_inputs(catalog, owner)
        assert sum(i.stage == 'captured' for i in items) == 4
        assert catalog.get_input_manifest(owner.token.batch_id) == manifest
        assert catalog.get_batch(owner.token.batch_id).input_manifest_hash == manifest.identity
        doc = items[0].document_id
        owner.abandon()
    (root / '.hidden.txt').unlink()
    ingest(catalog, kb, snap, root)
    assert catalog.get_document(doc).document_id == doc
    assert catalog.get_library(kb.kb_id).current_revision_id is None
    with catalog._db.transaction() as connection:
        assert connection.execute('SELECT count(*) FROM document_versions').fetchone() == (0,)


def test_same_path_identity_and_unpublished_attempt_is_not_unchanged(tmp_path):
    catalog, kb, snap = new_catalog(tmp_path)
    path = tmp_path / 'one.txt'
    path.write_bytes(b'first')
    one, = ingest(catalog, kb, snap, path)
    again, = ingest(catalog, kb, snap, path)
    assert again.document_id == one.document_id and again.change == 'new'
    path.write_bytes(b'second')
    changed, = ingest(catalog, kb, snap, path)
    assert changed.document_id == one.document_id and changed.change == 'new'
    assert read_input(catalog, one.batch_id, one.entry.item_id) == b'first'


def test_same_bytes_different_names_directories_and_default_move_are_distinct(tmp_path):
    catalog, kb, snap = new_catalog(tmp_path)
    paths = [tmp_path / 'a' / 'same.md', tmp_path / 'b' / 'same.md']
    for p in paths:
        p.parent.mkdir()
        p.write_bytes(b'identical')
    a, b = ingest(catalog, kb, snap, *paths)
    assert a.document_id != b.document_id and a.raw.sha256 == b.raw.sha256
    moved = tmp_path / 'moved.md'
    paths[0].rename(moved)
    new, = ingest(catalog, kb, snap, moved)
    assert new.document_id not in (a.document_id, b.document_id)
    assert catalog.get_document(a.document_id).source_key == a.raw.source_key


def test_explicit_move_keeps_identity_history_and_reports_source_change(tmp_path):
    catalog, kb, snap = new_catalog(tmp_path)
    path = tmp_path / 'before.txt'
    path.write_bytes(b'historical')
    item, version, revision = synthetic_publish(catalog, kb, snap, path)
    moved = tmp_path / 'after.txt'
    path.rename(moved)
    updated, = ingest(catalog, kb, snap, moved, document_id=item.document_id)
    assert updated.document_id == item.document_id and updated.change == 'source_changed'
    assert catalog.get_document(item.document_id).source_key == updated.raw.source_key
    assert catalog.get_version(version.document_version_id).source_uri == version.source_uri
    assert catalog.get_library(kb.kb_id).current_revision_id == revision.revision_id


def test_conflicts_cross_library_repeated_paths_and_document_are_file_errors(tmp_path):
    catalog, kb, snap = new_catalog(tmp_path)
    first, second = tmp_path/'first.txt', tmp_path/'second.txt'
    first.write_bytes(b'1'); second.write_bytes(b'2')
    a, b = ingest(catalog, kb, snap, first, second)
    collision, = ingest(catalog, kb, snap, second, document_id=a.document_id)
    assert collision.stage == 'failed' and 'another document' in collision.error.message
    other = catalog.create_library('other')
    cross, = ingest(catalog, other, HELPER['snapshot'](), first, document_id=a.document_id)
    assert cross.stage == 'failed' and 'this library' in cross.error.message
    repeated = ingest(catalog, kb, snap, first, first)
    assert all(i.stage == 'failed' and 'duplicate' in i.error.message for i in repeated)
    third = tmp_path/'third.txt'; third.write_bytes(b'3')
    manifest = select_inputs((InputSelection(path=str(first)), InputSelection(path=str(third), document_id=a.document_id)))
    with catalog.begin_import(kb.kb_id, snap, manifest) as owner:
        assert all(i.stage == 'failed' and 'same document' in i.error.message for i in capture_inputs(catalog, owner))
        owner.abandon()
    assert catalog.get_document(b.document_id).source_key == b.raw.source_key


@pytest.mark.parametrize('difference,expected,rebuild', [('none','unchanged',False), ('raw','content_changed',False),
    ('parser','encoding_changed',True), ('chunker','encoding_changed',True), ('index','index_changed',False),
    ('budget','unchanged',False)])
def test_comparison_uses_published_member_and_processing_identity(tmp_path, difference, expected, rebuild):
    catalog, kb, snap = new_catalog(tmp_path)
    path = tmp_path / 'doc.md'; path.write_bytes(b'original')
    original, _, _ = synthetic_publish(catalog, kb, snap, path)
    values = snap.resolved_config.model_dump(mode='json')
    if difference == 'raw':
        path.write_bytes(b'changed')
    elif difference == 'parser':
        values['processing']['parser']['version'] = 'unimplemented-test-parser-2'
    elif difference == 'chunker':
        values['processing']['chunker']['max_tokens'] += 1
    elif difference == 'index':
        values['processing']['index']['nlist'] += 1
    elif difference == 'budget':
        values['budgets']['qa']['searches'] += 1
    config = type(snap.resolved_config).model_validate_json(json.dumps(values))
    target = ProcessingSnapshot.capture(uuid4(), config)
    item, = ingest(catalog, kb, target, path)
    assert item.change == expected and item.requires_rebuild_confirmation is rebuild
    assert item.document_id == original.document_id
    frozen, checked = verify_inputs(Catalog(catalog._directory.root), item.batch_id)
    assert frozen == target and checked == (item,)


def test_source_changes_after_selection_fail_before_copy(tmp_path):
    catalog, kb, snap = new_catalog(tmp_path)
    path = tmp_path/'test.txt'; path.write_bytes(b'old')
    manifest = select_inputs((InputSelection(path=str(path)),))
    path.write_bytes(b'new')
    with catalog.begin_import(kb.kb_id, snap, manifest) as owner:
        item, = capture_inputs(catalog, owner)
        assert item.stage == 'failed' and item.error.code.value == 'SOURCE_CHANGED'
        owner.abandon()
    with pytest.raises(RagError, match='no complete'):
        read_input(catalog, item.batch_id, item.entry.item_id)


def test_new_document_still_checks_library_encoding_rebuild_boundary(tmp_path):
    catalog,kb,snap=new_catalog(tmp_path)
    old=tmp_path/'old.txt';old.write_bytes(b'old')
    synthetic_publish(catalog,kb,snap,old)
    values=snap.resolved_config.model_dump(mode='json')
    values['processing']['parser']['version']='unimplemented-test-parser-2'
    target=ProcessingSnapshot.capture(uuid4(),type(snap.resolved_config).model_validate_json(json.dumps(values)))
    new=tmp_path/'new.txt';new.write_bytes(b'new')
    item,=ingest(catalog,kb,target,new)
    assert item.change=='new' and item.requires_rebuild_confirmation is True


def test_failed_update_preserves_published_base_and_historical_raw(tmp_path,monkeypatch):
    import errno
    catalog,kb,snap=new_catalog(tmp_path)
    path=tmp_path/'published.txt';path.write_bytes(b'old published original')
    old,version,revision=synthetic_publish(catalog,kb,snap,path)
    path.write_bytes(b'failed new original')
    def full(*a,**kw): raise OSError(errno.ENOSPC,'injected disk full')
    monkeypatch.setattr(catalog.archives,'put',full)
    item,=ingest(catalog,kb,snap,path)
    assert item.stage=='failed' and item.document_id==old.document_id
    assert catalog.get_library(kb.kb_id).current_revision_id==revision.revision_id
    assert catalog.get_version(version.document_version_id)==version
    assert read_input(catalog,old.batch_id,old.entry.item_id)==b'old published original'


@pytest.mark.parametrize('content',[b'', b'\xef\xbb\xbf# heading\r\n\xe4\xb8\xad\xe6\x96\x87\r\n', b'\xff\xfe\x00\x01'])
def test_capture_preserves_every_original_byte_without_claiming_parse(tmp_path,content):
    catalog,kb,snap=new_catalog(tmp_path)
    path=tmp_path/'raw.txt';path.write_bytes(content)
    item,=ingest(catalog,kb,snap,path)
    assert item.stage=='captured' and read_input(catalog,item.batch_id,item.entry.item_id)==content


def test_scan_permission_error_is_an_entry_and_no_sql_is_held(tmp_path, monkeypatch):
    from agentic_rag.ingestion import selection
    root = tmp_path/'denied'; root.mkdir()
    def denied(_):
        raise PermissionError('injected directory permission denied')
    monkeypatch.setattr(selection.os, 'scandir', denied)
    manifest = select_inputs((InputSelection(path=str(root)), InputSelection(path=str(tmp_path/'missing.txt'))))
    assert len(manifest.entries) == 2
    assert 'PermissionError' in manifest.entries[0].error.message
    assert 'FileNotFoundError' in manifest.entries[1].error.message


def test_manifest_registration_rolls_back_entire_batch_on_insert_failure(tmp_path, monkeypatch):
    import agentic_rag.storage.inputs as inputs
    catalog, kb, snap = new_catalog(tmp_path)
    path = tmp_path/'x.pdf'; path.write_bytes(b'x')
    manifest = select_inputs((InputSelection(path=str(path)),))
    def fail(*_):
        raise RuntimeError('injected registration failure')
    monkeypatch.setattr(inputs, '_insert_result', fail)
    with pytest.raises(RuntimeError):
        catalog.begin_import(kb.kb_id, snap, manifest)
    assert catalog.get_library(kb.kb_id).pending_mutation_id is None
    with catalog._db.transaction() as connection:
        for table in ('mutation_batches','input_manifests','input_items','processing_snapshots'):
            assert connection.execute(f'SELECT count(*) FROM {table}').fetchone() == (0,)
