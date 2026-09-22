"""Durable reservations for every actual model HTTP request in one host run."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import time
from uuid import UUID

from codeplus.run_policy import BudgetStop

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


def usage_violation(protocol, raw, input_upper, output_cap):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return 'invalid_provider_usage'
    input_keys = (('input_tokens','cache_read_input_tokens','cache_creation_input_tokens')
                  if protocol == 'anthropic' else
                  ('prompt_tokens',) if protocol == 'openai-compat' else ('input_tokens',))
    output_key = 'completion_tokens' if protocol == 'openai-compat' else 'output_tokens'
    known = {}
    for key in (*input_keys, output_key, 'total_tokens'):
        value = raw.get(key)
        if value is None:
            continue
        if type(value) is not int or value < 0:
            return 'invalid_provider_usage'
        known[key] = value
    input_lower = sum(known.get(key,0) for key in input_keys)
    output_lower = known.get(output_key,0)
    if (input_lower > input_upper or output_lower > output_cap
            or known.get('total_tokens',0) > input_upper+output_cap):
        return 'provider_usage_exceeds_reservation'
    if 'total_tokens' in known and known['total_tokens'] < input_lower+output_lower:
        return 'inconsistent_provider_usage'
    if ('total_tokens' in known and all(k in known for k in (*input_keys,output_key))
            and known['total_tokens'] != input_lower+output_lower):
        return 'inconsistent_provider_usage'
    for key in ('prompt_tokens_details','input_tokens_details','completion_tokens_details','output_tokens_details'):
        details = raw.get(key)
        if details is None:
            continue
        if not isinstance(details, dict):
            return 'invalid_provider_usage'
        for counter in ('cached_tokens','reasoning_tokens'):
            value = details.get(counter)
            if value is not None and (type(value) is not int or value < 0):
                return 'invalid_provider_usage'
            bound = input_lower if counter == 'cached_tokens' else output_lower
            bound_known = all(k in known for k in input_keys) if counter == 'cached_tokens' else output_key in known
            if value is not None and bound_known and value > bound:
                return 'inconsistent_provider_usage'
    for key in ('prompt_cache_hit_tokens','prompt_cache_miss_tokens'):
        value=raw.get(key)
        if value is not None and (type(value) is not int or value<0):
            return 'invalid_provider_usage'
        if value is not None and all(k in known for k in input_keys) and value>input_lower:
            return 'inconsistent_provider_usage'
    hit,miss=raw.get('prompt_cache_hit_tokens'),raw.get('prompt_cache_miss_tokens')
    if hit is not None and miss is not None and 'prompt_tokens' in known and hit+miss!=input_lower:
        return 'inconsistent_provider_usage'
    cached=(raw.get('prompt_tokens_details') or {}).get('cached_tokens')
    if hit is not None and cached is not None and hit!=cached:
        return 'inconsistent_provider_usage'
    return None


def known_usage_lower(protocol, raw):
    if not isinstance(raw, dict):
        return 0
    keys = (('input_tokens','cache_read_input_tokens','cache_creation_input_tokens','output_tokens')
            if protocol == 'anthropic' else
            ('prompt_tokens','completion_tokens') if protocol == 'openai-compat' else ('input_tokens','output_tokens'))
    valid = lambda value: value if type(value) is int and value >= 0 else 0
    return max(sum(valid(raw.get(key)) for key in keys), valid(raw.get('total_tokens')))


@dataclass(frozen=True)
class RequestPermit:
    request_id: str
    delivery: object


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
        self.deadline = scope.deadline
        self.output_cap = scope.output_caps[purpose]

    async def before_send(self, request):
        scope = self.scope
        scope.check()
        upper = scope.meter.input_upper_bound(request.raw_body, output_cap=self.output_cap)
        if upper+self.output_cap > scope.meter.context_window:
            raise BudgetStop('context_limit', hard=True)
        finish = self.purpose in {'finalize', 'citation_repair'}
        if finish and upper > scope.finish_input_upper:
            raise BudgetStop('finish_input_limit', hard=True)
        mappings = []
        for mapped in request.mappings:
            path = mapped.path
            if request.protocol == 'openai-compat':
                identity = path[:-1]+('tool_call_id',)
            elif request.protocol == 'openai':
                identity = path[:-1]+('call_id',)
            else:
                identity = path[:4]+('tool_use_id',)
            span = mapped.source
            mappings.append(MappedSpan(UUID(span.candidate_id), Span(start=span.source_start, end=span.source_end),
                path, Span(start=span.body_start, end=span.body_end), identity))
        purpose = 'explore' if self.purpose == 'agent' else self.purpose
        protocol = {'openai': 'responses', 'openai-compat': 'compat', 'anthropic': 'anthropic'}[request.protocol]
        gateway_permit = None
        reserved = False
        try:
            # The gateway checks complete actual JSON paths, source hashes and
            # host call IDs before any model reservation is admitted.
            gateway_permit = scope.gateway.prepare(request.raw_body, tuple(mappings), purpose=purpose,
                protocol=protocol, request_id=UUID(request.request_id))
            scope.gateway.retain_prepared_window(gateway_permit)
            with scope.catalog._db.transaction(write=True) as db:
                scope.sources._active(db)
                used, = db.execute('SELECT COALESCE(SUM(charged),0) FROM model_requests WHERE run_id=?', (scope.run_id,)).fetchone()
                exploration, = db.execute("SELECT COALESCE(SUM(charged),0) FROM model_requests WHERE run_id=? AND purpose NOT IN ('finalize','citation_repair')", (scope.run_id,)).fetchone()
                if used > scope.budget.total_tokens:
                    raise BudgetStop('token_budget', hard=True)
                if not finish and (exploration+upper+self.output_cap > scope.budget.total_tokens-scope.budget.finish_reserve_tokens
                        or time.monotonic() >= scope.exploration_deadline):
                    raise BudgetStop('token_budget' if exploration+upper+self.output_cap > scope.budget.total_tokens-scope.budget.finish_reserve_tokens else 'time_budget')
                if used+upper+self.output_cap > scope.budget.total_tokens:
                    raise BudgetStop('token_budget', hard=True)
                if finish and db.execute('SELECT 1 FROM model_requests WHERE run_id=? AND purpose=?', (scope.run_id, self.purpose)).fetchone():
                    raise BudgetStop('finish_slot_already_used', hard=True)
                db.execute('INSERT INTO model_requests(request_id,run_id,purpose,protocol,input_upper,output_cap,charged,state,body_sha256) VALUES(?,?,?,?,?,?,?,\'reserved\',?)',
                    (request.request_id, scope.run_id, self.purpose, request.protocol, upper, self.output_cap,
                     upper+self.output_cap, hashlib.sha256(request.raw_body).hexdigest()))
                reserved = True
            return RequestPermit(request.request_id, gateway_permit)
        except BaseException:
            if gateway_permit is not None:
                scope.gateway.settle(gateway_permit, 'not_sent')
            if reserved:
                with scope.catalog._db.transaction(write=True) as db:
                    db.execute("UPDATE model_requests SET state='not_sent',charged=0 WHERE request_id=?", (request.request_id,))
            raise

    async def settled(self, permit, result):
        if permit is None:
            return
        scope = self.scope
        violation = None
        with scope.catalog._db.transaction(write=True) as db:
            row = db.execute('SELECT protocol,input_upper,output_cap,charged,state FROM model_requests WHERE request_id=? AND run_id=?',
                             (permit.request_id, scope.run_id)).fetchone()
            if row is None or row[4] != 'reserved':
                raise RuntimeError('request reservation is missing or already settled')
            eligible=evidence_delivery(row[0],result)
            violation = usage_violation(row[0], result.raw_usage, row[1], row[2])
            usage = usage_total(row[0], result.raw_usage)
            charge = (0 if result.delivery == 'not_sent' else
                      usage[3] if usage and result.delivery == 'confirmed' else
                      max(row[3], known_usage_lower(row[0], result.raw_usage)))
            if usage and (usage[0] > row[1] or usage[1] > row[2] or usage[3] > row[1]+row[2]):
                violation = 'provider_usage_exceeds_reservation'
            identity_violation = None
            if eligible == 'confirmed' and result.response_model != scope.meter.response_model:
                identity_violation = 'provider_model_identity_missing' if result.response_model is None else 'provider_model_identity_changed'
                violation = violation or identity_violation
            outcome=asdict(result)
            if identity_violation:outcome['identity_violation']=identity_violation
            db.execute('UPDATE model_requests SET state=?,charged=?,raw_usage=?,terminal=?,outcome=?,violation=? WHERE request_id=?',
                (result.delivery, charge, encode(result.raw_usage), result.terminal, encode(outcome), violation, permit.request_id))
        # Preserve actual accounting even when delivery/citation settlement fails.
        scope.gateway.settle(permit.delivery, 'unknown' if violation else eligible)
        if violation:
            scope.hard_failure = violation
            raise BudgetStop(violation, hard=True)
        if eligible=='rejected' and result.delivery!='not_sent':
            scope.hard_failure='answer_provider_failed'
            raise RuntimeError('answer_provider_failed')


def aggregate_usage(scope):
    current = scope.catalog.get_run(scope.lease.run.run_id).usage
    with scope.catalog._db.transaction() as db:
        rows = list(db.execute('SELECT protocol,raw_usage,state FROM model_requests WHERE run_id=?', (scope.run_id,)))
    totals = [0, 0, 0, 0]
    known = True
    cache_known = True
    for protocol, raw, state in rows:
        if state == 'not_sent':
            continue
        value = usage_total(protocol, json.loads(raw) if raw else None)
        if value is None or state != 'confirmed':
            known = False
            cache_known = False
        else:
            cache_known = cache_known and value[2] is not None
            totals = [a+(b if b is not None else 0) for a,b in zip(totals, value)]
    return current.model_copy(update={
        'input_tokens': totals[0] if known else None, 'output_tokens': totals[1] if known else None,
        'cached_tokens': totals[2] if cache_known else None, 'total_tokens': totals[3] if known else None,
        'elapsed_ms': int((time.monotonic()-scope.started)*1000)})
