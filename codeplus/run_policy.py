"""Optional tool bindings and passive source delivery records for the host."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
import time
from typing import Any, Callable, Literal, Protocol
from uuid import uuid4


ModelPurpose = Literal['agent', 'compact']


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


class ModelCallControl(Protocol):
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
class ArtifactSave:
    status: Literal['pending', 'saved', 'failed', 'interrupted']
    path: str
    sha256: str | None = None
    size_bytes: int | None = None
    message: str = ''


@dataclass(frozen=True)
class RunOutcome:
    status: str
    reason: str
    run_id: str | None = None
    save: ArtifactSave | None = None
    research: dict[str, Any] | None = None


class RunScope(Protocol):
    tools: tuple[Any, ...]
    system_prompt: str
    outcome: RunOutcome | None
    public_answer: str | None

    def model_control(self, purpose: ModelPurpose) -> ModelCallControl: ...
    def tool_finished(self, call: Any, result: Any) -> None: ...
    async def finish(self, outcome: RunOutcome) -> None: ...
    async def aclose(self) -> None: ...


class RunExecutionPolicy(Protocol):
    async def start(self, context: HostRunContext) -> RunScope: ...


class RunTaskOwner:
    """Track real worker completion so cancellation cannot release its version pin early."""
    def __init__(self):
        self.closing = False
        self.threads: set[Future] = set()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='knowledge-run')

    async def run_sync(self, function: Callable, *args, **kwargs):
        if self.closing:
            raise RuntimeError('knowledge run is closed')
        future = self._executor.submit(function, *args, **kwargs)
        self.threads.add(future)
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        await asyncio.wait({wrapped})
        return wrapped.result()

    def close_admission(self):
        self.closing = True
        for future in self.threads:
            future.cancel()

    def cleanup_sync(self, function: Callable):
        future = self._executor.submit(function)
        self.threads.add(future)
        return future

    async def drain(self, grace: float) -> tuple[str, ...]:
        end = time.monotonic() + grace
        while any(not future.done() for future in self.threads) and time.monotonic() < end:
            await asyncio.sleep(min(.02, max(0, end-time.monotonic())))
        pending = tuple(f'thread:{id(future)}' for future in self.threads if not future.done())
        for future in self.threads:
            if future.done() and not future.cancelled():
                future.exception()
        if not pending:
            self._executor.shutdown(wait=False, cancel_futures=True)
        return pending
