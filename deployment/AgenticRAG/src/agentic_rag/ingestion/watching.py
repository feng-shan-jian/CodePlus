"""Explicit source subscriptions; notifications schedule hash-based reconciliation."""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from uuid import UUID

from ..domain import RagError
from .records import InputSelection
from .selection import select_inputs
from .source import normalize_source, open_source, file_stamp


def subscriptions(catalog, kb_id=None):
    with catalog._db.transaction() as db:
        rows = db.execute('SELECT kb_id,path,last_result FROM watched_sources WHERE enabled=1' +
                          (' AND kb_id=?' if kb_id else '') + ' ORDER BY kb_id,path',
                          (str(kb_id),) if kb_id else ()).fetchall()
    return [{'kb_id':kb,'path':path,'last_result':json.loads(result) if result else None}
            for kb,path,result in rows]


def subscribe(catalog, kb_id, paths, *, enabled=True):
    catalog.get_library(kb_id)
    normalized = []
    for value in paths:
        path = Path(value)
        if enabled:
            path = normalize_source(path)
            if not path.is_dir() and path.suffix.lower() not in ('.md','.txt'):
                raise ValueError('Source watching supports .md/.txt files and directories.')
            data = catalog._directory.root.resolve()
            if path == data or data in path.parents or (path.is_dir() and path in data.parents):
                raise ValueError('Watch sources outside the knowledge data directory and its parents.')
        elif not path.is_absolute() or '..' in path.parts:
            raise ValueError('Use the absolute subscribed path to stop watching.')
        normalized.append(str(path))
    with catalog._db.transaction(write=True) as db:
        for path in normalized:
            db.execute('INSERT INTO watched_sources VALUES(?,?,?,NULL) ON CONFLICT(kb_id,path) '
                       'DO UPDATE SET enabled=excluded.enabled,last_result=NULL', (str(kb_id),path,int(enabled)))
    return subscriptions(catalog, kb_id)


def _digest(path):
    # Same stable source handle policy as explicit import. The importer captures
    # its own snapshot; only that successfully published snapshot is the baseline.
    with open_source(path) as stream:
        before = file_stamp(stream)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if file_stamp(stream) != before:
            raise OSError('source changed while hashing')
    return digest


def changed_paths(catalog, kb_id, paths):
    library = catalog.get_library(kb_id)
    with catalog._db.transaction() as db:
        published = dict(db.execute('SELECT v.source_uri,v.raw_hash FROM revision_members m '
            'JOIN document_versions v USING(document_version_id) WHERE m.revision_id=?',
            (str(library.current_revision_id),)))
    selected, errors, seen = [], [], set()
    for path in paths:
        manifest = select_inputs((InputSelection(path=path),))
        for entry in manifest.entries:
            # A watched directory tracks supported source documents only.
            if Path(entry.requested_path).suffix.lower() not in ('.md','.txt') and Path(entry.requested_path).is_file():
                continue
            if entry.error:
                errors.append({'path':entry.requested_path,'message':entry.error.message})
                continue
            if entry.source_key in seen:
                continue
            seen.add(entry.source_key)
            try:
                if _digest(Path(entry.requested_path)) != published.get(entry.source_uri):
                    selected.append(entry.requested_path)
            except (OSError, RagError) as error:
                errors.append({'path':entry.requested_path,'message':str(error)})
    return selected, errors


def synchronize(catalog, settings, kb_id, *, cancelled=None):
    """One reconciliation, through the same user-command mutation boundary."""
    from ..adapters.codeplus.management import run_management
    roots = subscriptions(catalog, kb_id)
    paths, errors = changed_paths(catalog, kb_id, [r['path'] for r in roots])
    if paths:
        result = run_management(settings, 'import', str(kb_id), arguments=tuple(paths), cancelled=cancelled)
    else:
        result = {'action':'sync','status':'partial' if errors else 'completed',
                  'stop_reason':'source_errors' if errors else 'no_change','data':{}}
    result = {**result,'action':'sync','source_errors':errors}
    if errors and result['status']=='completed':
        result.update(status='partial',stop_reason='source_errors')
    # This is diagnostic state only. Hash baselines always come from publication.
    with catalog._db.transaction(write=True) as db:
        for root in roots:
            db.execute('UPDATE watched_sources SET last_result=? WHERE kb_id=? AND path=?',
                       (json.dumps(result,ensure_ascii=False),str(kb_id),root['path']))
    return result


