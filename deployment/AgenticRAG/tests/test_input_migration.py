"""Actual schema-1 database upgrade, authenticated before any new write."""

from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import io
import json
from pathlib import Path
import runpy
from uuid import uuid4

import apsw
import pytest

from agentic_rag.domain import RagError
from agentic_rag.storage import Catalog
from agentic_rag.storage.archives import ArchiveStore
from agentic_rag.storage.database import APPLICATION_ID
from agentic_rag.storage.paths import DataDirectory

HELPER=runpy.run_path(str(Path(__file__).with_name('storage_process_helper.py')))


def schema1_fixture(path):
    """Use the exact shipped R06 SQL; history below is synthetic metadata."""
    directory=DataDirectory(path)
    archive=ArchiveStore(directory).put(io.BytesIO(b'real archived original in schema1'))
    schema=files('agentic_rag.storage').joinpath('schema.sql').read_text(encoding='utf-8')
    digest=hashlib.sha256(schema.encode()).hexdigest()
    connection=apsw.Connection(str(path/'catalog.sqlite'))
    kb,doc,version,batch,revision=tuple(uuid4() for _ in range(5))
    snapshot=HELPER['snapshot']()
    with connection:
        connection.execute(schema)
        connection.execute('INSERT INTO store_identity VALUES(1,?)',(str(directory.store_id),))
        connection.execute('INSERT INTO schema_migrations VALUES(?,?,?)',(1,digest,datetime.now(timezone.utc).isoformat()))
        connection.execute('INSERT INTO libraries(kb_id,name) VALUES(?,?)',(str(kb),'old schema library'))
        connection.execute('INSERT INTO processing_snapshots VALUES(?,?,?,?,?,?)',
            (str(snapshot.snapshot_id),str(kb),snapshot.config_fingerprint,snapshot.document_encoding_fingerprint,snapshot.index_fingerprint,snapshot.resolved_config.model_dump_json()))
        connection.execute('INSERT INTO archive_objects VALUES(?,?)',(archive.sha256,archive.size_bytes))
        connection.execute('INSERT INTO documents VALUES(?,?,?,?,?)',(str(doc),str(kb),'old-source.txt',1,'old-source.txt'))
        # Old complete versions require parsed/map references. These are marked
        # synthetic fixture bytes, never presented as real R07 parser output.
        parsed=ArchiveStore(directory).put(io.BytesIO(b'SYNTHETIC schema1 parsed/map fixture'))
        connection.execute('INSERT INTO archive_objects VALUES(?,?)',(parsed.sha256,parsed.size_bytes))
        metadata=json.dumps({'schema_version':1,'original_name':'old-source.txt','title':None,'media_type':'text/plain'})
        connection.execute('INSERT INTO document_versions VALUES(?,?,?,?,?,?,?,?,?,?)',
            (str(version),str(kb),str(doc),archive.sha256,parsed.sha256,parsed.sha256,'a'*64,'file:///old-source.txt',datetime.now(timezone.utc).isoformat(),metadata))
        connection.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?)',(str(revision),str(kb),None,'a'*64,str(snapshot.snapshot_id),'PREPARING'))
        connection.execute('INSERT INTO revision_members VALUES(?,?,?,?,?)',(str(kb),str(revision),str(doc),str(version),'a'*64))
        connection.execute("UPDATE revisions SET index_state='READY'")
        connection.execute("INSERT INTO mutation_batches VALUES(?,?,?,?,?,?,?,'WAITING_RECOVERY',NULL,'SNAPSHOTTING')",
            (str(batch),str(kb),str(revision),'b'*64,str(snapshot.snapshot_id),1,str(uuid4())))
        connection.execute('UPDATE libraries SET current_revision_id=?,pending_mutation_id=?,owner_epoch=1 WHERE kb_id=?',(str(revision),str(batch),str(kb)))
        connection.pragma('application_id',APPLICATION_ID)
        connection.pragma('user_version',1)
    connection.pragma('journal_mode','wal')
    connection.close()
    return dict(kb=kb,doc=doc,version=version,batch=batch,revision=revision,snapshot=snapshot,raw=archive.sha256,migration_hash=digest)


def test_real_schema1_upgrade_preserves_history_and_never_fabricates_manifest(tmp_path):
    path=tmp_path/'old-data'
    old=schema1_fixture(path)
    catalog=Catalog(path)
    assert catalog.get_library(old['kb']).current_revision_id==old['revision']
    assert catalog.get_document(old['doc']).original_name=='old-source.txt'
    assert catalog.get_version(old['version']).raw_hash==old['raw']
    assert catalog.archives.read(old['raw'])==b'real archived original in schema1'
    assert catalog.get_snapshot(old['snapshot'].snapshot_id)==old['snapshot']
    with catalog._db.transaction() as connection:
        assert connection.pragma('user_version')==8
        assert connection.execute('SELECT sha256 FROM schema_migrations WHERE version=1').fetchone()==(old['migration_hash'],)
        assert connection.execute('SELECT count(*) FROM schema_migrations').fetchone()==(8,)
        assert connection.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert connection.execute('PRAGMA integrity_check').fetchone()==('ok',)
    with pytest.raises(RagError,match='no persisted input manifest'):
        catalog.get_input_items(old['batch'])
    assert Catalog(path).get_version(old['version'])==catalog.get_version(old['version'])


@pytest.mark.parametrize('damage',['migration_hash','store_identity'])
def test_schema1_identity_is_verified_before_upgrade(tmp_path,damage):
    path=tmp_path/'wrong-old-data';schema1_fixture(path)
    connection=apsw.Connection(str(path/'catalog.sqlite'))
    if damage=='migration_hash': connection.execute("UPDATE schema_migrations SET sha256=?",('f'*64,))
    else: connection.execute('UPDATE store_identity SET store_id=?',(str(uuid4()),))
    connection.close()
    with pytest.raises(RagError): Catalog(path)
    connection=apsw.Connection(str(path/'catalog.sqlite'))
    assert connection.pragma('user_version')==1
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='input_manifests'").fetchone() is None
    connection.close()
