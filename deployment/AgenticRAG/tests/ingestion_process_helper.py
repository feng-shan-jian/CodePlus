"""Actual input-capture kill points and Win32 sharing probes for formal tests."""

import argparse
import ctypes
import json
import os
from pathlib import Path
import runpy
import time
from uuid import UUID

from agentic_rag.ingestion import InputSelection, capture_inputs, select_inputs
from agentic_rag.ingestion.source import VerifiedReader, _kernel
from agentic_rag.storage import Catalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('partial', 'orphan', 'writer', 'mapping', 'probe'))
    parser.add_argument('directory'); parser.add_argument('kb'); parser.add_argument('source')
    parser.add_argument('event'); parser.add_argument('control')
    args = parser.parse_args()
    source = Path(args.source)
    start = time.monotonic()

    def announce(**extra):
        result = {'pid':os.getpid(), 'mode':args.mode, 'elapsed_ms':(time.monotonic()-start)*1000, **extra}
        Path(args.event).write_text(json.dumps(result,indent=2),encoding='utf-8')

    def wait():
        deadline = time.monotonic() + 30
        while not Path(args.control).exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('test parent did not release input helper')
            time.sleep(.02)

    if args.mode == 'mapping' and os.name == 'nt':
        kernel = _kernel()
        kernel.CreateFileMappingW.argtypes = [ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_wchar_p]
        kernel.CreateFileMappingW.restype = ctypes.c_void_p
        kernel.MapViewOfFile.argtypes = [ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_size_t]
        kernel.MapViewOfFile.restype = ctypes.c_void_p
        kernel.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateFileW(str(source),0xC0000000,7,None,3,0,None)
        assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
        mapping = kernel.CreateFileMappingW(handle,None,4,0,0,None)  # PAGE_READWRITE
        kernel.CloseHandle(handle)
        assert mapping, ctypes.get_last_error()
        view = kernel.MapViewOfFile(mapping,2,0,0,0)  # FILE_MAP_WRITE
        try:
            assert view, ctypes.get_last_error()
            announce(result='held writable mapping with original file handle closed'); wait()
        finally:
            if view: kernel.UnmapViewOfFile(view)
            kernel.CloseHandle(mapping)
        return
    if args.mode in ('writer','mapping'):
        with source.open('r+b') as stream:
            if args.mode == 'mapping':
                import mmap
                with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_WRITE):
                    announce(result='held writable mapping'); wait()
            else:
                announce(result='held writable handle'); wait()
        return
    if args.mode == 'probe':
        results = {}
        for name, action in [('write',lambda:source.write_bytes(b'bad')),('rename',lambda:source.rename(source.with_suffix('.moved'))),
                              ('delete',lambda:source.unlink())]:
            try:
                action()
                results[name] = 'UNEXPECTED_SUCCESS'
            except OSError as exc:
                results[name] = {'type':type(exc).__name__,'winerror':getattr(exc,'winerror',None),'errno':exc.errno}
        announce(results=results)
        return
    catalog = Catalog(args.directory)
    snapshot = runpy.run_path(str(Path(__file__).with_name('storage_process_helper.py')))['snapshot']()
    manifest = select_inputs((InputSelection(path=str(source)),))
    with catalog.begin_import(UUID(args.kb), snapshot, manifest) as owner:
        original_read = VerifiedReader.read
        original_put = catalog.archives.put
        reads = 0
        def blocked_read(reader, size):
            nonlocal reads
            # first file uses two reads, including the verified EOF. Block the
            # second file's next read after one complete block reached staging.
            if reads == 3 and reader.count:
                announce(batch_id=str(owner.token.batch_id), checkpoint='second file partially copied',
                         complete=[i.raw.sha256 for i in catalog.get_input_items(owner.token.batch_id) if i.raw],
                         staging_files=[p.name for p in catalog._directory.path('staging').iterdir()])
                wait()
            reads += 1
            return original_read(reader,size)
        writes = 0
        def blocked_put(*a,**kw):
            nonlocal writes
            archive = original_put(*a,**kw)
            writes += 1
            if writes == 2:
                announce(batch_id=str(owner.token.batch_id), checkpoint='second archive complete before metadata',
                         orphan_hash=archive.sha256,
                         complete=[i.raw.sha256 for i in catalog.get_input_items(owner.token.batch_id) if i.raw])
                wait()
            return archive
        if args.mode == 'partial':
            VerifiedReader.read = blocked_read
        else:
            catalog.archives.put = blocked_put
        capture_inputs(catalog,owner)
        owner.abandon()


if __name__ == '__main__':
    main()
