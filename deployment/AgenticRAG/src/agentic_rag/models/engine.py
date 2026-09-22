"""Single-thread-owned CUDA slot. Imported only inside the installed worker."""

import gc
import hashlib
from importlib import resources
import json
from pathlib import Path
import time
from uuid import uuid4
import weakref

from ..capabilities import (EmbeddingResponse, EmbeddingResult, ModelTimings,
                            RerankResponse, RerankScore, validate_input_batch, validate_response)
from ..domain import ErrorCode, RagError
from .identity import describe
from .tokenization import FrozenTokenizer

MIB = 1024 * 1024


class UnsafeCudaCompletion(BaseException):
    """The process must exit; no execution-finished claim is safe after this."""


def synchronize_or_stop(torch):
    try:
        torch.cuda.synchronize()
    except Exception as exc:
        raise UnsafeCudaCompletion('CUDA completion could not be confirmed') from exc


def stopped(request, cancelled, stage):
    if cancelled.is_set():
        raise RagError(ErrorCode.CANCELLED, 'model request cancelled', stage=stage,
                       request_id=request.context.request_id)
    if time.monotonic_ns() >= request.context.deadline_monotonic_ns:
        raise RagError(ErrorCode.DEADLINE_EXCEEDED, 'model request deadline exceeded', stage=stage,
                       request_id=request.context.request_id)


def checked_assets(profile, cache):
    lock = json.loads(resources.files('agentic_rag.models').joinpath('models.lock.json').read_text(encoding='utf-8'))
    entry = lock['models']['embedding' if profile.capability == 'embedding' else 'reranker']
    if entry['model_id'] != profile.model or entry['revision'] != profile.revision:
        raise RagError(ErrorCode.IDENTITY_MISMATCH, 'model does not match package resource lock', stage='model_assets')
    root = Path(cache) / entry['relative_cache_path']
    for name, expected in entry['files'].items():
        path = root / name
        try:
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(4 * MIB), b''):
                    digest.update(block)
            if path.stat().st_size != expected['size'] or digest.hexdigest() != expected['sha256']:
                raise RagError(ErrorCode.IDENTITY_MISMATCH, f'locked model asset differs: {name}', stage='model_assets')
        except OSError as exc:
            raise RagError(ErrorCode.DEPENDENCY_UNAVAILABLE, f'locked model asset unavailable: {name}', stage='model_assets') from exc
    return root


