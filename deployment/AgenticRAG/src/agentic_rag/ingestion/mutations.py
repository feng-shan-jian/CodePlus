"""Ordinary imports/deletions: complete documents, one immutable publication.

This is a coordinator over the existing mutation/publication protocol, not a
second state machine. Full initial evaluation builds retain their strict API.
"""

import json
import time
from uuid import UUID, uuid4

from .._schema import canonical_json, fingerprint
from ..config import ProcessingSnapshot
from ..domain import ErrorCode, RagError, RevisionMember
from ..indexes.manifest import (PreparedRevision, schema_spec, index_error,
                               rows_for_document, vector_hash)
from ..storage import inputs as input_store, publication
from ..storage.paths import failure
from .capture import capture_inputs
from .processing import process_inputs
from ..storage.processing import read as _read_processed
from .records import InputSelection
from .selection import select_inputs
from .encoding import outcomes, _read_encoded, encode_documents, cancelled as _cancelled


def request(catalog, batch_id):
    with catalog._db.transaction() as db:
        row = db.execute('SELECT request_hash,request_json FROM ordinary_mutations WHERE batch_id=?', (str(batch_id),)).fetchone()
    if row is None:
        return None
    value = json.loads(row[1])
    if fingerprint('ordinary-mutation-v1', value) != row[0]:
        raise failure('ordinary mutation request fingerprint differs')
    return value


def register_request(catalog, db, batch, snapshot, manifest, value):
    """Called inside the original begin transaction, before any source IO."""
    if batch.base_revision_id is not None:
        base = db.execute('SELECT s.document_encoding_fingerprint FROM revisions r JOIN processing_snapshots s '
                          'ON s.snapshot_id=r.processing_snapshot_id WHERE r.revision_id=? AND r.kb_id=?',
                          (str(batch.base_revision_id), str(batch.kb_id))).fetchone()
        if base != (snapshot.document_encoding_fingerprint,):
            raise RagError(ErrorCode.IDENTITY_MISMATCH, 'ordinary import cannot change document encoding; explicit D16 rebuild required', stage='mutation')
    for identity in value['deletions']:
        if db.execute('SELECT 1 FROM documents WHERE document_id=? AND kb_id=?', (identity, str(batch.kb_id))).fetchone() is None:
            raise failure('deletion must name an existing document in this library')
        if any(str(e.document_id) == identity for e in manifest.entries):
            raise failure('a document cannot be explicitly updated and deleted in one batch')
        source = db.execute('SELECT source_key FROM documents WHERE document_id=?', (identity,)).fetchone()[0]
        if any(e.source_key == source for e in manifest.entries):
            raise failure('a document cannot be imported and deleted in one batch')
    for guard in value['retry_guards']:
        # A final-version comparison alone misses add/delete ABA. Inspect all
        # publications after the completed batch's immutable owner generation.
        touched = db.execute('SELECT 1 FROM publications p JOIN revisions r ON r.revision_id=p.revision_id '
            'LEFT JOIN revision_members n ON n.revision_id=r.revision_id AND n.document_id=? '
            'LEFT JOIN revision_members b ON b.revision_id=r.base_revision_id AND b.document_id=? '
            'WHERE p.kb_id=? AND p.owner_epoch>? AND n.document_version_id IS NOT b.document_version_id LIMIT 1',
            (guard['document_id'],guard['document_id'],str(batch.kb_id),guard['anchor_epoch'])).fetchone()
        if touched:
            raise RagError(ErrorCode.IDENTITY_MISMATCH, 'failed item was superseded by an intervening publication', stage='retry')
        entries = [e for e in manifest.entries if e.selection_index==guard['selection_index']]
        if len(entries) != 1:
            raise RagError(ErrorCode.IDENTITY_MISMATCH, 'failed selection changed its file range; start a new import', stage='retry')
        entry = entries[0]
        if guard['document_id'] is None and entry is not None and entry.source_key is not None:
            if db.execute('SELECT 1 FROM documents WHERE kb_id=? AND source_key=?',
                          (str(batch.kb_id),entry.source_key)).fetchone():
                raise RagError(ErrorCode.IDENTITY_MISMATCH, 'failed path now belongs to a later document; use a new import', stage='retry')
        current = db.execute('SELECT document_version_id FROM revision_members WHERE revision_id=? AND document_id=?',
                             (str(batch.base_revision_id), guard['document_id'])).fetchone()
        if (current[0] if current else None) != guard['base_version_id']:
            raise RagError(ErrorCode.IDENTITY_MISMATCH, 'failed item was superseded; start a new explicit import', stage='retry')
        if guard['document_id'] is not None:
            source = db.execute('SELECT source_key FROM documents WHERE document_id=? AND kb_id=?',
                                (guard['document_id'], str(batch.kb_id))).fetchone()
            if source is None or source[0] != guard['identity_source_key']:
                raise RagError(ErrorCode.IDENTITY_MISMATCH, 'failed item path identity changed since completion', stage='retry')
    db.execute('INSERT INTO ordinary_mutations VALUES(?,?,?,?)',
               (str(batch.batch_id), str(batch.kb_id), fingerprint('ordinary-mutation-v1', value), canonical_json(value)))


