"""R04 interface experiments against real host transforms and SDK MockTransport.

No production strategy is installed, no Agent/model end-to-end claim is made.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from codeplus.agent import Agent
from codeplus.context.manager import apply_tool_result_budget, auto_compact, make_persisted_preview
from codeplus.conversation import ConversationManager, Message, ToolResultBlock, ToolUseBlock
from codeplus.conversation_pairing import ensure_tool_pairing
from codeplus.serialization import build_messages
from codeplus.tools import ToolRegistry
from codeplus.tools.base import StreamEnd, TextDelta


PATH = Path(__file__).resolve().parents[1] / "probes/host_contract/contract.py"
SPEC = importlib.util.spec_from_file_location("r04_host_contract_probe", PATH)
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


def conversation(text, blocks=None, *, orphan=False):
    conv = ConversationManager()
    if not orphan:
        conv.history.append(Message("assistant", "", tool_uses=[ToolUseBlock("call", "knowledge_open", {})]))
    conv.history.append(Message("user", "", tool_results=[ToolResultBlock("call", text, content_blocks=blocks)]))
    return conv


def payload(conv, protocol):
    key = "input" if protocol == "openai" else "messages"
    return {key: build_messages(ensure_tool_pairing(conv.get_messages()), protocol)}


BINDING = probe.RunBinding("run-1", "kb-1", "revision-1")


def receipts(body, spans, protocol="anthropic", purpose="agent", delivery="confirmed", *, candidates=None):
    # Synthetic trusted fixture only. Production candidates come from successful
    # core calls and archived source maps, never from the provider body.
    if candidates is None:
        candidates = [probe.SourceCandidate(BINDING, s.call_id, s.source_id, s.source_start, s.text, True)
                      for s in spans]
    return probe.confirm_receipts(request_id="request-1", raw_body=json.dumps(body).encode(),
                                  protocol=protocol, purpose=purpose, delivery=delivery, spans=spans,
                                  binding=BINDING, candidates=candidates)


@pytest.mark.parametrize("protocol,path", [
    ("anthropic", ("messages", 1, "content", 0, "content")),
    ("openai", ("input", 1, "output")),
    ("openai-compat", ("messages", 1, "content")),
])
def test_real_serializers_and_unicode_offsets(protocol, path):
    text = "甲😀\r\nidentical / identical"
    span = probe.MappedSpan("call", "doc-v1", 71, text[5:], path, 5)
    body = payload(conversation(text), protocol)
    accepted = receipts(body, [span], protocol)
    assert len(accepted) == 1
    assert (accepted[0].start, accepted[0].end) == (71, 71 + len(text[5:]))
    assert accepted[0].text_sha256 == sha256(text[5:].encode()).hexdigest()
    # Same words in another occurrence are not located with str.find().
    wrong = probe.MappedSpan("wrong-call", "doc-v1", 71, text[5:], path, 5)
    assert receipts(body, [wrong], protocol) == []


def test_single_spill_only_delivered_prefix_not_full_body(tmp_path):
    agent = Agent(None, ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    text = "a" * 60_000 + "never sent tail"
    trimmed = agent._maybe_persist_or_truncate("call", text)
    assert trimmed == make_persisted_preview(text, agent.session_dir / "call.txt")
    body = payload(conversation(trimmed), "anthropic")
    path = ("messages", 1, "content", 0, "content")
    full = probe.MappedSpan("call", "doc", 0, text, path, 0)
    assert not receipts(body, [full])
    # Transform provenance explicitly adds the wrapper length; never scans body.
    offset = len(trimmed) - len("a" * 2_000 + "\n...\n</persisted-output>")
    prefix = probe.MappedSpan("call", "doc", 0, text[:2_000], path, offset)
    assert receipts(body, [prefix])[0].end == 2_000


def test_aggregate_spill_invalidates_original_mapping(tmp_path):
    results = [ToolResultBlock(str(i), chr(65 + i) * 49_000) for i in range(5)]
    apply_tool_result_budget(results, tmp_path)
    assert results[0].content != "A" * 49_000
    assert results[1].content == "B" * 49_000
    conv = ConversationManager(history=[
        Message("assistant", "", tool_uses=[ToolUseBlock(str(i), "knowledge_open", {}) for i in range(5)]),
        Message("user", "", tool_results=results),
    ])
    spans = [probe.MappedSpan(str(i), str(i), 0, chr(65+i) * 49_000,
                             ("messages", 1, "content", i, "content"), 0) for i in range(5)]
    assert [r.source_id for r in receipts(payload(conv, "anthropic"), spans)] == ["1", "2", "3", "4"]


def test_anthropic_content_blocks_override_ui_text():
    conv = conversation("UI only full original", [{"type": "text", "text": "actual excerpt"}])
    old = probe.MappedSpan("call", "doc", 0, "UI only full original", ("messages", 1, "content", 0, "content"), 0)
    actual = probe.MappedSpan("call", "doc", 30, "actual excerpt", ("messages", 1, "content", 0, "content", 0, "text"), 0)
    assert receipts(payload(conv, "anthropic"), [old]) == []
    assert receipts(payload(conv, "anthropic"), [actual])[0].start == 30
    # Other two serializers use content, proving provider-specific body ownership.
    assert payload(conv, "openai")["input"][1]["output"] == "UI only full original"


def test_pair_repair_orphan_and_synthetic_error_cannot_be_evidence():
    path = ("messages", 0, "content", 0, "content")
    candidate = probe.MappedSpan("call", "doc", 0, "original", path, 0)
    assert not receipts(payload(conversation("original", orphan=True), "anthropic"), [candidate])
    missing = ConversationManager(history=[Message("assistant", "", tool_uses=[ToolUseBlock("call", "knowledge_open", {})])])
    repaired = payload(missing, "anthropic")
    block = repaired["messages"][1]["content"][0]
    assert block["is_error"]
    synthetic = probe.MappedSpan("call", "doc", 0, block["content"], ("messages", 1, "content", 0, "content"), 0)
    assert not receipts(repaired, [synthetic])


@pytest.mark.asyncio
async def test_real_compact_summary_does_not_grant_new_evidence(tmp_path):
    conv = conversation("evidence before compaction" + "a" * 20_000)
    conv.history.extend(Message("user", "unrelated" * 4_000) for _ in range(10))
    calls = []

    class SummaryClient:
        async def stream(self, *args, **kwargs):
            calls.append(args[0])
            yield TextDelta("<summary>evidence before compaction</summary>")
            yield StreamEnd("end_turn", 100, 20)

    result = await auto_compact(conv, SummaryClient(), 200_000, tmp_path, manual=True)
    assert result is not None and calls
    text = "evidence before compaction"
    path = ("messages", 1, "content", 0, "content")
    span = probe.MappedSpan("call", "doc", 0, text, path, 0)
    assert not receipts(payload(conv, "anthropic"), [span])
    # Even a complete request made solely to summarize does not qualify for answer evidence.
    assert not receipts(payload(conversation(text), "anthropic"), [span], purpose="compact")


@pytest.mark.parametrize("delivery", ["prepared", "not_sent", "rejected", "unknown"])
def test_no_receipt_for_ambiguous_or_failed_delivery(delivery):
    span = probe.MappedSpan("call", "doc", 0, "body", ("messages", 1, "content", 0, "content"), 0)
    assert not receipts(payload(conversation("body"), "anthropic"), [span], delivery=delivery)


def test_parallel_tools_atomic_and_failed_accepted_attempts_stay_counted():
    ledger = probe.Ledger(1_000, 300, 4)
    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = list(pool.map(lambda _: ledger.accept_tool(allowed=True), range(40)))
    assert sum(accepted) == ledger.accepted == 4
    assert ledger.rejected == 36
    assert not ledger.accept_tool(allowed=False)
    # Failure after admission has no refund operation; another run is independent.
    other = probe.Ledger(1_000, 300, 4)
    assert other.accept_tool(allowed=True) and other.accepted == 1


def test_unknown_compact_retry_consumes_reserve_but_keeps_closing_budget():
    ledger = probe.Ledger(1_000, 300, 4)
    ledger.reserve("compact1", 200, 100)
    ledger.settle("compact1", probe.normalize_usage("openai", None))
    ledger.reserve("compact2", 200, 100)
    ledger.settle("compact2", probe.normalize_usage("openai", None))
    with pytest.raises(probe.BudgetStop):
        ledger.reserve("agent", 200, 100)
    ledger.reserve("finalize", 200, 100, closing=True)
    ledger.settle("finalize", probe.normalize_usage("openai", {"input_tokens": 200, "output_tokens": 50, "input_tokens_details": {"cached_tokens": 90}}))
    assert ledger.charged == 850 and ledger.unknown_calls == 2


@pytest.mark.parametrize("protocol", ["anthropic", "openai", "openai-compat"])
@pytest.mark.asyncio
async def test_sdk_final_body_cap_and_scoped_retry_disabling(protocol):
    captured = []
    observed = []

    async def before_send(request):
        observed.append(request.content)

    async def fail(request):
        captured.append(request.content)
        return httpx.Response(429, json={"error": {"type": "rate_limit_error", "message": "synthetic"}}, request=request)

    sdk_type = AsyncAnthropic if protocol == "anthropic" else AsyncOpenAI
    parent = sdk_type(api_key="synthetic-r04-not-a-secret", base_url="https://r04.invalid")
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail), event_hooks={"request": [before_send]}) as http:
            scoped = parent.with_options(max_retries=0, timeout=1.0, http_client=http)
            args = with_cap_args(protocol)
            operation = (scoped.messages.create if protocol == "anthropic" else
                         scoped.responses.create if protocol == "openai" else scoped.chat.completions.create)
            with pytest.raises(Exception) as error:
                await operation(**args)
            assert error.value.__class__.__name__ == "RateLimitError"
            assert len(captured) == len(observed) == 1
            assert captured == observed  # exact SDK-produced HTTP body observed pre-send
            decoded = json.loads(captured[0])
            key = "max_output_tokens" if protocol == "openai" else "max_tokens"
            assert decoded[key] == 37
            assert parent.max_retries == 2 and scoped.max_retries == 0
            assert parent.timeout != scoped.timeout
            await scoped.close()
            assert http.is_closed and not parent.is_closed()
    finally:
        await parent.close()


def test_registry_copy_reuses_instances_but_new_run_tools_do_not():
    from codeplus.agents.tool_filter import clone_registry_for_fork
    from codeplus.tools.base import Tool, ToolResult
    from pydantic import BaseModel

    class Params(BaseModel):
        query: str

    class BoundTool(Tool):
        name = "knowledge_search"
        description = "Synthetic run-bound interface fixture"
        params_model = Params

        def __init__(self, binding):
            self.binding = binding

        async def execute(self, params):
            return ToolResult(params.query)

    parent = ToolRegistry()
    first = BoundTool(BINDING)
    parent.register(first)
    assert clone_registry_for_fork(parent).get(first.name) is first
    second_registry = ToolRegistry()
    second_registry.register(BoundTool(replace(BINDING, run_id="run-2")))
    assert second_registry.get(first.name) is not first
    assert first.binding.run_id == "run-1"


def with_cap_args(protocol):
    args = {"model": "synthetic-model"}
    args["input" if protocol == "openai" else "messages"] = [{"role": "user", "content": "fixture"}]
    return probe.with_output_cap(protocol, args, 37)


@pytest.mark.parametrize("protocol", ["anthropic", "openai", "openai-compat"])
@pytest.mark.asyncio
async def test_sdk_wraps_presend_stop_but_trusted_gate_restores_not_sent(protocol):
    captured = []
    gate = probe.PreSendGate("request-budget-refusal")

    async def before_send(request):
        gate.reject("no remaining token reservation")

    async def transport(request):
        captured.append(request.content)
        return httpx.Response(500, json={}, request=request)

    sdk_type = AsyncAnthropic if protocol == "anthropic" else AsyncOpenAI
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport),
                                event_hooks={"request": [before_send]}) as http:
        async with sdk_type(api_key="synthetic-r04-not-a-secret", base_url="https://r04.invalid",
                            http_client=http, max_retries=0) as scoped:
            operation = (scoped.messages.create if protocol == "anthropic" else
                         scoped.responses.create if protocol == "openai" else scoped.chat.completions.create)
            with pytest.raises(Exception) as caught:
                await operation(**with_cap_args(protocol))
            assert caught.value.__class__.__name__ == "APIConnectionError"
            assert captured == [] and scoped.max_retries == 0
            assert gate.not_sent_reason == "no remaining token reservation"
            with pytest.raises(probe.BudgetStop, match="no remaining"):
                gate.reraise(caught.value)
            # No exception-text inference and no cross-request contamination.
            other = probe.PreSendGate("another-request")
            with pytest.raises(type(caught.value)):
                other.reraise(caught.value)


def test_truncated_or_unterminated_text_never_counts_as_completed():
    for terminal in (None, "length", "max_tokens", "incomplete", "content_filter", "tool_calls"):
        assert probe.terminal_status(terminal=terminal, validated=True, stop="finish") == "incomplete"
    assert probe.terminal_status(terminal="stop", validated=True, stop="iteration_limit") == "incomplete"
    assert probe.terminal_status(terminal="stop", validated=True, stop="cancelled") == "cancelled"
    assert probe.terminal_status(terminal="stop", validated=True, stop="error") == "failed"
    assert probe.terminal_status(terminal="stop", validated=True, stop="finish") == "completed"


def test_thinking_budget_cannot_silently_exceed_request_cap():
    args = {"thinking": {"type": "enabled", "budget_tokens": 1024}}
    with pytest.raises(probe.BudgetStop):
        probe.with_output_cap("anthropic", args, 512)
    assert args == {"thinking": {"type": "enabled", "budget_tokens": 1024}}


@pytest.mark.parametrize("protocol,path", [
    ("anthropic", ("messages", 1, "content", 0, "content")),
    ("openai", ("input", 1, "output")),
    ("openai-compat", ("messages", 1, "content")),
])
def test_failed_tool_text_is_not_evidence_across_protocols(protocol, path):
    conv = conversation("original")
    conv.history[-1].tool_results[0].is_error = True
    span = probe.MappedSpan("call", "doc", 0, "original", path, 0)
    candidate = probe.SourceCandidate(BINDING, "call", "doc", 0, "original", False)
    assert not receipts(payload(conv, protocol), [span], protocol, candidates=[candidate])
    assert not receipts(payload(conv, protocol), [span], protocol, candidates=[])


@pytest.mark.parametrize("field,value", [("run_id", "other"), ("kb_id", "other"), ("revision_id", "other")])
def test_candidate_scope_cannot_be_forged_by_body(field, value):
    span = probe.MappedSpan("call", "doc", 0, "original", ("input", 1, "output"), 0)
    candidate = probe.SourceCandidate(replace(BINDING, **{field: value}), "call", "doc", 0, "original", True)
    assert not receipts(payload(conversation("original"), "openai"), [span], "openai", candidates=[candidate])


def test_exact_source_offset_and_partial_mapping():
    original = "same / same"
    path = ("input", 1, "output")
    candidate = probe.SourceCandidate(BINDING, "call", "doc", 100, original, True)
    span = probe.MappedSpan("call", "doc", 107, "same", path, 7)
    accepted = receipts(payload(conversation(original), "openai"), [span], "openai", candidates=[candidate])
    assert accepted[0].start == 107 and accepted[0].body_start == 7
    assert not receipts(payload(conversation(original), "openai"), [replace(span, source_start=106)],
                        "openai", candidates=[candidate])


def test_usage_missing_breakdown_is_unknown_while_known_total_is_preserved():
    usage = probe.normalize_usage("openai", {"input_tokens": 40, "output_tokens": 10})
    assert usage.input_uncached is None and usage.cache_read is None and usage.total == 50
    ledger = probe.Ledger(1_000, 100, 1)
    ledger.reserve("agent", 100, 20)
    ledger.settle("agent", usage)
    assert ledger.charged == 50 and ledger.observed["output"] == 10
    assert ledger.unknown_components["cache_read"] == 1
    missing = probe.normalize_usage("anthropic", {"input_tokens": 40, "output_tokens": 10})
    assert missing.cache_read is None and missing.total is None
    assert probe.normalize_usage("openai-compat", None).total is None
    exact = probe.normalize_usage("anthropic", {"input_tokens": 40, "output_tokens": 10,
                                               "cache_read_input_tokens": 20, "cache_creation_input_tokens": 5})
    assert exact.total == 75


def test_budget_overrun_stops_future_requests_and_ids_are_never_reused():
    ledger = probe.Ledger(1_000, 100, 1)
    ledger.reserve("agent", 10, 10)
    with pytest.raises(probe.BudgetStop):
        ledger.settle("agent", probe.Usage(30, 10, 0, 0))
    assert ledger.charged == 40
    with pytest.raises(probe.BudgetStop):
        ledger.reserve("after", 1, 1, closing=True)
    assert not ledger.accept_tool(allowed=True)
    second = probe.Ledger(1_000, 100, 1)
    second.reserve("agent", 10, 10)
    second.settle("agent", probe.Usage(5, 5, 0, 0))
    with pytest.raises(ValueError, match="duplicate"):
        second.reserve("agent", 10, 10)


@pytest.mark.asyncio
async def test_current_host_compact_swallows_budget_exception_design_gap(tmp_path):
    """A passing observation of a current gap; not a fixed-host acceptance test."""
    conv = conversation("a" * 20_000)
    conv.history.extend(Message("user", "unrelated" * 4_000) for _ in range(10))

    class StoppedClient:
        async def stream(self, *args, **kwargs):
            raise probe.BudgetStop("synthetic hard limit")
            yield  # pragma: no cover

    result = await auto_compact(conv, StoppedClient(), 200_000, tmp_path, manual=True)
    assert isinstance(result, str) and "synthetic hard limit" in result


@pytest.mark.asyncio
async def test_current_compact_cancellation_propagates(tmp_path):
    import asyncio
    conv = conversation("a" * 20_000)
    conv.history.extend(Message("user", "unrelated" * 4_000) for _ in range(10))

    class CancelledClient:
        async def stream(self, *args, **kwargs):
            raise asyncio.CancelledError()
            yield  # pragma: no cover

    with pytest.raises(asyncio.CancelledError):
        await auto_compact(conv, CancelledClient(), 200_000, tmp_path, manual=True)
