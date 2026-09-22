"""Small synchronous model protocols shared by the local worker adapters."""

from datetime import datetime
import importlib.util
import math
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ._schema import NonNegativeInt, PositiveInt, Record, Sha256, Text
from .domain import ErrorCode, RagError
from .profiles import EmbeddingProfile, InputLimits, RerankProfile


class RequestContext(Record):
    request_id: UUID
    owner_id: UUID
    purpose: Literal["qa", "report", "import", "rebuild"]
    # Absolute time.monotonic_ns() value within this machine/boot. Never persist
    # for restart recovery; R09 must validate its clock domain during handshake.
    deadline_monotonic_ns: PositiveInt
    deadline_at: datetime | None = None  # optional wall-clock audit, never enforcement

    @field_validator("deadline_at")
    @classmethod
    def aware_deadline(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("audit deadline requires an explicit timezone")
        return value


class ModelInput(Record):
    item_id: UUID
    text: Text
    title: str | None = None


class ModelTimings(Record):
    queue_ms: NonNegativeInt
    load_ms: NonNegativeInt
    inference_ms: NonNegativeInt


class EmbeddingResult(Record):
    item_id: UUID
    vector: Annotated[tuple[float, ...], Field(min_length=1024, max_length=1024)]
    input_tokens: PositiveInt

    @model_validator(mode="after")
    def normalized_vector(self):
        if not math.isclose(math.sqrt(sum(value * value for value in self.vector)), 1.0, abs_tol=1e-4):
            raise ValueError("embedding vector must be finite and L2-normalized")
        return self


class EmbeddingResponse(Record):
    request_id: UUID
    profile_fingerprint: Sha256
    results: Annotated[tuple[EmbeddingResult, ...], Field(min_length=1)]
    timings: ModelTimings


class RerankScore(Record):
    item_id: UUID
    score: Annotated[float, Field(ge=0, le=1)]
    input_tokens: PositiveInt


class RerankResponse(Record):
    request_id: UUID
    profile_fingerprint: Sha256
    results: Annotated[tuple[RerankScore, ...], Field(min_length=1)]
    timings: ModelTimings


class EmbeddingProvider(Protocol):
    def embed_documents(self, items: tuple[ModelInput, ...], profile: EmbeddingProfile,
                        context: RequestContext) -> EmbeddingResponse: ...

    def embed_query(self, query: ModelInput, profile: EmbeddingProfile,
                    context: RequestContext) -> EmbeddingResponse: ...


class RerankProvider(Protocol):
    def rerank(self, query: str, candidates: tuple[ModelInput, ...], profile: RerankProfile,
               context: RequestContext) -> RerankResponse: ...


def validate_input_batch(items: tuple[ModelInput, ...], complete_token_counts: tuple[int, ...],
                         limits: InputLimits) -> None:
    """Adapter supplies actual tokenizer counts *including* the complete template.

    This validation never tokenizes or truncates. It does not prove that a
    caller-provided count corresponds to real text; R09 must measure first.
    """
    if (not items or len(items) != len(complete_token_counts)
            or len({item.item_id for item in items}) != len(items)
            or any(type(count) is not int or count <= 0 for count in complete_token_counts)):
        raise RagError(ErrorCode.INVALID_INPUT, "items and positive token counts must align with unique IDs", stage="model_input")
    if any(count > limits.max_input_tokens for count in complete_token_counts):
        raise RagError(ErrorCode.INPUT_TOO_LONG, "complete model input exceeds the profile token limit", stage="model_input")
    if len(items) > limits.max_batch_size or len(items) * max(complete_token_counts) > limits.max_padded_tokens:
        raise RagError(ErrorCode.BATCH_TOO_LARGE, "batch exceeds item or padded-token limit", stage="model_input")


def validate_response(response: EmbeddingResponse | RerankResponse, items: tuple[ModelInput, ...],
                      profile: EmbeddingProfile | RerankProfile, context: RequestContext) -> None:
    if response.request_id != context.request_id or response.profile_fingerprint != profile.identity:
        raise RagError(ErrorCode.IDENTITY_MISMATCH, "response request/profile identity does not match", stage="model_response", request_id=context.request_id)
    embedding = isinstance(profile, EmbeddingProfile)
    if embedding != isinstance(response, EmbeddingResponse):
        raise RagError(ErrorCode.INVALID_RESPONSE, "response capability does not match profile", stage="model_response", request_id=context.request_id)
    expected, actual = [item.item_id for item in items], [item.item_id for item in response.results]
    if (len(set(expected)) != len(expected) or len(set(actual)) != len(actual)
            or set(actual) != set(expected) or (embedding and actual != expected)):
        raise RagError(ErrorCode.INVALID_RESPONSE, "response IDs are missing, duplicated, foreign or reordered", stage="model_response", request_id=context.request_id)
    if not embedding:
        ordered = sorted(response.results, key=lambda row: (-row.score, str(row.item_id)))
        if tuple(ordered) != response.results:
            raise RagError(ErrorCode.INVALID_RESPONSE, "rerank order must be score descending and ID ascending", stage="model_response", request_id=context.request_id)
    validate_input_batch(items, tuple(row.input_tokens for row in response.results), profile.limits)


def require_optional_dependencies(capability: Literal["embedding", "rerank"]) -> None:
    """Read-only preflight. Success does not imply GPU or model availability."""
    if capability not in ("embedding", "rerank"):
        raise RagError(ErrorCode.CAPABILITY_UNAVAILABLE, f"unknown model capability: {capability}", stage="dependencies")
    missing = [name for name in ("torch", "transformers", "tokenizers") if importlib.util.find_spec(name) is None]
    if missing:
        raise RagError(ErrorCode.DEPENDENCY_UNAVAILABLE, "local model dependencies are missing: " + ", ".join(missing), stage="dependencies")


def require_provider(provider: EmbeddingProvider | RerankProvider | None, capability: Literal["embedding", "rerank"]):
    """Check the explicitly assembled provider; never create a hidden fallback."""
    required = {"embedding": ("embed_documents", "embed_query"), "rerank": ("rerank",)}
    if capability not in required or provider is None or any(not callable(getattr(provider, method, None)) for method in required[capability]):
        raise RagError(ErrorCode.CAPABILITY_UNAVAILABLE, f"{capability} adapter is unavailable; explicitly assemble a supported provider", stage="provider_selection")
    return provider