def begin_changes(catalog, kb_id, snapshot, selections=(), *, delete_document_ids=(), _retry=None):
    """Freeze an explicit request. A missing directory never implies deletion."""
    deletions = tuple(str(UUID(str(v))) for v in delete_document_ids)
    if len(set(deletions)) != len(deletions) or not (selections or deletions):
        raise ValueError('unique explicit deletions or file selections required')
    manifest = select_inputs(tuple(selections))
    value = {'deletions': list(deletions), 'retry_of': _retry['batch_id'] if _retry else None,
             'retry_guards': _retry['guards'] if _retry else [],
             'accept_input_changes': _retry['accept_input_changes'] if _retry else False}
    return input_store.begin_import(catalog, kb_id, snapshot, manifest, _ordinary=value)


def retry_failed(catalog, batch_id, *, snapshot=None, accept_input_changes=False):
    """A new operation containing only failed files, guarded against newer work.

    Changed/unavailable original bytes require explicit acknowledgement. Config
    changes always require a new import (and D16 where encoding is incompatible).
    """
    old = catalog.get_batch(batch_id)
    if old.state.value not in ('PUBLISHED', 'COMPLETED_NO_CHANGE') or request(catalog, batch_id) is None:
        raise failure('retry_failed requires a normally completed ordinary batch')
    frozen = catalog.get_snapshot(old.processing_snapshot_id)
    snapshot = snapshot or ProcessingSnapshot.capture(uuid4(), frozen.resolved_config)
    if snapshot.config_fingerprint != frozen.config_fingerprint:
        raise RagError(ErrorCode.IDENTITY_MISMATCH, 'retry configuration changed; use an explicit new operation', stage='retry')
    states = outcomes(catalog, batch_id)
    completed = summary(catalog,batch_id)
    selections, guards = [], []
    for item in catalog.get_input_items(batch_id):
        if states[str(item.entry.item_id)]['state'] != 'failed':
            continue
        saved = next(v for v in completed['items'] if v['item_id']==str(item.entry.item_id))
        selections.append(InputSelection(path=item.entry.requested_path, document_id=item.document_id))
        guards.append({'selection_index':len(selections)-1,
                       'document_id':str(item.document_id) if item.document_id else None,
                       'base_version_id':str(item.base_version_id) if item.base_version_id else None,
                       'identity_source_key':saved['identity_source_key'], 'anchor_epoch':old.owner_epoch,
                       'raw_hash':item.raw.sha256 if item.raw else None})
    if not selections:
        raise failure('completed batch has no failed files to retry')
    return begin_changes(catalog, old.kb_id, snapshot, tuple(selections),
                         _retry={'batch_id':str(batch_id), 'guards':guards, 'accept_input_changes':accept_input_changes})


