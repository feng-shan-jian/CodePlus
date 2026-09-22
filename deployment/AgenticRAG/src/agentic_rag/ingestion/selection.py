"""Deterministic, explicit recursive selection with diagnostic entries."""

from collections import Counter
import os
from pathlib import Path
from uuid import uuid4

from ..domain import ErrorCode, ErrorInfo, RagError, SourceMetadata
from .records import InputEntry, InputManifest, InputSelection
from .source import file_stamp, input_error, normalize_source, open_source


def error_info(exc):
    if isinstance(exc, RagError):
        return exc.error
    code = ErrorCode.SOURCE_CHANGED if getattr(exc, 'winerror', None) in (32, 33) else ErrorCode.INVALID_INPUT
    return ErrorInfo(code=code, stage='input_snapshot',
                     message=f'{type(exc).__name__}: {exc}', retryable=False)


def select_inputs(selections: tuple[InputSelection, ...]) -> InputManifest:
    """No content reads, links, exclusions, implicit cwd, or silent scan errors.

    Input order is user order, each directory is depth-first codepoint-name order.
    All suffixes outside .md/.txt are errors, including hidden files. Empty
    directories contribute their root selection and zero file entries.
    """
    entries = []

    def failed(path, index, selection, exc):
        entries.append(InputEntry(item_id=uuid4(), selection_index=index, requested_path=str(path),
                                  document_id=selection.document_id, error=error_info(exc)))

    def visit(path, index, selection):
        try:
            normalized = normalize_source(path)
            if normalized.is_dir():
                if selection.document_id is not None:
                    raise input_error('explicit document update requires one file, not a directory')
                # Collect all encountered names even if iteration fails partway.
                children = []
                try:
                    with os.scandir(normalized) as scan:
                        for child in scan:
                            children.append(Path(child.path))
                except OSError as exc:
                    failed(normalized, index, selection, exc)
                for child in sorted(children, key=lambda p: p.name):
                    visit(child, index, selection)
                return
            suffix = normalized.suffix.lower()
            if suffix not in ('.md', '.txt'):
                raise input_error(f'unsupported input type: {suffix or "<no suffix>"}; expected .md/.txt')
            with open_source(normalized) as stream:
                observed = file_stamp(stream)
            entries.append(InputEntry(item_id=uuid4(), selection_index=index, requested_path=str(path),
                                      document_id=selection.document_id, source_key=normalized.as_uri(),
                                      source_uri=normalized.as_uri(), stamp=observed,
                                      metadata=SourceMetadata(original_name=normalized.name,
                                          media_type='text/markdown' if suffix == '.md' else 'text/plain')))
        except (OSError, RagError) as exc:
            failed(path, index, selection, exc)

    for index, selection in enumerate(selections):
        visit(Path(selection.path), index, selection)
    counts = Counter(e.source_key for e in entries if e.error is None)
    entries = [entry.model_copy(update={'error': ErrorInfo(code=ErrorCode.IDENTITY_MISMATCH,
                    stage='input_manifest', message='duplicate or overlapping input path in one batch')})
               if entry.error is None and counts[entry.source_key] > 1 else entry for entry in entries]
    return InputManifest(selections=selections, entries=tuple(entries))