class CudaEngine:
    def __init__(self, cache):
        describe()  # target environment dependency checks before GPU import
        import torch
        from transformers import AutoModel, AutoModelForCausalLM
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RagError(ErrorCode.DEVICE_UNAVAILABLE, 'requires one visible NVIDIA CUDA device', stage='device')
        self.torch, self.factories = torch, {'embedding': AutoModel, 'rerank': AutoModelForCausalLM}
        self.cache, self.model, self.profile, self.tokenizer = cache, None, None, None
        self.load_count = 0
        self.device_identity = {'name': torch.cuda.get_device_name(0), 'index': 0,
                               'uuid': str(torch.cuda.get_device_properties(0).uuid),
                               'total_memory_bytes': torch.cuda.get_device_properties(0).total_memory}
        self.snapshot = {'loaded': False, 'load_count': 0, 'device': self.device_identity,
                         'total_memory_mib': torch.cuda.get_device_properties(0).total_memory // MIB}

    def unload(self):
        refs = [] if self.model is None else [weakref.ref(self.model)] + [
            weakref.ref(value) for value in (*self.model.parameters(), *self.model.buffers())]
        self.model, self.profile, self.tokenizer = None, None, None
        try:
            gc.collect()
            self.torch.cuda.empty_cache()
        finally:
            synchronize_or_stop(self.torch)
        self.snapshot = {**self.snapshot, 'loaded': False, 'unloaded_refs': len(refs),
                         'live_unloaded_refs': sum(value() is not None for value in refs),
                         'allocated_mib': self.torch.cuda.memory_allocated() / MIB,
                         'reserved_mib': self.torch.cuda.memory_reserved() / MIB}

    def load(self, profile, root, tokenizer):
        torch = self.torch
        self.unload()
        torch.cuda.set_per_process_memory_fraction(profile.runtime.allocator_cap_mib * MIB /
                                                   torch.cuda.get_device_properties(0).total_memory, 0)
        try:
            self.model = self.factories[profile.capability].from_pretrained(
                root, dtype=torch.bfloat16, attn_implementation='sdpa',
                local_files_only=True, trust_remote_code=False).to('cuda:0').eval()
            torch.cuda.synchronize()
        except torch.cuda.OutOfMemoryError as exc:
            self.unload()
            raise RagError(ErrorCode.CUDA_OUT_OF_MEMORY, 'model load exceeded CUDA allocator budget', stage='load') from exc
        finally:
            synchronize_or_stop(torch)
        if ({str(value.device) for value in self.model.parameters()} != {'cuda:0'} or
                {str(value.dtype) for value in self.model.parameters()} != {'torch.bfloat16'} or
                self.model.config._attn_implementation != 'sdpa'):
            self.unload()
            raise RagError(ErrorCode.IDENTITY_MISMATCH, 'actual model placement/attention differs', stage='load')
        self.model.config.use_cache = False
        self.profile, self.tokenizer = profile, tokenizer
        self.load_count += 1
        self.snapshot = {**self.snapshot, 'loaded': True, 'profile_fingerprint': profile.identity,
                         'model_instance_id': str(uuid4()), 'load_count': self.load_count,
                         'dtype': 'bfloat16', 'attention': 'sdpa'}

    def execute(self, request, cancelled, observer, admitted_ns):
        torch = self.torch
        stopped(request, cancelled, 'validation')
        observer('validation')
        profile = request.profile
        tokenizer = (self.tokenizer if self.profile and self.profile.identity == profile.identity
                     else FrozenTokenizer(profile, self.cache))
        if request.operation == 'documents':
            sequences = [tokenizer.document(item.text, item.title or '') for item in request.items]
        elif request.operation == 'query':
            sequences = [tokenizer.query(request.items[0].text)]
        else:
            sequences = [tokenizer.rerank(request.query, item.text, item.title or '') for item in request.items]
        counts = tuple(seq.token_count for seq in sequences)
        validate_input_batch(request.items, counts, profile.limits)
        stopped(request, cancelled, 'validation')
        queue_ms = (time.monotonic_ns() - admitted_ns) // 1000000
        start = time.monotonic_ns()
        if self.profile is None or self.profile.identity != profile.identity:
            observer('loading')
            root = checked_assets(profile, self.cache)
            stopped(request, cancelled, 'model_assets')
            self.load(profile, root, tokenizer)
        load_ms = (time.monotonic_ns() - start) // 1000000
        stopped(request, cancelled, 'load')
        width = max(counts)
        # The locked tokenizers use EOS as padding; attention_mask excludes padding.
        pad = tokenizer.encode('<|endoftext|>')[0]
        ids = [[pad] * (width - count) + list(seq.ids) for seq, count in zip(sequences, counts)]
        mask = [[0] * (width - count) + [1] * count for count in counts]
        hook = None
        try:
            encoded = {'input_ids': torch.tensor(ids, device='cuda:0'),
                       'attention_mask': torch.tensor(mask, device='cuda:0')}
            torch.cuda.synchronize()
            stopped(request, cancelled, 'inference')
            torch.cuda.reset_peak_memory_stats()
            start = time.monotonic_ns()
            # Observation is emitted AFTER the first real decoder layer executes,
            # so tests do not guess that a sleeping client implies active inference.
            layers = self.model.layers if profile.capability == 'embedding' else self.model.model.layers
            hook = layers[0].register_forward_hook(lambda *_: observer('inference_started'))
            with torch.inference_mode():
                if profile.capability == 'embedding':
                    hidden = self.model(**encoded, use_cache=False).last_hidden_state
                    result = torch.nn.functional.normalize(hidden[:, -1].float(), p=2, dim=1)
                else:
                    logits = self.model(**encoded, use_cache=False, logits_to_keep=1).logits[:, -1, :]
                    no, yes = tokenizer.encode('no'), tokenizer.encode('yes')
                    if len(no) != 1 or len(yes) != 1 or no == yes:
                        raise RagError(ErrorCode.IDENTITY_MISMATCH, 'locked yes/no labels differ', stage='inference')
                    result = torch.softmax(logits[:, [no[0], yes[0]]].float(), dim=1)[:, 1]
                if not torch.isfinite(result).all():
                    raise RagError(ErrorCode.INVALID_RESPONSE, 'nonfinite model output', stage='inference')
                values = result.cpu().tolist()
            torch.cuda.synchronize()
            inference_ms = (time.monotonic_ns() - start) // 1000000
            self.snapshot = {**self.snapshot, 'last_input_tokens': list(counts), 'last_padded_tokens': width * len(ids),
                             'allocated_mib': torch.cuda.memory_allocated() / MIB,
                             'reserved_mib': torch.cuda.memory_reserved() / MIB,
                             'peak_allocated_mib': torch.cuda.max_memory_allocated() / MIB}
        except torch.cuda.OutOfMemoryError as exc:
            torch.cuda.synchronize()
            raise RagError(ErrorCode.CUDA_OUT_OF_MEMORY, 'inference exceeded CUDA allocator budget', stage='inference') from exc
        finally:
            if hook is not None:
                hook.remove()
            # Also covers non-OOM forward/Python failures after asynchronous
            # kernels were queued. If synchronization fails, terminate instead
            # of publishing an unproven execution_finished notification.
            synchronize_or_stop(torch)
        stopped(request, cancelled, 'completed_batch')
        timings = ModelTimings(queue_ms=queue_ms, load_ms=load_ms, inference_ms=inference_ms)
        if profile.capability == 'embedding':
            response = EmbeddingResponse(request_id=request.context.request_id, profile_fingerprint=profile.identity,
                results=tuple(EmbeddingResult(item_id=item.item_id, vector=tuple(value), input_tokens=count)
                              for item, value, count in zip(request.items, values, counts)), timings=timings)
        else:
            rows = [RerankScore(item_id=item.item_id, score=value, input_tokens=count)
                    for item, value, count in zip(request.items, values, counts)]
            response = RerankResponse(request_id=request.context.request_id, profile_fingerprint=profile.identity,
                results=tuple(sorted(rows, key=lambda row: (-row.score, str(row.item_id)))), timings=timings)
        validate_response(response, request.items, profile, request.context)
        return response
