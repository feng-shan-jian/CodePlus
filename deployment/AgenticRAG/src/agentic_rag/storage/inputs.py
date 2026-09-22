"""Short owner-checked input transactions; source IO belongs to ingestion."""

from collections import Counter
from uuid import UUID, uuid4

from ..domain import ErrorCode, ErrorInfo
from ..ingestion.records import InputCheckpoint, InputManifest
from . import ownership
from .paths import failure


def _identity_error(message):
    return ErrorInfo(code=ErrorCode.IDENTITY_MISMATCH, stage='input_manifest', message=message)


def begin_import(catalog, kb_id, snapshot, manifest, *, batch_id=None, _ordinary=None):
    if not manifest.selections and not (_ordinary and _ordinary['deletions']):
        raise failure('import requires selections or explicit deletions')
    def register(connection, batch):
        if _ordinary is not None:
            from ..ingestion.mutations import register_request
            register_request(catalog, connection, batch, snapshot, manifest, _ordinary)
        connection.execute('INSERT INTO input_manifests VALUES(?,?,?,?,?)',
                           (str(batch.batch_id), str(kb_id), manifest.identity, manifest.model_dump_json(), batch.owner_epoch))
        plans = []
        for entry in manifest.entries:
            document_id, error = entry.document_id, entry.error
            existing = connection.execute('SELECT document_id FROM documents WHERE kb_id=? AND source_key=?',
                                          (str(kb_id), entry.source_key)).fetchone() if error is None else None
            if error is None and document_id is not None:
                document = connection.execute('SELECT kb_id FROM documents WHERE document_id=?', (str(document_id),)).fetchone()
                if document != (str(kb_id),):
                    error = _identity_error('explicit update must select an existing document in this library')
                elif existing is not None and existing[0] != str(document_id):
                    error = _identity_error('new source path already belongs to another document')
            elif error is None:
                document_id = UUID(existing[0]) if existing else uuid4()
            plans.append((entry, document_id, error, existing is None and entry.document_id is None))
        counts = Counter(doc for _, doc, error, _ in plans if error is None)
        for ordinal, (entry, doc, error, new) in enumerate(plans):
            if error is None and counts[doc] > 1:
                error = _identity_error('multiple inputs select the same document in one batch')
            if error is not None:
                # A bad explicit identity must not create a cross-library FK.
                doc = None
            elif new:
                connection.execute('INSERT INTO documents VALUES(?,?,?,?,?)',
                                   (str(doc), str(kb_id), entry.source_key, 1, entry.metadata.original_name))
            base = connection.execute('SELECT document_version_id FROM revision_members WHERE revision_id=? AND document_id=?',
                                      (str(batch.base_revision_id), str(doc))).fetchone() if doc else None
            checkpoint = InputCheckpoint(batch_id=batch.batch_id, entry=entry, document_id=doc,
                            base_version_id=UUID(base[0]) if base else None, capture_epoch=batch.owner_epoch,
                            stage='failed' if error else 'pending', error=error)
            connection.execute('INSERT INTO input_items VALUES(?,?,?,?,?,?,?)',
                               (str(entry.item_id), str(batch.batch_id), str(kb_id), ordinal,
                                str(doc) if doc else None, base[0] if base else None, checkpoint.model_dump_json()))
            if error:
                _insert_result(connection, kb_id, checkpoint)
    return ownership.begin(catalog._db, kb_id, batch_id or uuid4(), snapshot, manifest.identity, _register=register)


def read_manifest(catalog, batch_id):
    with catalog._db.transaction() as connection:
        batch = ownership.read_batch(connection, batch_id)
        row = connection.execute('SELECT request_hash,request_json FROM input_manifests WHERE batch_id=?', (str(batch_id),)).fetchone()
    if row is None:
        raise failure('batch has no persisted input manifest; original input range cannot be recovered')
    manifest = InputManifest.model_validate_json(row[1])
    if manifest.identity != row[0] or row[0] != batch.input_manifest_hash:
        raise failure('input manifest fingerprint mismatch')
    return manifest


def read_items(catalog, batch_id):
    manifest = read_manifest(catalog, batch_id)
    with catalog._db.transaction() as connection:
        rows = connection.execute('SELECT i.item_id,COALESCE(r.result_json,i.initial_json) FROM input_items i '
                                  'LEFT JOIN input_results r ON r.item_id=i.item_id WHERE i.batch_id=? ORDER BY i.ordinal',
                                  (str(batch_id),)).fetchall()
    items = tuple(InputCheckpoint.model_validate_json(row[1]) for row in rows)
    if tuple(i.entry for i in items) != manifest.entries or any(i.batch_id != batch_id for i in items):
        raise failure('input checkpoint set differs from immutable manifest')
    return items


class _InputRead:
    """One operation's validated manifest; each selected SQL row is reread."""

    def __init__(self, catalog, batch_id):
        self.catalog, self.batch_id = catalog, batch_id
        self.items = catalog.get_input_items(batch_id)
        self._items = {item.entry.item_id:item for item in self.items}

    def read(self, catalog, batch_id, item_id):
        if catalog is not self.catalog or batch_id != self.batch_id:
            raise failure('input read belongs to another catalog or batch')
        expected = self._items.get(item_id)
        if expected is None:
            return None
        with catalog._db.transaction() as connection:
            row = connection.execute('SELECT COALESCE(r.result_json,i.initial_json) FROM input_items i '
                'LEFT JOIN input_results r ON r.item_id=i.item_id WHERE i.item_id=? AND i.batch_id=?',
                (str(item_id), str(batch_id))).fetchone()
        if row is None or InputCheckpoint.model_validate_json(row[0]) != expected:
            raise failure('input checkpoint changed during the operation')
        return expected


