"""Prepared resources and one short authoritative publication transaction."""

from datetime import datetime, timezone
import io
import json
from uuid import UUID, uuid4

from .._schema import canonical_json, fingerprint
from ..domain import IndexArtifact, IndexState, KnowledgeRevision
from ..indexes.manifest import collection_name, prepare, index_error
from .paths import failure
from . import index_versions


def receipt(catalog, batch_id, revision_id=None):
    with catalog._db.transaction() as connection:
        row = connection.execute('SELECT batch_id,kb_id,revision_id,artifact_id,owner_nonce,owner_epoch,manifest_hash,published_at FROM publications WHERE batch_id=?', (str(batch_id),)).fetchone()
    if row is None:
        return None
    result = dict(zip(('batch_id','kb_id','revision_id','artifact_id','owner_nonce','owner_epoch','manifest_hash','published_at'), row))
    if revision_id is not None and result['revision_id'] != str(revision_id):
        raise failure('batch was published as a different revision')
    return result


def artifact(catalog, revision_id, *, published=False):
    with catalog._db.transaction() as connection:
        row = connection.execute('SELECT artifact_id,kb_id,revision_id,batch_id,collection_name,schema_hash,owner_epoch,spec_json,expected_hash,validation_json,state FROM index_artifacts WHERE revision_id=?', (str(revision_id),)).fetchone()
        exists = connection.execute('SELECT 1 FROM publications WHERE revision_id=?', (str(revision_id),)).fetchone()
    if row is None or (published and (row[-1] != 'READY' or exists is None)):
        raise failure('revision has no verified published index artifact')
    result = dict(zip(('artifact_id','kb_id','revision_id','batch_id','collection_name','schema_hash','owner_epoch','spec','expected_hash','validation','state'), row))
    result['spec'] = json.loads(result['spec'])
    result['validation'] = json.loads(result['validation']) if result['validation'] else None
    return result


def owned_artifact(catalog, owner, revision_id):
    with catalog._owned(owner) as connection:
        selected = connection.execute('SELECT a.revision_id FROM current_candidates c JOIN index_artifacts a ON a.artifact_id=c.artifact_id WHERE c.batch_id=? AND c.kb_id=?',
                                      (str(owner.token.batch_id),str(owner.token.kb_id))).fetchone()
        if selected != (str(revision_id),):
            raise failure('artifact is not the current candidate of this batch')
    stored = artifact(catalog, revision_id)
    if (stored['kb_id'], stored['batch_id'], stored['revision_id'], stored['owner_epoch']) != (
            str(owner.token.kb_id), str(owner.token.batch_id), str(revision_id), owner.token.owner_epoch):
        raise failure('artifact belongs to a different library, batch or owner epoch')
    return stored


def register(catalog, owner, revision_id=None):
    return _register(catalog, owner, revision_id)


def _prepared(catalog, owner, revision_id, operation):
    if operation is None:
        return prepare(catalog, owner.token.batch_id, revision_id or uuid4())
    from ..ingestion.mutations import _BuildPreparation
    if type(operation) is not _BuildPreparation:
        raise failure('internally verified build preparation required')
    return operation.checked(catalog, owner, revision_id or operation.revision_id)


