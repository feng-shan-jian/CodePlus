"""Operation-local manifest reuse still checks current rows and archive bytes."""

from pathlib import Path
import runpy
from uuid import UUID, uuid4

import pytest

from agentic_rag.domain import RagError, ErrorCode
from agentic_rag.ingestion import (InputSelection, select_inputs, capture_inputs, process_inputs,
                                  read_input, read_processed, inspect_recovery, continue_recovery)
from agentic_rag.ingestion.capture import _read_input
from agentic_rag.ingestion.records import InputCheckpoint
from agentic_rag.storage import Catalog, OwnerToken
from agentic_rag.storage.inputs import _InputRead
from agentic_rag.storage import processing as processing_store

H = runpy.run_path(str(Path(__file__).with_name('parsing_support.py')))


def captured(tmp_path, count=3, missing=False):
    catalog = Catalog(tmp_path / 'data')
    kb = catalog.create_library('input read scope').kb_id
    paths = [tmp_path / f'{index}.md' for index in range(count)]
    for index, path in enumerate(paths):
        path.write_text(f'# Document {index}\nImmutable telescope source {index}.\n', encoding='utf-8')
    selections = paths + ([tmp_path / 'missing.md'] if missing else [])
    owner = catalog.begin_import(kb, H['snapshot'](), select_inputs(tuple(InputSelection(path=str(p)) for p in selections)))
    items = capture_inputs(catalog, owner)
    for path in paths:
        path.unlink()
    return catalog, owner, items


@pytest.mark.parametrize('existing', [False, True])
def test_processing_reads_whole_input_set_once_for_new_and_existing_checkpoints(tmp_path, monkeypatch, existing):
    catalog, owner, items = captured(tmp_path, missing=True)
    tokenizer = H['tokenizer']()
    try:
        if existing:
            expected = process_inputs(catalog, owner, tokenizer)
        scans, parses = [], []
        get_items = catalog.get_input_items
        parse = InputCheckpoint.model_validate_json
        def scan(batch_id):
            scans.append(batch_id)
            return get_items(batch_id)
        def parsed(value):
            parses.append(value)
            return parse(value)
        monkeypatch.setattr(catalog, 'get_input_items', scan)
        monkeypatch.setattr(InputCheckpoint, 'model_validate_json', staticmethod(parsed))
        actual = process_inputs(catalog, owner, tokenizer)
        assert len(actual) == 3 and all(item.stage == 'chunked' for item in actual)
        if existing:
            assert actual == expected
        assert scans == [owner.token.batch_id]
        assert len(parses) == len(items) + 3  # One full set plus one current row per captured item.
    finally:
        owner.abandon()


def test_input_scope_rejects_foreign_catalog_batch_item_and_unverified_values(tmp_path):
    catalog, owner, items = captured(tmp_path, count=1)
    try:
        scope = _InputRead(catalog, owner.token.batch_id)
        raw = items[0]
        with pytest.raises(RagError):
            _read_input(Catalog(tmp_path / 'data'), raw.batch_id, raw.entry.item_id, _inputs=scope)
        with pytest.raises(RagError):
            _read_input(catalog, uuid4(), raw.entry.item_id, _inputs=scope)
        with pytest.raises(RagError):
            _read_input(catalog, raw.batch_id, uuid4(), _inputs=scope)
        for unverified in (object(), raw, items):
            with pytest.raises(RagError):
                _read_input(catalog, raw.batch_id, raw.entry.item_id, _inputs=unverified)
    finally:
        owner.abandon()


@pytest.mark.parametrize('existing', [False, True])
def test_complete_document_encoding_scans_manifest_once_and_keeps_linear_row_checks(tmp_path, monkeypatch, existing):
    from agentic_rag.ingestion.encoding import encode_documents
    helper = runpy.run_path(str(Path(__file__).with_name('test_mutations.py')))
    catalog, owner, items = captured(tmp_path)
    model = helper['Model']()
    try:
        process_inputs(catalog, owner, H['tokenizer']())
        if existing:
            encode_documents(catalog, owner, model)
            assert len(model.calls) == len(items)
        before = len(model.calls)
        scans, parses = [], []
        get_items = catalog.get_input_items
        parse = InputCheckpoint.model_validate_json
        def scan(batch_id):
            scans.append(batch_id)
            return get_items(batch_id)
        def parsed(value):
            parses.append(value)
            return parse(value)
        monkeypatch.setattr(catalog, 'get_input_items', scan)
        monkeypatch.setattr(InputCheckpoint, 'model_validate_json', staticmethod(parsed))
        calls = encode_documents(catalog, owner, model)
        assert scans == [owner.token.batch_id]
        assert len(items) <= len(parses) <= 4 * len(items)
        assert len(calls) == len(model.calls) - before == (0 if existing else len(items))
    finally:
        owner.abandon()


