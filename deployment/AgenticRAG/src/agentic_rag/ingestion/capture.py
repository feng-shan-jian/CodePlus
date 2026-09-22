"""Original capture and checkpoint validation; no parse, index or publication."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from ..domain import ErrorCode, ErrorInfo, RagError
from ..storage import inputs as input_store
from .records import RawSnapshot
from .selection import error_info
from .source import VerifiedReader, file_stamp, input_error, normalize_source, open_source


def capture_inputs(catalog, owner, *, cancelled=None):
    """Finish pending raw inputs only in their original owner's capture epoch."""
    token = owner.token
    for item in catalog.get_input_items(token.batch_id):
        if item.stage != 'pending':
            continue
        if item.capture_epoch != token.owner_epoch:
            result = item.model_copy(update={'stage': 'failed', 'error': ErrorInfo(
                code=ErrorCode.CHECKPOINT_INVALID, stage='input_snapshot',
                message='original snapshot incomplete at interruption; use a new batch, never latest source')})
            input_store.record_result(catalog, owner, result, produced_by=token)
            continue
        # Check live ownership before doing source IO, without holding SQL open.
        with catalog._owned(owner, token):
            pass
        try:
            if cancelled is not None and cancelled():
                raise input_error('input capture cancelled', ErrorCode.CANCELLED)
            path = normalize_source(Path(item.entry.requested_path))
            if path.as_uri() != item.entry.source_key:
                raise input_error('source path changed after input selection', ErrorCode.SOURCE_CHANGED)
            with open_source(path) as stream:
                before = file_stamp(stream)
                if before != item.entry.stamp:
                    raise input_error('source changed since input selection', ErrorCode.SOURCE_CHANGED)
                reader = VerifiedReader(stream, before, cancelled)
                archive = catalog.archives.put(reader)
                if not reader.complete or file_stamp(stream) != before or normalize_source(path).as_uri() != item.entry.source_key:
                    raise input_error('source changed before checkpoint acceptance', ErrorCode.SOURCE_CHANGED)
                raw = RawSnapshot(sha256=archive.sha256, size_bytes=archive.size_bytes,
                                  captured_at=datetime.now(timezone.utc), source_uri=item.entry.source_uri,
                                  source_key=item.entry.source_key, metadata=item.entry.metadata, stamp=before)
                change, rebuild = input_store.compare_base(catalog, token.batch_id, item.document_id, raw.sha256, raw.source_uri)
                result = item.model_copy(update={'stage': 'captured', 'raw': raw, 'change': change,
                                                'requires_rebuild_confirmation': rebuild})
                input_store.record_result(catalog, owner, result, produced_by=token)
        except (OSError, RagError) as exc:
            # Metadata/ownership failures must not overwrite an already committed
            # success or conceal loss of authority. A second insert fails closed.
            error = error_info(exc)
            if isinstance(exc, OSError) and getattr(exc, 'errno', None) in (28, 5):
                error = ErrorInfo(code=ErrorCode.STORAGE_FAILURE, stage='input_snapshot', message=str(exc))
            input_store.record_result(catalog, owner, item.model_copy(update={'stage': 'failed', 'error': error}), produced_by=token)
        except (KeyboardInterrupt, SystemExit):
            input_store.record_result(catalog, owner, item.model_copy(update={'stage': 'failed', 'error': ErrorInfo(
                code=ErrorCode.CANCELLED, stage='input_snapshot', message='capture interrupted before acceptance')}), produced_by=token)
            raise
    return catalog.get_input_items(token.batch_id)


def read_input(catalog, batch_id, item_id) -> bytes:
    """The R08 input boundary: verified archive only, never the original path."""
    item = next((i for i in catalog.get_input_items(batch_id) if i.entry.item_id == item_id), None)
    if item is None or item.stage != 'captured':
        raise input_error('input has no complete raw checkpoint', ErrorCode.CHECKPOINT_INVALID)
    try:
        data = catalog.archives.read(item.raw.sha256)
    except (OSError, RagError) as exc:
        raise input_error(f'raw checkpoint unavailable: {exc}', ErrorCode.CHECKPOINT_INVALID) from exc
    if len(data) != item.raw.size_bytes or hashlib.sha256(data).hexdigest() != item.raw.sha256:
        raise input_error('raw checkpoint size or hash differs', ErrorCode.CHECKPOINT_INVALID)
    return data


def verify_inputs(catalog, batch_id):
    """Read-only recovery material; reports invalid items, never silently repairs."""
    batch = catalog.get_batch(batch_id)
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    results = []
    for item in catalog.get_input_items(batch_id):
        if item.stage == 'captured':
            try:
                archive = catalog.archives.verify(item.raw.sha256)
                if archive.size_bytes != item.raw.size_bytes:
                    raise input_error('raw checkpoint size differs', ErrorCode.CHECKPOINT_INVALID)
            except (OSError, RagError) as exc:
                results.append(item.model_copy(update={'stage':'failed', 'raw':None, 'change':None,
                    'error':ErrorInfo(code=ErrorCode.CHECKPOINT_INVALID, stage='input_snapshot', message=str(exc))}))
                continue
        elif item.stage == 'pending':
            results.append(item.model_copy(update={'stage':'failed', 'error':ErrorInfo(
                code=ErrorCode.CHECKPOINT_INVALID, stage='input_snapshot', message='original input snapshot not completed')}))
            continue
        results.append(item)
    return snapshot, tuple(results)