def _register(catalog, owner, revision_id=None, *, _operation=None):
    batch = catalog.get_batch(owner.token.batch_id)
    with catalog._db.transaction() as connection:
        existing = connection.execute('SELECT a.revision_id FROM current_candidates c JOIN index_artifacts a ON a.artifact_id=c.artifact_id WHERE c.batch_id=? AND c.kb_id=? AND a.owner_epoch=?',
                                      (str(batch.batch_id),str(batch.kb_id),owner.token.owner_epoch)).fetchone()
    revision_id = revision_id or (UUID(existing[0]) if existing else uuid4())
    prepared = _prepared(catalog, owner, revision_id, _operation)
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    base = None
    if batch.base_revision_id is not None:
        from ..ingestion.mutations import request
        if request(catalog, batch.batch_id) is not None:
            previous = artifact(catalog, batch.base_revision_id, published=True)
            previous_snapshot = catalog.get_snapshot(catalog.get_batch(UUID(previous['batch_id'])).processing_snapshot_id)
            with catalog._db.transaction() as db:
                owned_collection = db.execute('SELECT 1 FROM artifact_ownership_proofs WHERE artifact_id=?',
                    (index_versions.collection_id(db, previous['artifact_id']),)).fetchone()
            if (previous['schema_hash'] == prepared.schema_hash and
                    previous_snapshot.index_fingerprint == snapshot.index_fingerprint and
                    previous_snapshot.resolved_config.storage == snapshot.resolved_config.storage and owned_collection):
                base = previous
    name = collection_name(snapshot.resolved_config.storage.namespace, catalog.store_id, batch.kb_id, revision_id, owner.token.owner_epoch)
    record = IndexArtifact(artifact_id=uuid4(), revision_id=revision_id, collection_name=name,
                           schema_hash=prepared.schema_hash, owner_epoch=owner.token.owner_epoch, state=IndexState.PREPARING)
    revision = KnowledgeRevision(revision_id=revision_id, kb_id=batch.kb_id, base_revision_id=batch.base_revision_id,
                                 manifest_hash=prepared.manifest_hash, processing_snapshot_id=batch.processing_snapshot_id,
                                 index_state=IndexState.PREPARING)
    with catalog._owned(owner) as connection:
        prior = connection.execute('SELECT revision_id,collection_name,schema_hash,owner_epoch FROM index_artifacts WHERE batch_id=? AND kb_id=? AND owner_epoch=?',
                                   (str(batch.batch_id),str(batch.kb_id),owner.token.owner_epoch)).fetchone()
        if prior is not None:
            if prior != (str(revision_id),name,prepared.schema_hash,owner.token.owner_epoch):
                raise failure('candidate registration retry differs from its original request')
            return prepared, artifact(catalog,revision_id)
        current = connection.execute('SELECT state FROM mutation_batches WHERE batch_id=?',(str(batch.batch_id),)).fetchone()
        if current != ('PROCESSING',):
            raise failure('candidate registration requires completed processing')
        catalog._insert_candidate(connection, owner, revision, prepared.members)
        connection.execute('INSERT INTO index_artifacts VALUES(?,?,?,?,?,?,?,?,NULL,NULL,?)',
            (str(record.artifact_id), str(batch.kb_id), str(revision_id), str(batch.batch_id), name,
             record.schema_hash, record.owner_epoch, canonical_json(prepared.spec), record.state.value))
        connection.execute('INSERT INTO current_candidates VALUES(?,?,?) ON CONFLICT(batch_id) DO UPDATE SET artifact_id=excluded.artifact_id',
                           (str(batch.batch_id),str(batch.kb_id),str(record.artifact_id)))
        index_versions.bind(connection, {'artifact_id':str(record.artifact_id),'revision_id':str(revision_id)}, base)
        connection.execute('INSERT INTO artifact_creation_intents VALUES(?,?,?,?)',
                           (str(record.artifact_id),snapshot.resolved_config.storage.milvus_uri,'default',
                            'agentic-rag-physical-v1:' + str(record.artifact_id) + ':' + uuid4().hex))
        connection.execute("UPDATE mutation_batches SET state='INDEXING' WHERE batch_id=?", (str(batch.batch_id),))
    return prepared, artifact(catalog, revision_id)


def record_encoded(catalog, owner, revision_id, expected_rows):
    """Persist expected float32 hashes, after full checkpoint derivation; no caller pass flag."""
    return _record_encoded(catalog, owner, revision_id, expected_rows)


def _record_encoded(catalog, owner, revision_id, expected_rows, *, _operation=None):
    stored = owned_artifact(catalog, owner, revision_id)
    if stored['state'] != 'PREPARING' or stored['expected_hash'] is not None:
        raise failure('encoded artifact is already sealed or recorded')
    candidate = _prepared(catalog, owner, revision_id, _operation)
    if len(expected_rows) != len(candidate.rows):
        raise index_error('encoded manifest row count differs')
    for expected, original in zip(expected_rows, candidate.rows, strict=True):
        if set(expected) != set(original) | {'vector_hash'} or {k:v for k,v in expected.items() if k != 'vector_hash'} != original:
            raise index_error('encoded manifest differs from verified source')
        digest = expected['vector_hash']
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise index_error('encoded manifest vector hash invalid')
    obj = catalog.archives.put(io.BytesIO(canonical_json({'rows': list(expected_rows)}).encode()))
    with catalog._owned(owner) as connection:
        connection.execute('INSERT INTO archive_objects VALUES(?,?) ON CONFLICT DO NOTHING', (obj.sha256, obj.size_bytes))
        connection.execute("UPDATE index_artifacts SET expected_hash=? WHERE revision_id=? AND kb_id=? AND batch_id=? AND owner_epoch=? AND state='PREPARING' AND expected_hash IS NULL",
                           (obj.sha256, str(revision_id), str(owner.token.kb_id), str(owner.token.batch_id), owner.token.owner_epoch))
        if connection.changes() != 1:
            raise failure('encoded artifact changed or owner no longer matches')
        connection.execute("UPDATE mutation_batches SET state='VALIDATING' WHERE batch_id=?", (str(owner.token.batch_id),))
    return obj.sha256


