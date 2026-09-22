"""R03's exact local tokenization/templates without transformers, Torch or probes.

Offsets describe tokenizer alignment, not evidence/source coverage. In particular
the locked NFC normalizer can omit combining-codepoint positions and ByteLevel
can emit several tokens with overlapping offsets for one Unicode character.
"""

from dataclasses import dataclass
import hashlib
from importlib.metadata import version
from pathlib import Path

from ..domain import ErrorCode, RagError
from ..profiles import EmbeddingProfile, RerankProfile


def document_input(profile, text: str, title: str = '') -> str:
    return (profile.document_with_title.format(title=title, text=text) if title
            else profile.document_without_title.format(text=text))


@dataclass(frozen=True)
class ModelSequence:
    ids: tuple[int, ...]
    # Pieces are separate encoding units; Rerank must not encode their join.
    pieces: tuple[str, ...]

    @property
    def token_count(self) -> int:
        return len(self.ids)


class FrozenTokenizer:
    """Explicit cache root: <cache>/<model basename>/<locked revision>/files.

    Hashes authenticate the files actually read into memory. No downloads,
    current default config, model weights, or repository resource dependencies.
    """

    def __init__(self, profile: EmbeddingProfile | RerankProfile, cache: str | Path):
        from tokenizers import Tokenizer
        if version('tokenizers') != profile.runtime.tokenizers_version:
            raise RagError(ErrorCode.IDENTITY_MISMATCH, 'requires frozen tokenizers 0.23.2', stage='tokenizer')
        self.profile = profile
        self.root = Path(cache) / profile.model.split('/')[-1] / profile.revision
        identity = profile.tokenizer
        assets = {'tokenizer.json': identity.tokenizer_sha256,
                  'tokenizer_config.json': identity.config_sha256,
                  'vocab.json': identity.vocab_sha256, 'merges.txt': identity.merges_sha256}
        tokenizer_bytes = None
        for name, expected in assets.items():
            try:
                data = (self.root / name).read_bytes()
            except OSError as exc:
                raise RagError(ErrorCode.DEPENDENCY_UNAVAILABLE, f'missing frozen tokenizer asset: {name}', stage='tokenizer') from exc
            if hashlib.sha256(data).hexdigest() != expected:
                raise RagError(ErrorCode.IDENTITY_MISMATCH, f'frozen tokenizer asset differs: {name}', stage='tokenizer')
            if name == 'tokenizer.json':
                tokenizer_bytes = data
        self._backend = Tokenizer.from_str(tokenizer_bytes.decode('utf-8'))
        self._backend.no_truncation()
        self._backend.no_padding()

    def encode(self, text: str, *, add_special_tokens: bool = False) -> tuple[int, ...]:
        return tuple(self._backend.encode(text, add_special_tokens=add_special_tokens).ids)

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple(self._backend.encode(text, add_special_tokens=False).offsets)

    def document(self, text: str, title: str = '', *, check: bool = True) -> ModelSequence:
        if not isinstance(self.profile, EmbeddingProfile):
            raise RagError(ErrorCode.INVALID_INPUT, 'embedding profile required', stage='tokenizer')
        complete = document_input(self.profile, text, title)
        return self._sequence((complete,), add_special_tokens=True, check=check)

    def query(self, query: str) -> ModelSequence:
        if not isinstance(self.profile, EmbeddingProfile) or not query.strip():
            raise RagError(ErrorCode.INVALID_INPUT, 'nonempty embedding query required', stage='tokenizer')
        return self._sequence((self.profile.query_template.format(instruction=self.profile.instruction, query=query),),
                              add_special_tokens=True, check=True)

    def rerank(self, query: str, text: str, title: str = '') -> ModelSequence:
        if not isinstance(self.profile, RerankProfile) or not query.strip() or not text.strip():
            raise RagError(ErrorCode.INVALID_INPUT, 'rerank profile and nonempty query/document required', stage='tokenizer')
        body = self.profile.body.format(instruction=self.profile.instruction, query=query,
                                        document=document_input(self.profile, text, title))
        return self._sequence((self.profile.prefix, body, self.profile.suffix), add_special_tokens=False, check=True)

    def _sequence(self, pieces, *, add_special_tokens, check):
        result = ModelSequence(tuple(token for piece in pieces for token in self.encode(
            piece, add_special_tokens=add_special_tokens)), pieces)
        if check and result.token_count > self.profile.limits.max_input_tokens:
            raise RagError(ErrorCode.INPUT_TOO_LONG,
                f'{self.profile.capability} complete input {result.token_count} exceeds {self.profile.limits.max_input_tokens}',
                stage='model_input')
        return result
