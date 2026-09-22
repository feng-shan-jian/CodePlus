"""Closed, bounded JSON framing. No executable deserialization or path requests."""

import asyncio
import json
import math
import struct
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .._schema import Record, Sha256, Text
from ..capabilities import ModelInput, RequestContext
from ..domain import ErrorCode, RagError
from ..profiles import ModelProfile

VERSION = 1
HARD_FRAME_LIMIT = 1048576


def protocol_error(message='invalid local worker protocol'):
    return RagError(ErrorCode.WORKER_PROTOCOL, message, stage='ipc')


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise protocol_error('duplicate JSON key')
        result[key] = value
    return result


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise protocol_error('nonfinite JSON number')
    return result


def decode(data: bytes):
    try:
        result = json.loads(data.decode('utf-8'), object_pairs_hook=_pairs,
                            parse_float=_finite_float,
                            parse_constant=lambda _: (_ for _ in ()).throw(protocol_error('nonfinite JSON number')))
        if not isinstance(result, dict):
            raise protocol_error('frame must contain an object')
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise protocol_error() from exc


def encode(value, limit=HARD_FRAME_LIMIT):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if not 0 < len(data) <= min(limit, HARD_FRAME_LIMIT):
        raise protocol_error('frame length exceeds limit')
    return struct.pack('!I', len(data)) + data


async def read_frame(reader, *, limit=HARD_FRAME_LIMIT, timeout=5.0):
    # One deadline covers both header and body; length is checked BEFORE body read.
    async with asyncio.timeout(timeout):
        length, = struct.unpack('!I', await reader.readexactly(4))
        if not 0 < length <= min(limit, HARD_FRAME_LIMIT):
            raise protocol_error('frame length exceeds limit')
        return decode(await reader.readexactly(length)), length


async def write_frame(writer, value, *, limit=HARD_FRAME_LIMIT, timeout=5.0):
    writer.write(encode(value, limit))
    await asyncio.wait_for(writer.drain(), timeout)


def parse(cls, value):
    try:
        return cls.model_validate_json(json.dumps(value, allow_nan=False))
    except (ValueError, TypeError) as exc:
        # Pydantic messages can contain raw document text or tokens. Do not relay.
        raise protocol_error('message does not match closed schema') from exc


class Hello(Record):
    type: Literal['hello']
    protocol: Literal[1]
    token: Sha256
    owner_id: UUID
    instance_id: UUID
    implementation_digest: Sha256
    runtime_fingerprint: Sha256
    clock_domain: Sha256

    @field_validator('protocol', mode='before')
    @classmethod
    def strict_protocol(cls, value):
        if type(value) is not int or value != VERSION:
            raise ValueError('unsupported protocol')
        return value


class Submit(Record):
    type: Literal['submit']
    operation: Literal['documents', 'query', 'rerank']
    profile: ModelProfile
    profile_fingerprint: Sha256
    context: RequestContext
    items: Annotated[tuple[ModelInput, ...], Field(min_length=1, max_length=4)]
    query: str | None = None

    @model_validator(mode='after')
    def coherent(self):
        if self.profile.identity != self.profile_fingerprint:
            raise ValueError('profile identity mismatch')
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError('duplicate candidate')
        if self.operation == 'rerank':
            if self.profile.capability != 'rerank' or not self.query or not self.query.strip():
                raise ValueError('rerank needs profile and query')
        elif self.profile.capability != 'embedding' or self.query is not None:
            raise ValueError('embedding capability mismatch')
        if self.operation == 'query' and len(self.items) != 1:
            raise ValueError('one query required')
        return self


class Cancel(Record):
    type: Literal['cancel']
    request_id: UUID


class Status(Record):
    type: Literal['status']


class Goodbye(Record):
    type: Literal['goodbye']
