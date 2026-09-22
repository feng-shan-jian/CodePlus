"""R04 bounded interface experiment; not a host hook or production RAG engine.

Only checks request provenance, conservative accounting, and finalization primitives.
The real Agent and its three client implementations do not call these functions.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class RunBinding:
    run_id: str
    kb_id: str
    revision_id: str


@dataclass(frozen=True)
class SourceCandidate:
    """Trusted parsed-document text; start is a Unicode code-point offset.

    Original-file byte offsets (including BOM/CRLF) require the parser source map.
    This experiment does not implement that parser or reconstruct source from wire.
    """
    binding: RunBinding
    call_id: str
    source_id: str
    start: int
    text: str
    succeeded: bool


@dataclass(frozen=True)
class MappedSpan:
    call_id: str
    source_id: str
    source_start: int
    text: str
    # JSON path identifies an actual provider tool body, not a text search.
    path: tuple[str | int, ...]
    body_start: int


@dataclass(frozen=True)
class Receipt:
    binding: RunBinding
    request_id: str
    body_sha256: str
    source_id: str
    start: int
    end: int
    text_sha256: str
    path: tuple[str | int, ...]
    body_start: int


def _tool_texts(payload: dict, protocol: str) -> dict[tuple, tuple[str, str]]:
    """Only exact tool-result text nodes are eligible; summaries never are."""
    found = {}
    if protocol == "anthropic":
        for mi, msg in enumerate(payload.get("messages", [])):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for bi, block in enumerate(msg["content"]):
                if block.get("type") != "tool_result" or block.get("is_error"):
                    continue
                path = ("messages", mi, "content", bi, "content")
                body = block["content"]
                if isinstance(body, str):
                    found[path] = (block["tool_use_id"], body)
                elif isinstance(body, list):
                    for ti, text in enumerate(body):
                        if text.get("type") == "text":
                            found[path + (ti, "text")] = (block["tool_use_id"], text["text"])
    elif protocol == "openai":
        for i, item in enumerate(payload.get("input", [])):
            if item.get("type") == "function_call_output" and isinstance(item.get("output"), str):
                found[("input", i, "output")] = (item["call_id"], item["output"])
    elif protocol == "openai-compat":
        for i, item in enumerate(payload.get("messages", [])):
            if item.get("role") == "tool" and isinstance(item.get("content"), str):
                found[("messages", i, "content")] = (item["tool_call_id"], item["content"])
    else:
        raise ValueError("unsupported protocol")
    return found


def confirm_receipts(*, request_id: str, raw_body: bytes, protocol: str,
                     purpose: str, delivery: str, spans: list[MappedSpan],
                     binding: RunBinding, candidates: list[SourceCandidate]) -> list[Receipt]:
    """Complete provider response confirms submission, not internal model reading.

    Span metadata is trusted adapter provenance, never parsed from model arguments.
    All transformations must supply updated mappings; stale mappings fail closed.
    """
    if purpose not in {"agent", "finalize", "citation_repair"} or delivery != "confirmed":
        return []
    texts = _tool_texts(json.loads(raw_body), protocol)
    receipts = []
    for span in spans:
        # OpenAI serializers drop is_error. Success is authoritative out-of-band
        # provenance, not inferred from fields or words in the request body.
        eligible = any(
            candidate.succeeded and candidate.binding == binding
            and candidate.call_id == span.call_id and candidate.source_id == span.source_id
            and candidate.start <= span.source_start
            and span.source_start + len(span.text) <= candidate.start + len(candidate.text)
            and candidate.text[span.source_start - candidate.start:
                               span.source_start - candidate.start + len(span.text)] == span.text
            for candidate in candidates
        )
        if not eligible:
            continue
        call_id, body = texts.get(span.path, (None, ""))
        start, end = span.body_start, span.body_start + len(span.text)
        if (call_id != span.call_id or not span.text or span.source_start < 0
                or start < 0 or end > len(body) or body[start:end] != span.text):
            continue
        receipts.append(Receipt(binding, request_id, sha256(raw_body).hexdigest(), span.source_id,
                                span.source_start, span.source_start + len(span.text),
                                sha256(span.text.encode("utf-8")).hexdigest(), span.path, start))
    return receipts


@dataclass(frozen=True)
class Usage:
    input_uncached: int | None
    output: int | None
    cache_read: int | None
    cache_creation: int | None
    # OpenAI provides total input even if cache subdivision is unavailable.
    input_total: int | None = None

    @property
    def total(self) -> int | None:
        values = (self.input_uncached, self.output, self.cache_read, self.cache_creation)
        if any(value is not None and value < 0 for value in (*values, self.input_total)):
            raise ValueError("negative usage")
        if self.input_total is not None and self.output is not None:
            return self.input_total + self.output
        if any(value is None for value in values):
            return None
        return sum(values)


def normalize_usage(protocol: str, raw: dict[str, Any] | None) -> Usage:
    if protocol not in {"anthropic", "openai", "openai-compat"}:
        raise ValueError("unsupported protocol")
    if raw is None:
        return Usage(None, None, None, None)
    if protocol == "anthropic":
        return Usage(raw.get("input_tokens"), raw.get("output_tokens"),
                     raw.get("cache_read_input_tokens"), raw.get("cache_creation_input_tokens"))
    input_key, output_key, details_key = (
        ("input_tokens", "output_tokens", "input_tokens_details") if protocol == "openai"
        else ("prompt_tokens", "completion_tokens", "prompt_tokens_details"))
    total_input = raw.get(input_key)
    cached = (raw.get(details_key) or {}).get("cached_tokens")
    if total_input is not None and cached is not None and not 0 <= cached <= total_input:
        raise ValueError("inconsistent cache usage")
    return Usage(None if total_input is None or cached is None else total_input - cached,
                 raw.get(output_key), cached, 0, total_input)


class BudgetStop(Exception):
    """Must propagate through compact and tool generic exception handlers."""


class PreSendGate:
    """Per-attempt trusted side channel across SDK exception translation."""
    def __init__(self, request_id: str):
        self.request_id = request_id
        self.not_sent_reason: str | None = None

    def reject(self, reason: str) -> None:
        self.not_sent_reason = reason
        raise BudgetStop(reason)

    def reraise(self, error: Exception) -> None:
        if self.not_sent_reason is not None:
            raise BudgetStop(self.not_sent_reason) from error
        raise error


class Ledger:
    """Atomic reservation experiment: accepted failures remain charged."""
    def __init__(self, total: int, finish_reserve: int, tool_limit: int):
        if not 0 <= finish_reserve <= total or tool_limit < 0:
            raise ValueError("invalid budget")
        self.total = total
        self.finish_reserve = finish_reserve
        self.tool_limit = tool_limit
        self.accepted = 0
        self.rejected = 0
        self.charged = 0
        self.unknown_calls = 0
        self.observed = {"input_uncached": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
        self.unknown_components = {name: 0 for name in self.observed}
        self._pending: dict[str, int] = {}
        self._seen: set[str] = set()
        self.stopped = False
        self._lock = Lock()

    def accept_tool(self, *, allowed: bool) -> bool:
        with self._lock:
            if not allowed or self.stopped or self.accepted >= self.tool_limit:
                self.rejected += 1
                return False
            self.accepted += 1
            return True

    def reserve(self, call_id: str, input_bound: int, output_cap: int, *, closing=False) -> None:
        with self._lock:
            if self.stopped:
                raise BudgetStop("budget already stopped")
            if call_id in self._seen:
                raise ValueError("duplicate request id")
            ceiling = self.total - (0 if closing else self.finish_reserve)
            reservation = input_bound + output_cap
            if input_bound < 0 or output_cap <= 0 or self.charged + reservation > ceiling:
                raise BudgetStop("token reservation exhausted")
            self.charged += reservation
            self._pending[call_id] = reservation
            self._seen.add(call_id)

    def settle(self, call_id: str, usage: Usage) -> None:
        with self._lock:
            actual = usage.total
            reservation = self._pending.pop(call_id)
            for name in self.observed:
                value = getattr(usage, name)
                if value is None:
                    self.unknown_components[name] += 1
                else:
                    self.observed[name] += value
            if actual is None:
                self.unknown_calls += 1  # keep entire conservative reservation
            else:
                self.charged += actual - reservation
                if actual > reservation:
                    self.stopped = True
                    raise BudgetStop("provider exceeded reserved bound")


def with_output_cap(protocol: str, payload: dict, cap: int) -> dict:
    if protocol not in {"anthropic", "openai", "openai-compat"}:
        raise ValueError("unsupported protocol")
    if cap <= 0:
        raise BudgetStop("no output budget")
    result = dict(payload)
    result["max_output_tokens" if protocol == "openai" else "max_tokens"] = cap
    # A fixed thinking budget must fit inside the same total output cap.
    thinking = result.get("thinking")
    if thinking and thinking.get("type") == "enabled" and thinking.get("budget_tokens", 0) >= cap:
        raise BudgetStop("thinking budget cannot fit output cap")
    return result


def terminal_status(*, terminal: str | None, validated: bool, stop: str,
                    has_verified_partial: bool = False) -> str:
    if stop == "cancelled":
        return "cancelled"
    if stop == "error":
        return "failed"
    if stop == "finish" and terminal in {"end_turn", "stop", "completed"} and validated:
        return "completed"
    return "partial" if has_verified_partial else "incomplete"