def process_changes(catalog, owner, tokenizer, provider, *, cancelled=None, request_seconds=120, observer=None):
    """Persist document encoding only after every chunk has a valid response."""
    batch = catalog.get_batch(owner.token.batch_id)
    req = request(catalog, batch.batch_id)
    if req is None:
        raise failure('ordinary request required')
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    _cancelled(cancelled)
    capture_inputs(catalog, owner, cancelled=cancelled, abort_cancelled=True)
    # Retry guards are checked after capture, using bytes rather than timestamps.
    for item in catalog.get_input_items(batch.batch_id):
        guard = next((g for g in req['retry_guards'] if g['selection_index'] == item.entry.selection_index), None)
        if guard and item.raw and not req['accept_input_changes'] and item.raw.sha256 != guard['raw_hash']:
            raise RagError(ErrorCode.SOURCE_CHANGED, 'retry input differs or had no saved original; explicitly acknowledge changed input', stage='retry')
    process_inputs(catalog, owner, tokenizer, skip_unchanged=True, cancelled=cancelled)
    encode_documents(catalog,owner,provider,ordinary=True,cancelled_check=cancelled,
                     request_seconds=request_seconds,observer=observer)
    return summary(catalog, batch.batch_id)


def _base(catalog, batch, snapshot, revision_id):
    """Re-derive every archived row and membership, authenticate old proof."""
    if batch.base_revision_id is None:
        return {}, {}, None, None
    from ..source_archive import read_version
    stored = publication.artifact(catalog, batch.base_revision_id, published=True)
    old_receipt = publication.receipt(catalog, UUID(stored['batch_id']), batch.base_revision_id)
    with catalog._db.transaction() as db:
        rev = db.execute('SELECT manifest_hash,processing_snapshot_id,index_state FROM revisions WHERE revision_id=? AND kb_id=?',
                         (str(batch.base_revision_id),str(batch.kb_id))).fetchone()
        values = db.execute('SELECT document_id,document_version_id,chunk_set_hash FROM revision_members WHERE revision_id=? AND kb_id=? ORDER BY document_id',
                            (str(batch.base_revision_id),str(batch.kb_id))).fetchall()
    original = catalog.get_snapshot(UUID(rev[1]))
    expected = json.loads(catalog.archives.read(stored['expected_hash']))['rows']
    proof = stored['validation']
    if (stored['kb_id'] != str(batch.kb_id) or original.document_encoding_fingerprint != snapshot.document_encoding_fingerprint or
            stored['spec'] != schema_spec(original) or stored['schema_hash'] != fingerprint('milvus-schema',stored['spec']) or
            rev[2] != 'READY' or old_receipt['artifact_id'] != stored['artifact_id'] or
            old_receipt['manifest_hash'] != rev[0] or proof['manifest_hash'] != rev[0] or
            proof['expected_hash'] != stored['expected_hash'] or proof['schema_hash'] != stored['schema_hash'] or
            proof['row_manifest_hash'] != fingerprint('index-rows',{'rows':expected})):
        raise failure('base artifact, encoding, publication or row proof differs')
    members, rows, actual = {}, {}, []
    for doc, ver, chunks_hash in values:
        source = read_version(catalog, batch.kb_id, batch.base_revision_id, UUID(doc), UUID(ver))
        derived, _, _ = rows_for_document(batch.kb_id, batch.base_revision_id, original, source.version, source.chunks)
        actual.extend(derived)
        members[doc] = RevisionMember(revision_id=revision_id,document_id=UUID(doc),document_version_id=UUID(ver),chunk_set_hash=chunks_hash)
        rows[doc] = tuple({**r,'revision_id':str(revision_id)} for r in derived)
    actual.sort(key=lambda r:r['chunk_id'])
    if [{k:v for k,v in r.items() if k!='vector_hash'} for r in expected] != actual:
        raise failure('base members/archived sources differ from sealed encoded manifest')
    return members, rows, stored, expected


def prepare_changes(catalog, batch_id, revision_id):
    return _prepare_changes_with_base(catalog, batch_id, revision_id)[0]


def _preparation_binding(catalog, batch, snapshot, req):
    return (catalog.store_id, batch.kb_id, batch.batch_id, batch.base_revision_id,
            batch.input_manifest_hash, batch.processing_snapshot_id,
            snapshot.config_fingerprint, fingerprint('ordinary-mutation-v1', req))