def test_current_terminal_input_row_is_rechecked_inside_operation(tmp_path):
    catalog, owner, items = captured(tmp_path, count=1)
    try:
        process_inputs(catalog, owner, H['tokenizer']())
        raw = items[0]
        scope = _InputRead(catalog, raw.batch_id)
        assert _read_input(catalog, raw.batch_id, raw.entry.item_id, _inputs=scope)
        # Controlled persisted corruption; normal writers cannot update this row.
        changed = raw.model_copy(update={'change':'source_changed'})
        with catalog._db.transaction(write=True) as db:
            db.execute('DROP TRIGGER immutable_input_results_update')
            db.execute('UPDATE input_results SET result_json=? WHERE item_id=?',
                       (changed.model_dump_json(), str(raw.entry.item_id)))
        with pytest.raises(RagError, match='changed during the operation'):
            _read_input(catalog, raw.batch_id, raw.entry.item_id, _inputs=scope)
        with pytest.raises(RagError, match='changed during the operation'):
            processing_store.read(catalog, raw.batch_id, raw.entry.item_id, _inputs=scope)
    finally:
        owner.abandon()


def test_archives_are_reread_inside_scope_and_on_next_public_call(tmp_path):
    catalog, owner, items = captured(tmp_path, count=1)
    try:
        process_inputs(catalog, owner, H['tokenizer']())
        raw = items[0]
        scope = _InputRead(catalog, raw.batch_id)
        assert _read_input(catalog, raw.batch_id, raw.entry.item_id, _inputs=scope)
        catalog.archives._path(raw.raw.sha256).write_bytes(b'controlled archive corruption')
        for call in (
            lambda:_read_input(catalog, raw.batch_id, raw.entry.item_id, _inputs=scope),
            lambda:read_input(catalog, raw.batch_id, raw.entry.item_id),
            lambda:read_processed(catalog, raw.batch_id, raw.entry.item_id),
        ):
            with pytest.raises(RagError) as caught:
                call()
            assert caught.value.error.code == ErrorCode.CHECKPOINT_INVALID
    finally:
        owner.abandon()


@pytest.mark.parametrize('damage', ['invalid_manifest', 'manifest_hash', 'item_order', 'foreign_batch'])
def test_next_public_operation_reauthenticates_manifest_and_complete_set(tmp_path, damage):
    catalog, owner, items = captured(tmp_path, count=2)
    try:
        scope = _InputRead(catalog, owner.token.batch_id)
        assert _read_input(catalog, items[0].batch_id, items[0].entry.item_id, _inputs=scope)
        with catalog._db.transaction(write=True) as db:
            if damage == 'invalid_manifest':
                # Corrupt the manifest body without violating inbound foreign keys.
                db.execute('DROP TRIGGER immutable_input_manifests_update')
                db.execute("UPDATE input_manifests SET request_json='{}' WHERE batch_id=?", (str(owner.token.batch_id),))
            elif damage == 'manifest_hash':
                db.execute('DROP TRIGGER immutable_input_manifests_update')
                db.execute('UPDATE input_manifests SET request_hash=? WHERE batch_id=?', ('f'*64, str(owner.token.batch_id)))
            elif damage == 'item_order':
                db.execute('DROP TRIGGER immutable_input_items_update')
                db.execute('UPDATE input_items SET ordinal=ordinal+100 WHERE batch_id=?', (str(owner.token.batch_id),))
                db.execute('UPDATE input_items SET ordinal=101-ordinal WHERE batch_id=?', (str(owner.token.batch_id),))
            else:
                db.execute('DROP TRIGGER immutable_input_results_update')
                changed = items[0].model_copy(update={'batch_id':uuid4()})
                db.execute('UPDATE input_results SET result_json=? WHERE item_id=?',
                           (changed.model_dump_json(), str(items[0].entry.item_id)))
        for call in (lambda:read_input(catalog, items[0].batch_id, items[0].entry.item_id),
                     lambda:process_inputs(catalog, owner, H['tokenizer']())):
            with pytest.raises((RagError, ValueError)):
                call()
    finally:
        owner.abandon()


def test_continue_revalidates_after_a_successful_inspection_before_runtime(tmp_path):
    from agentic_rag.config import ProcessingSnapshot
    from agentic_rag.ingestion import begin_changes, process_changes
    helper = runpy.run_path(str(Path(__file__).with_name('test_mutations.py')))
    catalog = Catalog(tmp_path / 'data')
    config = helper['HELPER']['configuration'](tmp_path / 'data')
    kb = catalog.create_library('recovery input read').kb_id
    path = tmp_path / 'source.md'
    path.write_text('# Original\nImmutable telescope recovery source.\n', encoding='utf-8')
    owner = begin_changes(catalog, kb, ProcessingSnapshot.capture(uuid4(), config), (InputSelection(path=str(path)),))
    try:
        process_changes(catalog, owner, H['tokenizer'](), helper['Model']())
    finally:
        owner.close()
    plan = inspect_recovery(catalog, owner.token.batch_id)
    assert plan['can_continue'] and plan['items'][0]['checkpoint'] == 'encoded'
    raw, = catalog.get_input_items(owner.token.batch_id)
    catalog.archives._path(raw.raw.sha256).write_bytes(b'corrupt after inspection')
    expected = OwnerToken(**{k:UUID(v) if k != 'owner_epoch' else v for k,v in plan['expected'].items()})
    with pytest.raises(RagError):
        continue_recovery(catalog, expected, runtime_factory=lambda _:pytest.fail('must revalidate before opening runtime'))
    assert catalog.get_library(kb).current_revision_id is None
    assert catalog.get_library(kb).pending_mutation_id == owner.token.batch_id
