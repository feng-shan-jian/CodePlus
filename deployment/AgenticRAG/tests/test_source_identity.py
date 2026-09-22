"""Real Windows spelling/Unicode/hard-link boundaries and conservative links."""

import os
from pathlib import Path
import runpy
import subprocess

import pytest

from agentic_rag.ingestion import InputSelection, select_inputs
from agentic_rag.ingestion.source import normalize_source

SUPPORT = runpy.run_path(str(Path(__file__).with_name('test_ingestion_inputs.py')))


def test_actual_case_separators_unicode_and_tilde_are_reproducible(tmp_path):
    path = tmp_path/'资料 space~draft-é.txt'; path.write_bytes(b'bytes')
    key = normalize_source(path).as_uri()
    assert normalize_source(path.as_posix()).as_uri() == key
    if os.name == 'nt':
        assert normalize_source(str(path).swapcase()).as_uri() == key
    decomposed = tmp_path/'资料 space~draft-e\u0301.txt'; decomposed.write_bytes(b'bytes')
    assert normalize_source(decomposed).as_uri() != key


def test_actual_hard_links_keep_two_source_identities(tmp_path):
    catalog, kb, snap = SUPPORT['new_catalog'](tmp_path)
    first, second = tmp_path/'a.txt', tmp_path/'b.txt'
    first.write_bytes(b'hardlink original')
    os.link(first, second)
    a,b = SUPPORT['ingest'](catalog,kb,snap,first,second)
    assert a.stage == b.stage == 'captured'
    assert a.document_id != b.document_id and a.raw.sha256 == b.raw.sha256
    assert a.raw.source_key != b.raw.source_key


def test_actual_case_sensitive_directory_keeps_distinct_names(tmp_path):
    directory=tmp_path/'case-sensitive';directory.mkdir()
    if os.name=='nt':
        result=subprocess.run(['fsutil','file','setCaseSensitiveInfo',str(directory),'enable'],capture_output=True,text=True)
        assert result.returncode==0, result.stdout+result.stderr
    upper,lower=directory/'A.txt',directory/'a.txt'
    upper.write_bytes(b'upper');lower.write_bytes(b'lower')
    assert upper.read_bytes()!=lower.read_bytes()
    assert normalize_source(upper).as_uri()!=normalize_source(lower).as_uri()
    catalog,kb,snap=SUPPORT['new_catalog'](tmp_path)
    items=SUPPORT['ingest'](catalog,kb,snap,directory)
    assert len(items)==2 and all(item.stage=='captured' for item in items)
    assert items[0].document_id!=items[1].document_id


def test_actual_short_name_resolution_reports_duplicate_aliases(tmp_path):
    if os.name!='nt':
        return
    import ctypes
    path=tmp_path/'Long Original Source Filename.txt';path.write_bytes(b'original')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetShortPathNameW.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint32]
    buffer=ctypes.create_unicode_buffer(32768)
    assert kernel.GetShortPathNameW(str(path),buffer,len(buffer)),ctypes.get_last_error()
    assert normalize_source(buffer.value).as_uri()==normalize_source(path).as_uri()
    entries=select_inputs((InputSelection(path=str(path)),InputSelection(path=buffer.value))).entries
    assert all(e.error and 'duplicate' in e.error.message for e in entries)


def test_real_reparse_or_symlink_boundary_is_reported(tmp_path):
    target = tmp_path/'target'; target.mkdir()
    (target/'a.txt').write_bytes(b'one')
    linked = tmp_path/'link'
    if os.name == 'nt':
        command = subprocess.run(['cmd','/c','mklink','/J',str(linked),str(target)], capture_output=True)
        assert command.returncode == 0
    else:
        linked.symlink_to(target, target_is_directory=True)
    try:
        entry, = select_inputs((InputSelection(path=str(linked)),)).entries
        assert entry.error and 'reparse' in entry.error.message
    finally:
        if os.name == 'nt':
            linked.rmdir()
        else:
            linked.unlink()


@pytest.mark.parametrize('path', ['relative.txt', 'C:relative.txt', 'C:/folder/../input.txt', '//host/share/x.txt', 'C:/x.txt:stream'])
def test_invalid_or_ambiguous_paths_have_diagnostics(path):
    item, = select_inputs((InputSelection(path=path),)).entries
    assert item.error is not None


def test_same_file_selected_twice_with_case_alias_is_explicit_duplicate(tmp_path):
    path = tmp_path/'Case.txt'; path.write_bytes(b'bytes')
    alias = str(path).swapcase() if os.name == 'nt' else str(path)
    manifest = select_inputs((InputSelection(path=str(path)),InputSelection(path=alias)))
    assert len(manifest.entries) == 2 and all(e.error and 'duplicate' in e.error.message for e in manifest.entries)