def _prepare_changes_with_base(catalog, batch_id, revision_id):
    batch = catalog.get_batch(batch_id)
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    req, states = request(catalog,batch_id), outcomes(catalog,batch_id)
    input_read = input_store._InputRead(catalog, batch_id)
    raw_items = input_read.items
    if set(states) != {str(i.entry.item_id) for i in raw_items}:
        raise failure('ordinary candidate requires every file to be terminal')
    members, rows_by_doc, base_artifact, base_expected = _base(catalog,batch,snapshot,revision_id)
    vectors = []
    for raw in raw_items:
        state = states[str(raw.entry.item_id)]
        if state['state'] == 'unchanged':
            if raw.stage != 'captured' or raw.change not in ('unchanged','index_changed') or str(raw.document_id) not in members:
                raise failure('unchanged item has no matching base member')
            if members[str(raw.document_id)].document_version_id != raw.base_version_id:
                raise failure('unchanged member version differs')
        elif state['state'] == 'encoded':
            checked = _read_processed(catalog,batch_id,raw.entry.item_id,_inputs=input_read)
            if checked is None or checked[0].stage != 'chunked':
                raise failure('encoded document lacks complete processing checkpoint')
            item, version, result = checked
            rows, _, _ = rows_for_document(batch.kb_id,revision_id,snapshot,version,result)
            encoded = _read_encoded(catalog,batch_id,raw,state,_inputs=input_read)
            members[str(raw.document_id)] = RevisionMember(revision_id=revision_id,document_id=raw.document_id,
                document_version_id=version.document_version_id,chunk_set_hash=next(a.sha256 for a in item.output_hashes if a.kind=='chunks'))
            rows_by_doc[str(raw.document_id)] = rows
            vectors.extend(encoded)
        elif state['state'] != 'failed':
            raise failure('unknown terminal file state')
    for identity in req['deletions']:
        members.pop(identity,None); rows_by_doc.pop(identity,None)
    rows = tuple(sorted((r for values in rows_by_doc.values() for r in values),key=lambda r:r['chunk_id']))
    if len({r['chunk_id'] for r in rows}) != len(rows):
        raise failure('candidate chunk identities are not unique')
    members = tuple(sorted(members.values(),key=lambda m:str(m.document_id)))
    spec = schema_spec(snapshot)
    manifest = fingerprint('publication-manifest', {'batch_id':str(batch_id),'input_manifest_hash':batch.input_manifest_hash,
        'snapshot':snapshot.config_fingerprint,'ordinary_request':fingerprint('ordinary-mutation-v1',req),
        'members':[m.model_dump(mode='json') for m in members],'rows':list(rows),'schema':spec})
    candidate = PreparedRevision(revision_id,members,rows,(),(),manifest,fingerprint('milvus-schema',spec),spec,tuple(vectors))
    return candidate, _preparation_binding(catalog,batch,snapshot,req), base_artifact, base_expected


def _candidate_seal(candidate):
    """Detect mutation without copying or JSON-serializing dense payloads."""
    if candidate.model_inputs or candidate.token_counts:
        raise failure('ordinary candidate cannot carry first-build model inputs')
    vectors = []
    for value in candidate.encoded_vectors:
        if set(value) != {'chunk_id','vector_hash','dense'}:
            raise failure('prepared vector fields changed')
        digest = vector_hash(value['dense'])
        if digest != value['vector_hash']:
            raise failure('prepared vector content differs from its digest')
        vectors.append({'chunk_id':value['chunk_id'], 'vector_hash':digest})
    return fingerprint('ordinary-build-candidate-v1', {
        'revision_id':str(candidate.revision_id),
        'members':[member.model_dump(mode='json') for member in candidate.members],
        'rows':list(candidate.rows), 'manifest_hash':candidate.manifest_hash,
        'schema_hash':candidate.schema_hash, 'spec':candidate.spec, 'vectors':vectors})