class _Notifications:
    """Windows directory notifications; periodic reconciliation covers missed events."""

    def __init__(self):
        self.handles = {}
        self.file_stamps = {}
        self.kernel = None
        if os.name == 'nt':
            self.kernel = ctypes.WinDLL('kernel32',use_last_error=True)
            self.kernel.FindFirstChangeNotificationW.argtypes = [ctypes.c_wchar_p,ctypes.c_int,ctypes.c_uint32]
            self.kernel.FindFirstChangeNotificationW.restype = ctypes.c_void_p
            for name in ('FindNextChangeNotification','FindCloseChangeNotification'):
                getattr(self.kernel,name).argtypes = [ctypes.c_void_p]
                getattr(self.kernel,name).restype = ctypes.c_int
            self.kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p,ctypes.c_uint32]
            self.kernel.WaitForSingleObject.restype = ctypes.c_uint32

    def poll(self, paths):
        if self.kernel is None:
            return set()
        directories = {str(Path(p)) for p in paths if Path(p).is_dir()}
        for path in set(self.handles)-directories:
            self.kernel.FindCloseChangeNotification(self.handles.pop(path))
        for path in directories-set(self.handles):
            handle = self.kernel.FindFirstChangeNotificationW(path, True, 0x1|0x2|0x8|0x10)
            if handle not in (None,ctypes.c_void_p(-1).value):
                self.handles[path] = handle
        changed = set()
        for path,handle in tuple(self.handles.items()):
            state = self.kernel.WaitForSingleObject(handle,0)
            if state == 0:
                changed.add(path)
                if not self.kernel.FindNextChangeNotification(handle):
                    self.kernel.FindCloseChangeNotification(self.handles.pop(path))
            elif state != 258:
                self.kernel.FindCloseChangeNotification(self.handles.pop(path))
                changed.add(path)
        selected = set()
        for value in paths:
            path = Path(value)
            if path.is_dir():
                if str(path) in changed:
                    selected.add(value)
                continue
            try:
                info = path.stat()
                stamp = (info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            except OSError:
                stamp = None
            if value in self.file_stamps and self.file_stamps[value] != stamp:
                selected.add(value)
            self.file_stamps[value] = stamp
        self.file_stamps = {path:stamp for path,stamp in self.file_stamps.items() if path in paths}
        return selected

    def close(self):
        if self.kernel:
            for handle in self.handles.values():
                self.kernel.FindCloseChangeNotification(handle)
        self.handles.clear()


class SourceWatcher:
    """One host worker, serial library updates, quiet unchanged scans and drained stop."""

    def __init__(self, settings, *, notify=None, interval=30, debounce=0.6):
        if interval <= 0 or debounce < 0:
            raise ValueError('positive reconciliation interval and nonnegative debounce required')
        self.settings, self.notify = settings, notify
        self.interval, self.debounce = interval, debounce
        self.stop_event = threading.Event()
        self.last_error = None
        self.thread = threading.Thread(target=self._run,name='knowledge-source-watcher',daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()

    def join(self, timeout=None):
        self.thread.join(timeout)

    def _run(self):
        from ..storage import Catalog
        notifications = _Notifications()
        previous, due, next_scan, last_notice = {}, {}, {}, {}
        try:
            catalog = Catalog(self.settings.knowledge.storage.data_dir)
            while not self.stop_event.is_set():
                roots = subscriptions(catalog)
                grouped = {}
                for root in roots:
                    grouped.setdefault(root['kb_id'],set()).add(root['path'])
                changed = notifications.poll([r['path'] for r in roots])
                now = time.monotonic()
                for kb,paths in grouped.items():
                    signalled = bool(paths & changed)
                    deadline = next_scan.setdefault(kb, now+self.interval)
                    if paths != previous.get(kb) or signalled:
                        due[kb] = min(now+self.debounce, deadline)
                    if now >= deadline:
                        due[kb] = now
                    if kb not in due or now < due[kb] or self.stop_event.is_set():
                        continue
                    try:
                        result = synchronize(catalog,self.settings,UUID(kb),cancelled=self.stop_event.is_set)
                    except Exception as error:
                        result = {'action':'sync','status':'failed','stop_reason':'update_failed',
                                  'data':{'message':str(error)}}
                        with catalog._db.transaction(write=True) as db:
                            db.execute('UPDATE watched_sources SET last_result=? WHERE kb_id=? AND enabled=1',
                                       (json.dumps(result,ensure_ascii=False),kb))
                    del due[kb]
                    next_scan[kb] = time.monotonic()+self.interval
                    summary = result.get('data',{}).get('summary',{})
                    meaningful = result['status']!='completed' or bool(summary.get('published_revision_id'))
                    message = json.dumps(result,ensure_ascii=False,sort_keys=True)
                    if meaningful and message != last_notice.get(kb) and self.notify and not self.stop_event.is_set():
                        self.notify({'kb_id':kb,**result})
                    last_notice[kb] = message
                previous = grouped
                for mapping in (due,next_scan,last_notice):
                    for kb in set(mapping)-set(grouped):
                        del mapping[kb]
                self.stop_event.wait(0.2)
        except Exception as error:
            self.last_error = str(error)
            if self.notify and not self.stop_event.is_set():
                self.notify({'kb_id':None,'action':'sync','status':'failed',
                             'stop_reason':'monitor_stopped','data':{'message':self.last_error}})
        finally:
            notifications.close()