def validate(catalog, owner, revision_id, backend):
    stored = owned_artifact(catalog, owner, revision_id)
    from ..indexes.milvus import publication_validator
    snapshot = catalog.get_snapshot(catalog.get_batch(owner.token.batch_id).processing_snapshot_id)
    validate_index = publication_validator(catalog, snapshot.resolved_config.storage, backend)
    candidate = prepare(catalog, owner.token.batch_id, revision_id)
    if stored['owner_epoch'] != owner.token.owner_epoch or stored['schema_hash'] != candidate.schema_hash or not stored['expected_hash']:
        raise failure('artifact identity/encoding unavailable')
    expected = json.loads(catalog.archives.read(stored['expected_hash']))['rows']
    if [{k:v for k,v in r.items() if k != 'vector_hash'} for r in expected] != list(candidate.rows):
        raise failure('archived encoded manifest differs from validated processing')
    # Heavy service validation and full archive reads are outside SQLite locks.
    proof = validate_index(stored, expected)
    proof.update(manifest_hash=candidate.manifest_hash, expected_hash=stored['expected_hash'],
                 schema_hash=candidate.schema_hash, validation_version=1)
    serialized = canonical_json(proof)
    with catalog._owned(owner) as connection:
        row = connection.execute('SELECT manifest_hash,processing_snapshot_id FROM revisions WHERE revision_id=?', (str(revision_id),)).fetchone()
        batch = catalog.get_batch(owner.token.batch_id)
        if row != (candidate.manifest_hash, str(batch.processing_snapshot_id)):
            raise failure('candidate metadata changed during validation')
        actual = connection.execute('SELECT document_id,document_version_id,chunk_set_hash FROM revision_members WHERE revision_id=? ORDER BY document_id', (str(revision_id),)).fetchall()
        if actual != [(str(m.document_id), str(m.document_version_id), m.chunk_set_hash) for m in candidate.members]:
            raise failure('candidate members differ from verified checkpoints')
        connection.execute("UPDATE index_artifacts SET validation_json=?,state='READY' WHERE revision_id=? AND kb_id=? AND batch_id=? AND state='PREPARING' AND expected_hash=? AND owner_epoch=?",
                           (serialized, str(revision_id), str(owner.token.kb_id), str(owner.token.batch_id), stored['expected_hash'], owner.token.owner_epoch))
        if connection.changes() != 1:
            raise failure('candidate validation ownership changed')
        connection.execute("UPDATE revisions SET index_state='READY' WHERE revision_id=? AND index_state='PREPARING'", (str(revision_id),))
        connection.execute("UPDATE mutation_batches SET state='READY' WHERE batch_id=?", (str(owner.token.batch_id),))
    return proof


