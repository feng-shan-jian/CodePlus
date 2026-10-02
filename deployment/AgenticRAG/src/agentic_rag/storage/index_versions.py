"""Immutable revision membership over shared, append-only document versions."""

import json
from uuid import UUID

from .paths import failure


def collection_id(db, artifact_id):
    row = db.execute('SELECT collection_artifact_id FROM artifact_collections WHERE artifact_id=?',
                     (artifact_id,)).fetchone()
    return row[0] if row else artifact_id


def bind(db, artifact, base=None):
    """Called in candidate registration; old stores are enrolled without rewriting rows."""
    if base is not None:
        root = collection_id(db, base['artifact_id'])
        db.execute('INSERT INTO artifact_collections VALUES(?,?) ON CONFLICT DO NOTHING',
                   (base['artifact_id'], root))
        db.execute('INSERT INTO indexed_document_versions '
                   'SELECT ?,document_version_id,? FROM revision_members WHERE revision_id=? '
                   'ON CONFLICT DO NOTHING', (root, base['revision_id'], base['revision_id']))
    else:
        root = artifact['artifact_id']
    db.execute('INSERT INTO artifact_collections VALUES(?,?)', (artifact['artifact_id'], root))
    db.execute('INSERT INTO indexed_document_versions '
               'SELECT ?,document_version_id,? FROM revision_members WHERE revision_id=? '
               'ON CONFLICT DO NOTHING', (root, artifact['revision_id'], artifact['revision_id']))


def physical(catalog, artifact):
    from .publication import artifact as read_artifact
    with catalog._db.transaction() as db:
        root = collection_id(db, artifact['artifact_id'])
        row = db.execute('SELECT revision_id FROM index_artifacts WHERE artifact_id=?', (root,)).fetchone()
    if row is None:
        raise failure('physical collection identity is missing')
    value = read_artifact(catalog, UUID(row[0]))
    if value['kb_id'] != artifact['kb_id'] or value['schema_hash'] != artifact['schema_hash']:
        raise failure('shared collection library/schema differs')
    return value


def shared(catalog, artifact):
    with catalog._db.transaction() as db:
        root = collection_id(db, artifact['artifact_id'])
        return db.execute('SELECT count(*) FROM artifact_collections WHERE collection_artifact_id=?',
                          (root,)).fetchone()[0] > 1


def origins(catalog, artifact):
    with catalog._db.transaction() as db:
        root = collection_id(db, artifact['artifact_id'])
        return dict(db.execute('SELECT m.document_version_id,COALESCE(v.producer_revision_id,?) '
            'FROM revision_members m LEFT JOIN indexed_document_versions v '
            'ON v.document_version_id=m.document_version_id AND v.collection_artifact_id=? '
            'WHERE m.revision_id=?', (artifact['revision_id'], root, artifact['revision_id'])))


def version_filter(versions, extra=''):
    # Filtering happens inside both Milvus retrieval branches, before their top K.
    selected = 'document_version_id in ' + json.dumps(sorted(versions))
    return f'({selected}) and ({extra})' if extra else selected


def logical_row(artifact, row, producers):
    expected = producers.get(row['document_version_id'])
    if expected is None or row['revision_id'] != expected:
        from ..indexes.manifest import index_error
        raise index_error('indexed document version/producer differs from revision membership')
    return {**row, 'revision_id': artifact['revision_id']}


def reclamation(db, artifact):
    """The family lifecycle lock stays held until deletion and its receipt finish."""
    root = collection_id(db, artifact['artifact_id'])
    other = db.execute("SELECT a.revision_id FROM artifact_collections c JOIN index_artifacts a USING(artifact_id) "
        "WHERE c.collection_artifact_id=? AND a.artifact_id<>? AND a.state<>'RECLAIMED'",
        (root, artifact['artifact_id'])).fetchall()
    if not other:
        return None  # Last logical revision owns the final physical drop.
    retained = {v for rid, in other for v, in db.execute(
        'SELECT document_version_id FROM revision_members WHERE revision_id=?', (rid,))}
    versions = {v for v, in db.execute('SELECT document_version_id FROM revision_members WHERE revision_id=?',
                                      (artifact['revision_id'],))}
    return sorted(versions - retained)
