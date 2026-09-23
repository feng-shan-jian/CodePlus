"""Optional run controls; independent of any feature package or product UI.

Default Agent callers do not construct these objects. Capabilities are supplied
by trusted host adapters, never reconstructed from model-generated dictionaries.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
import time
from typing import Any, Callable, Literal, Protocol
from uuid import uuid4


ModelPurpose = Literal['agent', 'compact', 'finalize', 'citation_repair']


class BudgetStop(Exception):
    def __init__(self, reason: str, *, hard: bool = False):
        super().__init__(reason)
        self.reason, self.hard = reason, hard


@dataclass(frozen=True)
class SourceSpan:
    candidate_id: str
    source_start: int
    source_end: int
    body_start: int
    body_end: int
    block_index: int | None = None

    def crop(self, start: int, end: int, offset: int = 0) -> SourceSpan | None:
        low, high = max(start, self.body_start), min(end, self.body_end)
        if high <= low:
            return None
        return replace(self, source_start=self.source_start+low-self.body_start,
                       source_end=self.source_start+high-self.body_start,
                       body_start=low-start+offset, body_end=high-start+offset)


@dataclass(frozen=True)
class WireSpan:
    tool_call_id: str
    path: tuple[str | int, ...]
    source: SourceSpan


@dataclass(frozen=True)
class PreparedRequest:
    request_id: str
    raw_body: bytes
    protocol: str
    mappings: tuple[WireSpan, ...]
    output_cap: int


@dataclass(frozen=True)
class RequestOutcome:
    delivery: Literal['not_sent', 'rejected', 'unknown', 'confirmed']
    raw_usage: dict[str, Any] | None
    terminal: str | None
    elapsed_ms: int
    response_model: str | None = None
    error_type: str | None = None
    terminal_details: dict[str, Any] | None = None


@dataclass
class PreSendGate:
    request_id: str = field(default_factory=lambda: str(uuid4()))
    refusal: BaseException | None = None
    permit: Any = None
    observed: bool = False


class ModelCallControl(Protocol):
    output_cap: int
    purpose: ModelPurpose
    deadline: float
    json_output: bool = False

    async def before_send(self, request: PreparedRequest) -> Any: ...
    async def settled(self, permit: Any, result: RequestOutcome) -> None: ...


@dataclass(frozen=True)
class HostRunContext:
    entrypoint: str
    session_id: str
    work_dir: str
    protocol: str
    client: Any
    hook_engine: Any
    permission_checker: Any
    request: str = ''


@dataclass(frozen=True)
class ValidatedArtifact:
    run_id: str
    markdown: str
    sha256: str
    citation_ids: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactSave:
    status: Literal['pending', 'saved', 'failed', 'interrupted']
    path: str
    sha256: str | None = None
    size_bytes: int | None = None
    message: str = ''


@dataclass(frozen=True)
class OutputDecision:
    action: Literal['accept', 'repair', 'stop']
    artifact: ValidatedArtifact | None = None
    message: str = ''


@dataclass(frozen=True)
class RunOutcome:
    status: str
    reason: str
    artifact: ValidatedArtifact | None = None
    run_id: str | None = None
    save: ArtifactSave | None = None
    research: dict[str, Any] | None = None


class RunScope(Protocol):
    registry: Any
    client: Any
    system_prompt: str
    owner: RunTaskOwner
    deadline: float
    max_iterations: int
    purpose: ModelPurpose
    outcome: RunOutcome | None
    report_path: str | None

    def check(self) -> None: ...
    def model_control(self, purpose: ModelPurpose) -> ModelCallControl: ...
    def prepare_turn(self, conversation: Any) -> None: ...
    def system_prompt_with_hooks(self, prompts: list[str] | None) -> str: ...
    def begin_finalize(self, conversation: Any, reason: str) -> bool: ...
    async def admit_tool(self, tool_id: str, name: str, params: Any) -> Any: ...
    async def tool_finished(self, permit: Any, result: Any) -> None: ...
    def rejected_tool(self, tool_id: str, name: str, reason: str) -> None: ...
    async def assess_output(self, text: str, terminal: str) -> OutputDecision: ...
    async def finish(self, outcome: RunOutcome) -> None: ...
    async def aclose(self) -> None: ...


class RunExecutionPolicy(Protocol):
    async def start(self, context: HostRunContext) -> RunScope: ...


class RunTaskOwner:
    """Own coroutine tasks, permission waits and real executor completions.

    Cancelling an awaiting coroutine never proves its worker thread finished.
    The adapter must keep its version pin while ``drain`` reports pending work.
    """
    def __init__(self, deadline: float):
        self.deadline = deadline
        self.nonce = uuid4().hex
        self.closing = False
        self.tasks: set[asyncio.Task] = set()
        self.permissions: set[asyncio.Future] = set()
        self.threads: set[Future] = set()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='controlled-run')

    def check(self) -> None:
        if self.closing:
            raise BudgetStop('run_closed', hard=True)
        if time.monotonic() >= self.deadline:
            raise BudgetStop('time_budget', hard=True)

    def task(self, coroutine) -> asyncio.Task:
        try:
            self.check()
        except BaseException:
            coroutine.close()
            raise
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        def completed(done):
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(completed)
        return task

    async def gather(self, coroutines):
        coroutines = list(coroutines)
        tasks = []
        try:
            for index, coroutine in enumerate(coroutines):
                try:
                    tasks.append(self.task(coroutine))
                except BaseException:
                    for unstarted in coroutines[index+1:]:
                        unstarted.close()
                    raise
            return await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            # Scope aclose owns the finite grace and cleanup_pending handoff.
            # Never wait here indefinitely for a child suppressing cancellation.
            raise

    async def run_sync(self, function: Callable, *args, **kwargs):
        self.check()
        future = self._executor.submit(function, *args, **kwargs)
        self.threads.add(future)
        # Do not attach a coroutine cancellation to the concurrent Future.
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            # wait() does not cancel its member on caller cancellation. Unlike
            # Python 3.14 shield(), it also does not install an unconditional
            # late-exception logger after cancellation; our owner consumes it.
            await asyncio.wait({wrapped})
            return wrapped.result()
        finally:
            # Keep completed/failed futures until drain observes every result.
            if wrapped.done() and not wrapped.cancelled():
                wrapped.exception()

    async def wait_permission(self, future: asyncio.Future):
        self.permissions.add(future)
        try:
            self.check()
            async with asyncio.timeout_at(self.deadline):
                result = await future
            self.check()
            return result
        except TimeoutError as error:
            raise BudgetStop('time_budget', hard=True) from error
        finally:
            self.permissions.discard(future)
            if not future.done():
                future.cancel()

    def close_admission(self) -> None:
        self.closing = True
        for future in self.permissions:
            if not future.done():
                future.cancel()
        for task in tuple(self.tasks):
            if task is not asyncio.current_task():
                task.cancel()
        for future in self.threads:
            if future not in getattr(self, '_cleanup_threads', ()):
                future.cancel()  # Only succeeds for work which has not begun.

    def cleanup_sync(self, function: Callable):
        """Retain a cleanup operation even after normal admission closes."""
        future = self._executor.submit(function)
        self.threads.add(future)
        if not hasattr(self, '_cleanup_threads'):
            self._cleanup_threads = set()
        self._cleanup_threads.add(future)
        return future

    async def drain(self, grace: float) -> tuple[str, ...]:
        self.close_admission()
        end = time.monotonic()+max(0, grace)
        tasks = [t for t in self.tasks if t is not asyncio.current_task()]
        if tasks:
            await asyncio.wait(tasks, timeout=max(0, end-time.monotonic()))
        while any(not f.done() for f in self.threads) and time.monotonic() < end:
            await asyncio.sleep(min(.02, max(0, end-time.monotonic())))
        pending = [f'task:{id(t)}' for t in tasks if not t.done()]
        pending += [f'thread:{id(f)}' for f in self.threads if not f.done()]
        for task in tasks:
            if task.done() and not task.cancelled():
                task.exception()
        for future in self.threads:
            if future.done() and not future.cancelled():
                future.exception()
        if not pending:
            self._executor.shutdown(wait=False, cancel_futures=True)
        return tuple(pending)