class _BuildPreparation:
    """One ordinary build's verified inputs; never a caller-supplied proof."""

    def __init__(self, catalog, owner, revision_id):
        with catalog._db.transaction() as db:
            owner.require(catalog._db, db)
        self._catalog, self._owner, self.revision_id = catalog, owner.token, revision_id
        self.candidate, self._binding, self.base_artifact, self.base_expected = _prepare_changes_with_base(
            catalog, owner.token.batch_id, revision_id)
        self._seal = _candidate_seal(self.candidate)
        self._base_seal = fingerprint('ordinary-build-base-v1', {
            'artifact':self.base_artifact, 'expected':self.base_expected})
        self._active = self._entered = False

    def __enter__(self):
        if self._entered:
            raise failure('build preparation cannot be reused')
        self._entered = self._active = True
        return self

    def __exit__(self, *_):
        self._active = False

    def checked(self, catalog, owner, revision_id):
        if (not self._active or catalog is not self._catalog or owner.token != self._owner or
                revision_id != self.revision_id):
            raise failure('build preparation belongs to a different operation or owner')
        with catalog._db.transaction() as db:
            owner.require(catalog._db, db, self._owner)
        batch = catalog.get_batch(owner.token.batch_id)
        snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
        binding = _preparation_binding(catalog, batch, snapshot, request(catalog,batch.batch_id))
        if binding != self._binding or _candidate_seal(self.candidate) != self._seal:
            raise failure('build preparation identity or candidate changed')
        if fingerprint('ordinary-build-base-v1', {
                'artifact':self.base_artifact, 'expected':self.base_expected}) != self._base_seal:
            raise failure('build preparation base material changed')
        return self.candidate


def summary(catalog, batch_id):
    batch = catalog.get_batch(batch_id)
    with catalog._db.transaction() as db:
        completed = db.execute('SELECT summary_json FROM mutation_completions WHERE batch_id=?',(str(batch_id),)).fetchone()
    if completed:
        return json.loads(completed[0])
    req, states = request(catalog,batch_id), outcomes(catalog,batch_id)
    items = []
    for item in catalog.get_input_items(batch_id):
        state = states.get(str(item.entry.item_id),{'state':'pending','error':None})
        items.append({'item_id':str(item.entry.item_id),'document_id':str(item.document_id) if item.document_id else None,
                      'identity_source_key':catalog.get_document(item.document_id).source_key if item.document_id else None,
                      'path':item.entry.requested_path,'base_version_id':str(item.base_version_id) if item.base_version_id else None,
                      'raw_hash':item.raw.sha256 if item.raw else None,'change':item.change,**state})
    return {'batch_id':str(batch_id),'state':batch.state.value,'base_revision_id':str(batch.base_revision_id) if batch.base_revision_id else None,
            'published_revision_id':None,'published_new':0,'published_updated':0,'published_deleted':0,
            'published_index_changed':False,
            'processed_unpublished':sum(i['state']=='encoded' for i in items),
            'unchanged':sum(i['state']=='unchanged' for i in items),'failed':sum(i['state']=='failed' for i in items),
            'items':items,'requested_deletions':req['deletions'] if req else []}


def completion_value(catalog, batch_id, revision_id=None):
    value = summary(catalog,batch_id)
    value['state'] = 'PUBLISHED' if revision_id else 'COMPLETED_NO_CHANGE'
    value['published_revision_id'] = str(revision_id) if revision_id else None
    if revision_id:
        value['published_new'] = sum(i['state']=='encoded' and i['base_version_id'] is None for i in value['items'])
        value['published_updated'] = sum(i['state']=='encoded' and i['base_version_id'] is not None for i in value['items'])
        with catalog._db.transaction() as db:
            base = {r[0] for r in db.execute('SELECT document_id FROM revision_members WHERE revision_id=?',(value['base_revision_id'],))}
            previous = db.execute('SELECT s.index_fingerprint FROM revisions r JOIN processing_snapshots s ON s.snapshot_id=r.processing_snapshot_id WHERE r.revision_id=?',
                                  (value['base_revision_id'],)).fetchone()
        batch = catalog.get_batch(batch_id)
        value['published_index_changed'] = previous is not None and previous[0] != catalog.get_snapshot(batch.processing_snapshot_id).index_fingerprint
        value['published_deleted'] = len(base & set(value['requested_deletions']))
        value['processed_unpublished'] = 0
    return value


