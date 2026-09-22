"""Shared formal R08 fixtures; no production module imports this file."""
import json
import os
from pathlib import Path
import runpy
from uuid import uuid4

from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot
from agentic_rag.ingestion.parsing import PARSER
from agentic_rag.ingestion.chunking import chunker_config
from agentic_rag.models import FrozenTokenizer
from agentic_rag.profiles import EmbeddingProfile, RerankProfile


def cache():
    return Path(os.environ.get('R08_MODEL_CACHE', str(Path.home()/'.cache/codeplus-agenticrag/models')))


def tokenizer(capability='embedding'):
    profile = EmbeddingProfile(name='embed') if capability == 'embedding' else RerankProfile(name='rank')
    return FrozenTokenizer(profile, cache())


def configuration(max_tokens=512, overlap_tokens=64):
    data = runpy.run_path(str(Path(__file__).with_name('test_configuration.py')))['example_config']()
    data['processing']['parser'] = PARSER.model_dump(mode='json')
    data['processing']['chunker'] = chunker_config(max_tokens, overlap_tokens).model_dump(mode='json')
    return KnowledgeConfig.model_validate_json(json.dumps(data))


def snapshot(max_tokens=512, overlap_tokens=64):
    return ProcessingSnapshot.capture(uuid4(), configuration(max_tokens, overlap_tokens))