def _read_item(catalog, batch_id, item_id, operation=None):
    if operation is None:
        return next((item for item in catalog.get_input_items(batch_id) if item.entry.item_id == item_id), None)
    if type(operation) is not _InputRead:
        raise failure('input read requires the internally validated manifest')
    return operation.read(catalog, batch_id, item_id)


def _insert_result(connection, kb_id, item):
    connection.execute('INSERT INTO input_results VALUES(?,?,?,?,?,?)',
                       (str(item.entry.item_id), str(kb_id), str(item.batch_id), item.stage,
                        item.raw.sha256 if item.raw else None, item.model_dump_json()))


def record_result(catalog, owner, item, *, produced_by):
    if item.stage not in ('captured', 'failed'):
        raise failure('only terminal raw capture results may be recorded')
    archive = catalog.archives.verify(item.raw.sha256) if item.raw else None
    if archive and archive.size_bytes != item.raw.size_bytes:
        raise failure('raw checkpoint archive size differs')
    with catalog._owned(owner, produced_by) as connection:
        row = connection.execute('SELECT initial_json FROM input_items WHERE item_id=? AND batch_id=? AND kb_id=?',
                                 (str(item.entry.item_id), str(owner.token.batch_id), str(owner.token.kb_id))).fetchone()
        if row is None:
            raise failure('input does not belong to this batch')
        original = InputCheckpoint.model_validate_json(row[0])
        fixed_fields = ('entry', 'batch_id', 'document_id', 'base_version_id', 'capture_epoch')
        if any(getattr(original, key) != getattr(item, key) for key in fixed_fields):
            raise failure('input result changed the immutable request or baseline')
        if item.raw:
            if item.capture_epoch != owner.token.owner_epoch:
                raise failure('incomplete original input cannot be captured under a recovered owner')
            if (item.raw.source_uri != item.entry.source_uri or item.raw.source_key != item.entry.source_key or
                    item.raw.metadata != item.entry.metadata or item.raw.stamp != item.entry.stamp):
                raise failure('raw source differs from frozen request')
            previous = connection.execute('SELECT source_key FROM documents WHERE document_id=?', (str(item.document_id),)).fetchone()[0]
            connection.execute('INSERT INTO archive_objects VALUES(?,?) ON CONFLICT DO NOTHING', (archive.sha256, archive.size_bytes))
            if connection.execute('SELECT size_bytes FROM archive_objects WHERE sha256=?', (archive.sha256,)).fetchone() != (archive.size_bytes,):
                raise failure('registered archive size mismatch')
            _insert_result(connection, owner.token.kb_id, item)
            connection.execute('INSERT INTO document_sources VALUES(?,?,?,?,?,?)',
                               (str(item.entry.item_id), str(item.document_id), str(owner.token.kb_id), previous,
                                item.raw.source_key, item.raw.metadata.original_name))
            # Ordinary changes transfer path ownership only in the publication
            # transaction. A failed moved-file update retains the published path.
            if connection.execute('SELECT 1 FROM ordinary_mutations WHERE batch_id=?', (str(item.batch_id),)).fetchone() is None:
                connection.execute('UPDATE documents SET source_key=?,original_name=? WHERE document_id=? AND kb_id=?',
                                   (item.raw.source_key, item.raw.metadata.original_name, str(item.document_id), str(owner.token.kb_id)))
        else:
            _insert_result(connection, owner.token.kb_id, item)


def compare_base(catalog, batch_id, document_id, raw_hash, source_uri):
    """Only the batch's published base membership/config can make it unchanged."""
    with catalog._db.transaction() as connection:
        batch = ownership.read_batch(connection, batch_id)
        row = connection.execute('SELECT v.raw_hash,v.source_uri,s.document_encoding_fingerprint,s.index_fingerprint '
             'FROM revision_members m JOIN document_versions v ON v.document_version_id=m.document_version_id '
             'JOIN revisions r ON r.revision_id=m.revision_id JOIN processing_snapshots s ON s.snapshot_id=r.processing_snapshot_id '
             'WHERE m.revision_id=? AND m.document_id=?', (str(batch.base_revision_id), str(document_id))).fetchone()
        target = connection.execute('SELECT document_encoding_fingerprint,index_fingerprint FROM processing_snapshots WHERE snapshot_id=?',
                                     (str(batch.processing_snapshot_id),)).fetchone()
        base_config = connection.execute('SELECT s.document_encoding_fingerprint FROM revisions r '
                         'JOIN processing_snapshots s ON s.snapshot_id=r.processing_snapshot_id WHERE r.revision_id=?',
                         (str(batch.base_revision_id),)).fetchone()
    rebuild = base_config is not None and base_config[0] != target[0]
    if row is None:
        return 'new', rebuild
    if row[0] != raw_hash:
        return 'content_changed', rebuild
    if rebuild:
        return 'encoding_changed', True
    if row[1] != source_uri:
        return 'source_changed', False
    if row[3] != target[1]:
        return 'index_changed', False
    return 'unchanged', False
