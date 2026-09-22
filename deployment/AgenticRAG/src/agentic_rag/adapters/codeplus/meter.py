"""Conservative, model-bound input upper bounds, never a token estimator.

See docs/implementation-records/R12-budget-design.md for the byte-BPE proof.
The HTTP body whitelist intentionally rejects unsupported protocol features.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any

from ._vendor.deepseek_v41 import encode_messages

REVISION = 'dba1be0a40aa45a94ad051997016db3960a90277'
TOKENIZER_SHA256 = 'c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b'
ENCODER_SHA256 = '502bdaec8a3fd88ebc24c4721a7038fbe42f2063c664638127056107920035c1'
METER_VERSION = 'deepseek-v41-full-template-utf8-upper-v1'


class MeterUnavailable(ValueError):
    pass


def _keys(value: Any, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or value.keys()-allowed or required-value.keys():
        raise MeterUnavailable('unsupported_payload_fields')
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise MeterUnavailable('text_only_payload_required')
    # Reject lone surrogates, rather than silently changing the wire text.
    value.encode('utf-8', errors='strict')
    return value


class DeepSeekTextMeter:
    context_window = 1_000_000
    response_model = 'deepseek-flash'

    def __init__(self, tokenizer_path: str | Path, *, model: str, protocol: str,
                 base_url: str):
        if protocol != 'openai-compat' or model not in {'deepseek-chat', 'deepseek-reasoner'}:
            raise MeterUnavailable('verified_answer_meter_unavailable_for_model')
        if base_url.rstrip('/') not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'}:
            raise MeterUnavailable('verified_answer_meter_requires_official_endpoint')
        path = Path(tokenizer_path)
        if not path.is_absolute() or not path.is_file():
            raise MeterUnavailable('pinned_answer_tokenizer_absolute_path_required')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != TOKENIZER_SHA256:
            raise MeterUnavailable('pinned_answer_tokenizer_hash_mismatch')
        if importlib.metadata.version('tokenizers') != '0.23.2':
            raise MeterUnavailable('pinned_tokenizers_runtime_required')
        from ._vendor import deepseek_v41
        if hashlib.sha256(Path(deepseek_v41.__file__).read_bytes()).hexdigest() != ENCODER_SHA256:
            raise MeterUnavailable('pinned_prompt_encoder_hash_mismatch')
        spec = json.loads(raw)
        if spec['normalizer'] != {'type': 'Sequence', 'normalizers': []}:
            raise MeterUnavailable('tokenizer_normalizer_expansion')
        pre = spec['pre_tokenizer']
        if pre.get('type') != 'Sequence' or not pre.get('pretokenizers'):
            raise MeterUnavailable('unknown_pre_tokenizer')
        for item in pre['pretokenizers']:
            if item['type'] == 'Split':
                if item.get('behavior') not in {'Isolated', 'Contiguous'}:
                    raise MeterUnavailable('unknown_split_behavior')
            elif item['type'] != 'ByteLevel' or item.get('add_prefix_space') is not False:
                raise MeterUnavailable('tokenizer_prefix_expansion')
        bpe = spec['model']
        if (bpe['type'] != 'BPE' or bpe.get('unk_token') is not None
                or bpe.get('continuing_subword_prefix') or bpe.get('end_of_word_suffix')
                or bpe.get('byte_fallback') is not False):
            raise MeterUnavailable('unknown_bpe_expansion')
        if spec['post_processor']['type'] != 'ByteLevel':
            raise MeterUnavailable('unknown_post_processor')
        if any(not item.get('content') for item in spec['added_tokens']):
            raise MeterUnavailable('zero_width_added_token')
        self._control_literals = tuple(item['content'] for item in spec['added_tokens'])
        self.model = model
        self.thinking_mode = 'thinking' if model == 'deepseek-reasoner' else 'chat'
        self.identity = ':'.join((METER_VERSION, model, self.thinking_mode, REVISION, TOKENIZER_SHA256))

    def count(self, text: str) -> int:
        """Upper bound for source-window costs; separate from request accounting."""
        return len(_text(text).encode('utf-8'))

    def input_upper_bound(self, raw_body: bytes, *, output_cap: int) -> int:
        body = _keys(json.loads(raw_body),
            {'model', 'messages', 'tools', 'max_tokens', 'stream', 'stream_options'},
            {'model', 'messages', 'max_tokens', 'stream', 'stream_options'})
        self._check_literals(body)
        if (body['model'] != self.model or body['max_tokens'] != output_cap
                or body['stream'] is not True or body['stream_options'] != {'include_usage': True}):
            raise MeterUnavailable('request_mode_or_cap_changed')
        messages = copy.deepcopy(body['messages'])
        if not isinstance(messages, list) or not messages:
            raise MeterUnavailable('messages_required')
        for message in messages:
            _keys(message, {'role', 'content', 'tool_call_id', 'tool_calls', 'reasoning_content'}, {'role'})
            role = message['role']
            if role not in {'system', 'user', 'assistant', 'tool'}:
                raise MeterUnavailable('unsupported_message_role')
            if message.get('content') is not None:
                _text(message['content'])
            elif role != 'assistant':
                raise MeterUnavailable('text_content_required')
            if 'reasoning_content' in message:
                if role != 'assistant':
                    raise MeterUnavailable('invalid_reasoning_role')
                _text(message['reasoning_content'])
            if 'tool_call_id' in message:
                if role != 'tool':
                    raise MeterUnavailable('invalid_tool_result_role')
                _text(message['tool_call_id'])
            if role == 'tool' and 'tool_call_id' not in message:
                raise MeterUnavailable('tool_result_id_required')
            if 'tool_calls' in message:
                if role != 'assistant' or not isinstance(message['tool_calls'], list):
                    raise MeterUnavailable('invalid_tool_calls')
                for call in message['tool_calls']:
                    _keys(call, {'id', 'type', 'function'}, {'id', 'type', 'function'})
                    if call['type'] != 'function':
                        raise MeterUnavailable('function_tools_only')
                    _text(call['id'])
                    function = _keys(call['function'], {'name', 'arguments'}, {'name', 'arguments'})
                    _text(function['name'])
                    _text(function['arguments'])
                    try:
                        parsed_arguments = json.loads(function['arguments'])
                    except ValueError:
                        # The official encoder falls back to the original string.
                        parsed_arguments = function['arguments']
                    self._check_literals(parsed_arguments)
        tools = body.get('tools', [])
        if not isinstance(tools, list):
            raise MeterUnavailable('invalid_tools')
        for tool in tools:
            _keys(tool, {'type', 'function'}, {'type', 'function'})
            if tool['type'] != 'function':
                raise MeterUnavailable('function_tools_only')
            function = _keys(tool['function'], {'name', 'description', 'parameters'}, {'name'})
            _text(function['name'])
            if 'description' in function:
                _text(function['description'])
            if 'parameters' in function and not isinstance(function['parameters'], dict):
                raise MeterUnavailable('invalid_tool_schema')
        if tools:
            if messages[0]['role'] != 'system':
                messages.insert(0, {'role': 'system', 'content': ''})
            messages[0]['tools'] = tools
        rendered = encode_messages(messages, thinking_mode=self.thinking_mode, reasoning_effort=75)
        return self.count(rendered)

    def _check_literals(self, value: Any) -> None:
        if isinstance(value, str):
            _text(value)
            if any(token in value for token in self._control_literals):
                raise MeterUnavailable('unsupported_model_control_literal')
        elif isinstance(value, dict):
            for key, child in value.items():
                self._check_literals(key)
                self._check_literals(child)
        elif isinstance(value, list):
            for child in value:
                self._check_literals(child)

    def frozen_identity(self) -> dict[str, Any]:
        return {'meter': self.identity, 'model_alias': self.model, 'response_model': self.response_model,
                'model_family': 'DeepSeek-V4.1-Flash', 'thinking_mode': self.thinking_mode,
                'reasoning_effort': 75 if self.thinking_mode == 'thinking' else None,
                'context_window': self.context_window, 'template_revision': REVISION,
                'tokenizer_sha256': TOKENIZER_SHA256, 'bound_unit': 'rendered_utf8_bytes'}