def build_changes(catalog, owner, provider, backend, tokenizer, *, cancelled=None, observer=None, request_seconds=120, index_timeout=180):
    """Execute ordinary work; exceptions retain pending work and old pointer."""
    batch = catalog.get_batch(owner.token.batch_id)
    from ..storage.recovery import terminal
    old = terminal(catalog,batch.batch_id)
    if old is not None:
        return old
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    if backend.catalog is not catalog or backend.storage != snapshot.resolved_config.storage:
        raise failure('mutation backend differs from frozen catalog/endpoint')
    started = time.perf_counter()
    process_changes(catalog,owner,tokenizer,provider,cancelled=cancelled,request_seconds=request_seconds,observer=observer)
    with _BuildPreparation(catalog,owner,uuid4()) as prepared:
        return _build_prepared(catalog,owner,backend,batch,snapshot,prepared,started,
                               cancelled=cancelled,observer=observer,index_timeout=index_timeout)


def _build_prepared(catalog, owner, backend, batch, snapshot, prepared, started, *,
                    cancelled, observer, index_timeout):
    candidate = prepared.candidate
    with catalog._db.transaction() as db:
        prior = db.execute('SELECT document_id,document_version_id,chunk_set_hash FROM revision_members WHERE revision_id=? ORDER BY document_id',
                           (str(batch.base_revision_id),)).fetchall()
        prior_index = db.execute('SELECT s.index_fingerprint FROM revisions r JOIN processing_snapshots s ON s.snapshot_id=r.processing_snapshot_id WHERE revision_id=?',
                                 (str(batch.base_revision_id),)).fetchone()
    target = [(str(m.document_id),str(m.document_version_id),m.chunk_set_hash) for m in candidate.members]
    index_change = (prior_index is not None and prior_index[0] != snapshot.index_fingerprint and
                    any(i.stage=='captured' and i.change=='index_changed' for i in catalog.get_input_items(batch.batch_id)))
    changed = prior != target or index_change
    _cancelled(cancelled)
    if not changed:
        value = publication._complete_no_change(catalog,owner,_operation=prepared)
        return {'receipt':None,'summary':value,'metrics':{'build_seconds':time.perf_counter()-started,'reused_vectors':0,'encoded_vectors':0}}
    # Reuse authenticated rows from the complete immutable base Collection.
    vectors = {v['chunk_id']:v for v in candidate.encoded_vectors}
    reused = 0
    if any(r['chunk_id'] not in vectors for r in candidate.rows):
        catalog.retain_revision(owner,batch.base_revision_id,'vector_reuse')
        prepared.checked(catalog,owner,candidate.revision_id)
        old_vectors = backend.read_vectors(prepared.base_artifact,prepared.base_expected)
        for row in candidate.rows:
            if row['chunk_id'] not in vectors:
                vectors[row['chunk_id']] = old_vectors[row['chunk_id']]
                reused += 1
    _cancelled(cancelled)
    _, artifact = publication._register(catalog,owner,candidate.revision_id,_operation=prepared)
    backend.create(artifact,owner)
    if observer:
        observer('created',{'revision_id':str(candidate.revision_id),'collection_name':artifact['collection_name']})
    expected = []
    for offset in range(0,len(candidate.rows),256):
        _cancelled(cancelled)
        rows = []
        for row in candidate.rows[offset:offset+256]:
            vector = vectors[row['chunk_id']]
            encoded = {**row,'vector_hash':vector['vector_hash']}
            expected.append(encoded)
            rows.append({**encoded,'dense':vector['dense']})
        backend.insert(artifact,rows,owner)
    publication._record_encoded(catalog,owner,candidate.revision_id,expected,_operation=prepared)
    if observer:
        observer('inserted',{'revision_id':str(candidate.revision_id),'rows':len(expected)})
    timings = backend.finalize(artifact,len(expected),owner,index_timeout=index_timeout)
    _cancelled(cancelled)
    proof = publication.validate(catalog,owner,candidate.revision_id,backend)
    _cancelled(cancelled)
    if observer:
        observer('validated',{'revision_id':str(candidate.revision_id),'rows':len(expected)})
    _cancelled(cancelled)
    receipt = publication.publish(catalog,owner,candidate.revision_id)
    return {'receipt':receipt,'summary':summary(catalog,batch.batch_id),'validation':proof,
            'metrics':{'build_seconds':time.perf_counter()-started,'reused_vectors':reused,
                       'encoded_vectors':len(candidate.encoded_vectors),'documents':len(candidate.members),'chunks':len(candidate.rows),**timings}}
