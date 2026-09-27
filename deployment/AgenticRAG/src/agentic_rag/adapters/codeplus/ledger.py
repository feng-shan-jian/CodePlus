"""Passive records of actual model requests and confirmed source delivery."""
from dataclasses import asdict
import json
import time
from uuid import UUID

from ...domain import Span
from ...evidence import MappedSpan


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def usage_total(protocol, raw):
    """Return complete billable input/output counts, or None; never infer zero."""
    if not isinstance(raw, dict):
        return None
    def count(key):
        value = raw.get(key)
        return value if type(value) is int and value >= 0 else None
    if protocol == 'anthropic':
        values = [count(key) for key in ('input_tokens', 'output_tokens')]
        if any(value is None for value in values):
            return None
        # Standard Anthropic cache counters are optional, but explicit null or
        # malformed values make the complete input unknown.
        cached = []
        for key in ('cache_read_input_tokens', 'cache_creation_input_tokens'):
            value = count(key)
            if value is None:
                return None
            cached.append(value)
        inputs, outputs = values[0]+sum(cached), values[1]
        return inputs, outputs, sum(cached), inputs+outputs
    prefix = 'prompt' if protocol == 'openai-compat' else 'input'
    outkey = 'completion_tokens' if protocol == 'openai-compat' else 'output_tokens'
    inputs, outputs, total = count(prefix+'_tokens'), count(outkey), count('total_tokens')
    if any(value is None for value in (inputs, outputs, total)) or total != inputs+outputs:
        return None
    details = raw.get(prefix+'_tokens_details') or {}
    if not isinstance(details, dict):
        return None
    cached = details.get('cached_tokens', raw.get('prompt_cache_hit_tokens'))
    if cached is not None and (type(cached) is not int or cached < 0 or cached > inputs):
        return None
    # Reasoning/cache detail counters already belong to the reported totals.
    return inputs, outputs, cached, total


def evidence_delivery(protocol, result):
    """Transport completion and source eligibility are separate facts.

    Legal output truncation proves input delivery but never validates an answer.
    Explicit refusal/failure cannot confer source eligibility.
    """
    detail=result.terminal_details or {}
    if result.delivery in {'not_sent','rejected'}:
        return result.delivery
    if (result.terminal in {'failed','error','content_filter','refusal'}
            or detail.get('error_code') or detail.get('incomplete_reason') in {'content_filter','refusal'}):
        return 'rejected'
    if result.error_type:
        return 'unknown'
    if result.delivery!='confirmed':
        return result.delivery
    allowed={'openai-compat':{'stop','tool_calls','function_call','length'},
             'anthropic':{'end_turn','tool_use','max_tokens','stop_sequence','pause_turn'},
             'openai':{'completed'}}
    if result.terminal in allowed.get(protocol,set()):
        return 'confirmed'
    if protocol=='openai' and result.terminal=='incomplete' and detail.get('incomplete_reason')=='max_output_tokens':
        return 'confirmed'
    return 'unknown'


class ModelControl:
    def __init__(self, scope, purpose):
        self.scope, self.purpose = scope, purpose

    async def before_send(self, request):
        mappings = []
        for mapped in request.mappings:
            span = mapped.source
            if span.candidate_id not in self.scope.candidate_ids:
                continue
            path = mapped.path
            identity = (path[:-1]+('tool_call_id',) if request.protocol == 'openai-compat' else
                        path[:-1]+('call_id',) if request.protocol == 'openai' else
                        path[:4]+('tool_use_id',))
            mappings.append(MappedSpan(UUID(span.candidate_id), Span(start=span.source_start, end=span.source_end),
                path, Span(start=span.body_start, end=span.body_end), identity))
        protocol = {'openai':'responses', 'openai-compat':'compat', 'anthropic':'anthropic'}[request.protocol]
        delivery = self.scope.gateway.prepare(request.raw_body, tuple(mappings),
            purpose='explore' if self.purpose == 'agent' else self.purpose,
            protocol=protocol, request_id=UUID(request.request_id))
        return request.request_id, request.protocol, delivery

    async def settled(self, permit, result):
        if permit is None:
            return
        request_id, protocol, delivery = permit
        self.scope.model_requests.append({'request_id':request_id, 'protocol':protocol,
            'purpose':self.purpose, **asdict(result)})
        self.scope.gateway.settle(delivery, evidence_delivery(protocol, result))


def aggregate_usage(scope):
    current = scope.catalog.get_run(scope.lease.run.run_id).usage
    rows = [item for item in scope.model_requests if item['delivery'] != 'not_sent']
    values = [usage_total(item['protocol'], item['raw_usage']) if item['delivery'] == 'confirmed' else None
              for item in rows]
    totals = {key:sum(value[index] for value in values)
              if all(value is not None and value[index] is not None for value in values) else None
              for index,key in enumerate(('input_tokens','output_tokens','cached_tokens','total_tokens'))}
    return current.model_copy(update={**totals, 'elapsed_ms':int((time.monotonic()-scope.started)*1000)})
