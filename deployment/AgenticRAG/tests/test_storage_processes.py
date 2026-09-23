"""Real separate Windows processes, forced death, takeover and run lock proof."""

import ctypes
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from uuid import UUID, uuid4

import pytest

from agentic_rag.domain import BatchState, Document, RagError, RunStatus
from agentic_rag.storage import Catalog, OwnerToken

HELPER_PATH = Path(__file__).with_name("storage_process_helper.py")
HELPER = runpy.run_path(str(HELPER_PATH))
EVENTS = []


@pytest.fixture(autouse=True)
def save_evidence(request):
    yield
    report = os.environ.get("R06_PROCESS_REPORT")
    if report:
        Path(report).write_text(json.dumps({"platform": sys.platform, "python": sys.executable, "events": EVENTS}, indent=2) + "\n", encoding="utf-8")


class Child:
    def __init__(self, tmp_path, mode, catalog, kb_id):
        suffix = str(uuid4())
        self.event, self.control = tmp_path / f"event-{suffix}.json", tmp_path / f"control-{suffix}"
        self.args = [sys.executable, "-I", "-B", str(HELPER_PATH), mode, str(catalog._directory.root), str(kb_id), str(self.event), str(self.control)]
        self.process = subprocess.Popen(self.args, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.actual_pid = None
        EVENTS.append({"event": "spawn", "launcher_pid": self.process.pid, "argv": self.args, "cwd": str(tmp_path)})

    def wait_event(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.event.exists():
                try:
                    result = json.loads(self.event.read_text(encoding="utf-8"))
                    self.actual_pid = result["pid"]
                    EVENTS.append(result)
                    return result
                except json.JSONDecodeError:
                    pass
            if self.process.poll() is not None:
                out, err = self.process.communicate(timeout=1)
                pytest.fail(f"child exited {self.process.returncode}: {out} {err}")
            time.sleep(0.02)
        pytest.fail("child did not publish event within 15 seconds")

    def finish(self, *, kill=False):
        if self.process.poll() is None:
            if kill:
                if os.name == "nt" and self.actual_pid is not None:
                    # Windows venv python.exe is a redirector. Terminate the
                    # event's real interpreter PID, not merely the launcher.
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
                    kernel.OpenProcess.restype = ctypes.c_void_p
                    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                    handle = kernel.OpenProcess(0x100001, False, self.actual_pid)
                    assert handle, ctypes.get_last_error()
                    try:
                        assert kernel.TerminateProcess(handle, 91), ctypes.get_last_error()
                        assert kernel.WaitForSingleObject(handle, 10000) == 0
                        EVENTS.append({"event": "actual_interpreter_terminated", "pid": self.actual_pid, "launcher_pid": self.process.pid, "wait_result": "signaled", "requested_exit_code": 91})
                    finally:
                        kernel.CloseHandle(handle)
                else:
                    self.process.terminate()
            else:
                self.control.touch()
        try:
            out, err = self.process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            out, err = self.process.communicate(timeout=5)
            EVENTS.append({"event": "timeout", "launcher_pid": self.process.pid, "stdout": out, "stderr": err})
            raise
        EVENTS.append({"event": "terminated" if kill else "joined", "pid": self.actual_pid, "launcher_pid": self.process.pid, "exit_code": self.process.returncode, "stdout": out, "stderr": err})
        if not kill:
            assert self.process.returncode == 0, out + err
        assert self.process.poll() is not None


def token_from(data):
    return OwnerToken(**{key: value if key == "owner_epoch" else UUID(value) for key, value in data.items()})


def test_same_library_exclusion_other_library_and_pin_progress(tmp_path):
    catalog = Catalog(tmp_path / "data")
    library = catalog.create_library("contention")
    revision = HELPER["seed_ready"](catalog, library.kb_id)
    holder = Child(tmp_path, "mutation", catalog, library.kb_id)
    probe = None
    try:
        first = holder.wait_event()
        probe = Child(tmp_path, "probe", catalog, library.kb_id)
        second = probe.wait_event()
        assert first["pid"] != second["pid"] != os.getpid()
        assert first["result"] == "acquired"
        assert second["same_library"] == "LIBRARY_BUSY"
        assert second["other_library"] == "completed"
        assert second["read_library"] == str(library.kb_id)
        assert second["pin"] == str(revision)
        assert second["elapsed_ms"] < 3000
        assert holder.process.poll() is None  # long OS lock still held, outside SQL
        probe.finish()
    finally:
        if probe is not None and probe.process.poll() is None:
            probe.finish(kill=True)
        holder.finish(kill=True)


def test_real_owner_death_preserves_pending_and_fences_new_epoch(tmp_path):
    catalog = Catalog(tmp_path / "data")
    library = catalog.create_library("death")
    holder = Child(tmp_path, "mutation", catalog, library.kb_id)
    try:
        info = holder.wait_event()
    finally:
        holder.finish(kill=True)
    assert holder.process.returncode != 0
    old = token_from(info["token"])
    reopened = Catalog(catalog._directory.root)
    with pytest.raises(RagError, match="unfinished batch"):
        reopened.begin_mutation(library.kb_id, HELPER["snapshot"](), "a" * 64)
    expected = reopened.identify_interrupted(library.kb_id)
    assert expected == old
    assert reopened.get_batch(old.batch_id).state == BatchState.WAITING_RECOVERY
    with reopened.resume_mutation(expected) as current:
        assert current.token.owner_epoch > old.owner_epoch
        doc = Document(document_id=uuid4(), kb_id=library.kb_id, source_key="late", original_name="late")
        with pytest.raises(RagError, match="late result"):
            reopened.add_document(current, doc, produced_by=old)
        reopened.add_document(current, doc, produced_by=current.token)
        EVENTS.append({"event": "takeover", "pid": os.getpid(), "old_epoch": old.owner_epoch, "new_epoch": current.token.owner_epoch, "old_nonce": str(old.owner_nonce), "new_nonce": str(current.token.owner_nonce), "late_result": "rejected"})
        current.abandon()
    with reopened.begin_mutation(library.kb_id, HELPER["snapshot"](), "a" * 64) as next_owner:
        next_owner.abandon()


def test_real_run_death_and_nonce_checked_cleanup(tmp_path):
    catalog = Catalog(tmp_path / "data")
    library = catalog.create_library("run death")
    HELPER["seed_ready"](catalog, library.kb_id)
    holder = Child(tmp_path, "run", catalog, library.kb_id)
    try:
        info = holder.wait_event()
        run_id, nonce = UUID(info["run_id"]), UUID(info["nonce"])
        with pytest.raises(RagError, match="lock is held"):
            catalog.release_crashed_run(run_id, nonce)
        assert catalog.get_pin(run_id).state == "active"
    finally:
        holder.finish(kill=True)
    assert holder.process.returncode != 0
    reopened = Catalog(catalog._directory.root)
    with pytest.raises(RagError, match="nonce"):
        reopened.release_crashed_run(run_id, uuid4())
    # R15 startup now performs this exact death/nonce/lock proof automatically.
    assert reopened.get_pin(run_id).state == "released"
    reopened.release_crashed_run(run_id, nonce)
    assert reopened.get_pin(run_id).state == "released"
    assert reopened.get_run(run_id).status == RunStatus.FAILED
    EVENTS.append({"event": "crashed_pin_released", "pid": os.getpid(), "run_id": str(run_id), "nonce": str(nonce), "status": reopened.get_run(run_id).status.value})


def test_two_processes_publish_identical_archive_once(tmp_path):
    catalog = Catalog(tmp_path / "data")
    library = catalog.create_library("archives")
    children = [Child(tmp_path, "archive", catalog, library.kb_id) for _ in range(2)]
    try:
        deadline = time.monotonic() + 15
        while not all(Path(str(child.event) + ".ready").exists() for child in children):
            assert time.monotonic() < deadline, "archive barrier timeout"
            assert all(child.process.poll() is None for child in children)
            time.sleep(0.01)
        EVENTS.append({"event": "archive_barrier_ready", "pids": [child.process.pid for child in children]})
        for child in children:
            child.control.touch()
        events = [child.wait_event() for child in children]
        assert events[0]["pid"] != events[1]["pid"]
        assert events[0]["sha256"] == events[1]["sha256"]
        obj = catalog.archives.verify(events[0]["sha256"])
        assert obj.size_bytes == events[0]["size_bytes"] == events[1]["size_bytes"]
        assert len(list(catalog._directory.path("archives").rglob("*"))) == 2  # prefix dir + one complete object
        assert list(catalog._directory.path("staging").iterdir()) == []
        for child in children:
            child.finish()
    finally:
        for child in children:
            if child.process.poll() is None:
                child.finish(kill=True)