def publish(catalog, owner, revision_id):
    """Retry reads the old receipt first, including after a newer publication.

    No Milvus or filesystem calls in this transaction. A lost commit response
    is resolved via receipt(), never by resetting state or deleting resources.
    """
    old = receipt(catalog, owner.token.batch_id, revision_id)
    if old is not None:
        if (old['kb_id'], old['owner_nonce'], old['owner_epoch']) != (str(owner.token.kb_id), str(owner.token.owner_nonce), owner.token.owner_epoch):
            raise failure('publication retry belongs to a different owner')
        return old
    from ..ingestion.mutations import request, completion_value
    completion = completion_value(catalog,owner.token.batch_id,revision_id) if request(catalog,owner.token.batch_id) is not None else None
    with catalog._owned(owner) as connection:
        batch = connection.execute('SELECT base_revision_id,processing_snapshot_id,state FROM mutation_batches WHERE batch_id=?', (str(owner.token.batch_id),)).fetchone()
        current = connection.execute('SELECT current_revision_id FROM libraries WHERE kb_id=?', (str(owner.token.kb_id),)).fetchone()
        row = connection.execute('SELECT a.artifact_id,a.validation_json,a.expected_hash,r.manifest_hash,r.processing_snapshot_id,a.owner_epoch,a.state,r.index_state FROM index_artifacts a JOIN revisions r ON r.revision_id=a.revision_id AND r.kb_id=a.kb_id WHERE a.revision_id=? AND a.batch_id=? AND a.kb_id=?',
                                 (str(revision_id), str(owner.token.batch_id), str(owner.token.kb_id))).fetchone()
        if batch[2] != 'READY' or current != (batch[0],) or row is None or row[4:] != (batch[1], owner.token.owner_epoch, 'READY', 'READY'):
            raise failure('publication requires validated candidate, current base and matching owner/snapshot')
        proof = json.loads(row[1])
        if proof['manifest_hash'] != row[3] or proof['expected_hash'] != row[2]:
            raise failure('publication validation fingerprints differ')
        connection.execute('INSERT INTO publications VALUES(?,?,?,?,?,?,?,?)',
                           (str(owner.token.batch_id), str(owner.token.kb_id), str(revision_id), row[0],
                            str(owner.token.owner_nonce), owner.token.owner_epoch, row[3], datetime.now(timezone.utc).isoformat()))
        connection.execute('UPDATE libraries SET current_revision_id=?,pending_mutation_id=NULL WHERE kb_id=?', (str(revision_id), str(owner.token.kb_id)))
        connection.execute("UPDATE mutation_batches SET state='PUBLISHED',published_revision_id=? WHERE batch_id=?", (str(revision_id), str(owner.token.batch_id)))
        if completion is not None:
            # Path ownership follows only successful versions admitted to this
            # publication. Failed updates keep their previous logical identity.
            for item in completion['items']:
                if item['state'] != 'encoded':
                    continue
                source = connection.execute('SELECT source_key,original_name FROM document_sources WHERE item_id=? AND kb_id=?',
                                            (item['item_id'],str(owner.token.kb_id))).fetchone()
                if source is None:
                    raise failure('published update lacks its captured source identity')
                connection.execute('UPDATE documents SET source_key=?,original_name=? WHERE document_id=? AND kb_id=?',
                                   (*source,item['document_id'],str(owner.token.kb_id)))
            connection.execute('INSERT INTO mutation_completions VALUES(?,?,?)',
                               (str(owner.token.batch_id),str(owner.token.kb_id),canonical_json(completion)))
        connection.execute('DELETE FROM revision_dependencies WHERE batch_id=?', (str(owner.token.batch_id),))
    catalog._maintain_indexes()
    return receipt(catalog, owner.token.batch_id, revision_id)


def complete_no_change(catalog, owner):
    """Normal terminal batch, no revision/collection/publication is created."""
    return _complete_no_change(catalog, owner)


def _complete_no_change(catalog, owner, *, _operation=None):
    from .recovery import terminal
    old = terminal(catalog,owner.token.batch_id)
    if old is not None:
        if old['state'] != 'COMPLETED_NO_CHANGE':
            raise failure('batch already has a different terminal result')
        return old['summary']
    from ..ingestion.mutations import prepare_changes, completion_value
    prepared = (prepare_changes(catalog,owner.token.batch_id,uuid4()) if _operation is None else
                _prepared(catalog,owner,None,_operation))
    summary = completion_value(catalog,owner.token.batch_id)
    target = [(str(m.document_id),str(m.document_version_id),m.chunk_set_hash) for m in prepared.members]
    with catalog._owned(owner) as connection:
        batch = catalog.get_batch(owner.token.batch_id)
        current = connection.execute('SELECT current_revision_id FROM libraries WHERE kb_id=?',(str(batch.kb_id),)).fetchone()
        members = connection.execute('SELECT document_id,document_version_id,chunk_set_hash FROM revision_members WHERE revision_id=? ORDER BY document_id',
                                     (str(batch.base_revision_id),)).fetchall()
        if any(i.stage=='captured' and i.change=='index_changed' for i in catalog.get_input_items(batch.batch_id)):
            raise failure('successful index changes require a validated new candidate')
        if (batch.state.value != 'PROCESSING' or current != (str(batch.base_revision_id) if batch.base_revision_id else None,) or
                target != members or connection.execute('SELECT 1 FROM index_artifacts WHERE batch_id=?',(str(batch.batch_id),)).fetchone()):
            raise failure('no-change completion requires unchanged membership and no candidate')
        connection.execute('INSERT INTO mutation_completions VALUES(?,?,?)',
                           (str(batch.batch_id),str(batch.kb_id),canonical_json(summary)))
        connection.execute("UPDATE mutation_batches SET state='COMPLETED_NO_CHANGE',recovery_stage=NULL WHERE batch_id=?",(str(batch.batch_id),))
        connection.execute('UPDATE libraries SET pending_mutation_id=NULL WHERE kb_id=?',(str(batch.kb_id),))
        connection.execute('DELETE FROM revision_dependencies WHERE batch_id=?',(str(batch.batch_id),))
    catalog._maintain_indexes()
    return summary
