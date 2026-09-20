"""Persisted identities and the small retrieval contract shared by the CLI and service."""

from dataclasses import dataclass
from hashlib import sha256
import json

from codeplus.config import KnowledgeConfig
from . import QUERY_INSTRUCTION


def fingerprint(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def profile(config: KnowledgeConfig) -> dict:
    if not 0 <= config.chunk_overlap < config.chunk_tokens <= config.max_input_tokens:
        raise ValueError("Require 0 <= chunk_overlap < chunk_tokens <= max_input_tokens")
    return {
        "model": config.embedding_model, "revision": config.embedding_revision,
        "dimension": config.embedding_dimension, "max_input_tokens": config.max_input_tokens,
        "tokenizer": [config.embedding_model, config.embedding_revision],
        "document_template": "raw", "query_template": f"Instruct: {QUERY_INSTRUCTION}\nQuery:{{query}}",
        "encoding": "cpu/float32/left-padding/last-token/l2-v1",
        "parsing": "markdown-it-4/lines-v1", "chunking": "sentence-offsets-v2/llama-index-0.14.24",
        "chunk_tokens": config.chunk_tokens, "chunk_overlap": config.chunk_overlap,
    }


@dataclass
class SourceSpan:
    kind: str
    heading_path: list[str]
    line_start: int | None
    line_end: int | None
    char_start: int
    char_end: int
    format: str = "markdown"
    page: int | None = None
    paragraph: int | None = None
    table: int | None = None
    row: int | None = None
    column: int | None = None


@dataclass
class ParsedBlock:
    text: str
    source: SourceSpan


@dataclass
class Chunk:
    id: str
    doc_id: str
    generation_id: str
    ordinal: int
    text: str
    source_spans: list[SourceSpan]


@dataclass
class SearchHit:
    chunk_id: str
    doc_id: str
    generation_id: str
    text: str
    source_uri: str
    original_path: str
    source_spans: list[SourceSpan]
    score: float
    score_type: str = "cosine_similarity"


@dataclass
class SearchResult:
    query: str
    kb_id: str
    revision: int
    retrieval: dict
    hits: list[SearchHit]
