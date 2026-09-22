"""Explicit knowledge configuration assembly, frozen run selection and snapshots."""

import json
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ._schema import NonNegativeInt, PositiveInt, Record, Sha256, Text, fingerprint
from .profiles import EmbeddingProfile, ModelProfile, RerankProfile

RetrievalMode = Literal["fixed", "auto"]
TaskKind = Literal["qa", "report"]
Route = Literal["dense", "bm25", "hybrid"]


class StorageConfig(Record):
    data_dir: Text
    milvus_uri: Text
    namespace: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
    credential_ref: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")] | None = None

    @field_validator("data_dir")
    @classmethod
    def absolute_local_path(cls, value: str) -> str:
        windows, posix = PureWindowsPath(value), PurePosixPath(value)
        if not (windows.is_absolute() or posix.is_absolute()):
            raise ValueError("adapter must supply an absolute data_dir")
        if value.startswith(("\\\\", "//")) or ".." in windows.parts or ".." in posix.parts:
            raise ValueError("data_dir must be a normalized local path, not a shared directory")
        return value

    @field_validator("milvus_uri")
    @classmethod
    def endpoint_without_secrets(cls, value: str) -> str:
        uri = urlsplit(value)
        if (uri.scheme not in ("http", "https") or not uri.hostname or uri.username
                or uri.password or uri.query or uri.fragment or uri.path not in ("", "/")):
            raise ValueError("milvus_uri must be an HTTP(S) endpoint; credentials use credential_ref")
        _ = uri.port  # validates port syntax/range
        return value


class ParserConfig(Record):
    implementation: Text
    version: Text
    normalization_version: Text
    encoding: Literal["utf-8"] = "utf-8"


class ChunkerConfig(Record):
    implementation: Text
    version: Text
    max_tokens: PositiveInt
    overlap_tokens: NonNegativeInt

    @model_validator(mode="after")
    def bounded_overlap(self):
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")
        return self


class IndexConfig(Record):
    """Explicit experiment settings; query-time search knobs belong to retrieval."""

    schema_version_name: Text
    dense_index: Literal["IVF_FLAT"] = "IVF_FLAT"
    dense_metric: Literal["COSINE"] = "COSINE"
    nlist: PositiveInt
    sparse_index: Literal["SPARSE_INVERTED_INDEX"] = "SPARSE_INVERTED_INDEX"
    sparse_algorithm: Literal["DAAT_MAXSCORE"] = "DAAT_MAXSCORE"
    tokenizer: Literal["standard"] = "standard"
    filters: tuple[Literal["lowercase"], ...] = ("lowercase",)
    bm25_k1: Annotated[float, Field(gt=0)]
    bm25_b: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def no_duplicate_filters(self):
        if len(set(self.filters)) != len(self.filters):
            raise ValueError("duplicate analyzer filters")
        return self


class ProcessingConfig(Record):
    parser: ParserConfig
    chunker: ChunkerConfig
    index: IndexConfig


class ModelSelection(Record):
    embedding: Text
    reranker: Text | None = None


class RetrievalConfig(Record):
    mode: RetrievalMode = "auto"
    route: Route
    rerank: bool
    dense_candidates: PositiveInt
    bm25_candidates: PositiveInt
    rerank_candidates: PositiveInt
    rrf_k: PositiveInt
    nprobe: PositiveInt
    context_chunks: PositiveInt
    context_tokens: PositiveInt

    @model_validator(mode="after")
    def coherent_candidates(self):
        available = {"dense": self.dense_candidates, "bm25": self.bm25_candidates,
                     "hybrid": self.dense_candidates + self.bm25_candidates}[self.route]
        if self.rerank and self.rerank_candidates > available:
            raise ValueError("rerank_candidates exceeds the selected route candidate bound")
        bound = self.rerank_candidates if self.rerank else available
        if self.context_chunks > bound:
            raise ValueError("context_chunks exceeds the selected candidate bound")
        return self


class RunBudget(Record):
    searches: PositiveInt
    opens: PositiveInt
    total_tokens: PositiveInt
    duration_ms: PositiveInt
    finish_reserve_tokens: PositiveInt
    finish_reserve_ms: PositiveInt

    @model_validator(mode="after")
    def reserve_inside_total(self):
        if self.finish_reserve_tokens >= self.total_tokens or self.finish_reserve_ms >= self.duration_ms:
            raise ValueError("finish reserves must be smaller than the corresponding hard totals")
        return self


class Budgets(Record):
    qa: RunBudget
    report: RunBudget


class KnowledgeConfig(Record):
    """Only the knowledge feature. No permission mode or implicit environment IO."""

    parameter_status: Literal["experiment"] = "experiment"
    storage: StorageConfig
    processing: ProcessingConfig
    models: ModelSelection
    model_profiles: tuple[ModelProfile, ...]
    retrieval: RetrievalConfig
    budgets: Budgets

    def profile(self, name: str) -> ModelProfile:
        for profile in self.model_profiles:
            if profile.name == name:
                return profile
        raise ValueError(f"unknown model profile: {name}")

    @property
    def embedding(self) -> EmbeddingProfile:
        profile = self.profile(self.models.embedding)
        if not isinstance(profile, EmbeddingProfile):
            raise ValueError("models.embedding must select an embedding capability")
        return profile

    @property
    def reranker(self) -> RerankProfile | None:
        if self.models.reranker is None:
            return None
        profile = self.profile(self.models.reranker)
        if not isinstance(profile, RerankProfile):
            raise ValueError("models.reranker must select a rerank capability")
        return profile

    @model_validator(mode="after")
    def valid_selections(self):
        names = [profile.name for profile in self.model_profiles]
        if len(names) != len(set(names)):
            raise ValueError("model profile names must be unique")
        if self.processing.chunker.max_tokens > self.embedding.limits.max_input_tokens:
            raise ValueError("chunk token budget exceeds embedding complete-input limit")
        if self.retrieval.rerank and self.reranker is None:
            raise ValueError("rerank enabled without a selected reranker")
        _ = self.reranker  # validates even when disabled
        if self.retrieval.nprobe > self.processing.index.nlist:
            raise ValueError("nprobe exceeds index nlist")
        return self


class ConfigOrigin(Record):
    path: Text
    source: Literal["defaults", "configured", "explicit"]


class WorkerExecutionConfig(Record):
    """Explicit local operations; excluded from frozen knowledge/model identity."""

    executable: Text
    model_cache: Text
    runtime_dir: Text
    startup_timeout_ms: Annotated[int, Field(ge=1000, le=120000)] = 60000
    handshake_timeout_ms: Annotated[int, Field(ge=100, le=10000)] = 3000
    io_timeout_ms: Annotated[int, Field(ge=100, le=30000)] = 5000
    idle_timeout_ms: Annotated[int, Field(ge=100, le=3600000)] = 60000
    max_frame_bytes: Annotated[int, Field(ge=131072, le=1048576)] = 1048576
    max_connections: Annotated[int, Field(ge=1, le=32)] = 16
    max_queue_items: Annotated[int, Field(ge=1, le=64)] = 16
    max_queue_bytes: Annotated[int, Field(ge=131072, le=16777216)] = 4194304
    max_session_requests: Annotated[int, Field(ge=1, le=16384)] = 4096

    @field_validator("executable", "model_cache", "runtime_dir")
    @classmethod
    def explicit_paths(cls, value: str) -> str:
        return StorageConfig.absolute_local_path(value)


class AssembledConfig(Record):
    knowledge: KnowledgeConfig
    origins: tuple[ConfigOrigin, ...]
    worker: WorkerExecutionConfig | None = None


def assemble_configuration(
    *, defaults: Mapping[str, Any], configured: Mapping[str, Any] | None = None,
    explicit: Mapping[str, Any] | None = None,
) -> AssembledConfig:
    """Merge caller-provided knowledge mappings: explicit > configured > defaults.

    Mappings merge recursively, arrays replace atomically, null is explicit.
    Profile collections are arrays and replace as one unit to avoid hybrid models.
    No files, environment variables, credentials or host settings are read.
    """
    data: dict[str, Any] = {}
    origins: dict[str, str] = {}

    def merge(target, incoming, source, prefix=""):
        for key, value in incoming.items():
            if not isinstance(key, str):
                raise ValueError("configuration keys must be strings")
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, Mapping):
                if not isinstance(target.get(key), dict):
                    target[key] = {}
                    for old in tuple(origins):
                        if old == path or old.startswith(path + "."):
                            del origins[old]
                merge(target[key], value, source, path)
            else:
                # JSON round-trip copies mutable containers and rejects objects.
                target[key] = json.loads(json.dumps(value, allow_nan=False))
                for old in tuple(origins):
                    if old == path or old.startswith(path + "."):
                        del origins[old]
                origins[path] = source

    for source, mapping in (("defaults", defaults), ("configured", configured), ("explicit", explicit)):
        if mapping is not None:
            merge(data, mapping, source)
    worker_data = data.pop("worker", None)
    worker = (WorkerExecutionConfig.model_validate_json(json.dumps(worker_data, allow_nan=False))
              if worker_data is not None else None)
    knowledge = KnowledgeConfig.model_validate_json(json.dumps(data, allow_nan=False))
    return AssembledConfig(knowledge=knowledge, worker=worker, origins=tuple(
        ConfigOrigin(path=path, source=source) for path, source in sorted(origins.items())
    ))


class RunOverride(Record):
    """The user may override knowledge mode once, not host permissions or limits."""

    mode: RetrievalMode | None = None


class RunConfiguration(Record):
    feature: Literal["knowledge"] = "knowledge"
    task_kind: TaskKind
    knowledge: KnowledgeConfig
    retrieval: RetrievalConfig
    budget: RunBudget

    @model_validator(mode="after")
    def consistent_resolution(self):
        expected = self.knowledge.retrieval.model_copy(update={"mode": self.retrieval.mode})
        if self.retrieval != expected or self.budget != getattr(self.knowledge.budgets, self.task_kind):
            raise ValueError("run configuration must resolve from its frozen knowledge config")
        return self

    @property
    def identity(self) -> str:
        return fingerprint("knowledge-run-config", self)


def resolve_run(config: KnowledgeConfig, task_kind: TaskKind, override: RunOverride | None = None) -> RunConfiguration:
    if task_kind not in ("qa", "report"):
        raise ValueError("unknown knowledge task_kind")
    mode = override.mode if override and override.mode else config.retrieval.mode
    return RunConfiguration(task_kind=task_kind, knowledge=config,
                            retrieval=config.retrieval.model_copy(update={"mode": mode}),
                            budget=getattr(config.budgets, task_kind))


def document_encoding_identity(config: KnowledgeConfig) -> str:
    """Conservative reuse boundary; excludes rerank, budgets, storage and routes."""
    return fingerprint("document-encoding", {
        "parser": config.processing.parser.model_dump(mode="json"),
        "chunker": config.processing.chunker.model_dump(mode="json"),
        "embedding": config.embedding.model_dump(mode="json", exclude={"name"}),
    })


def index_identity(config: KnowledgeConfig) -> str:
    return fingerprint("index-processing", {
        "document_encoding": document_encoding_identity(config),
        "index": config.processing.index.model_dump(mode="json"),
    })


class ProcessingSnapshot(Record):
    snapshot_id: UUID
    fingerprint_version: Literal[1] = 1
    resolved_config: KnowledgeConfig
    config_fingerprint: Sha256
    document_encoding_fingerprint: Sha256
    index_fingerprint: Sha256

    @model_validator(mode="after")
    def verify_fingerprints(self):
        expected = (fingerprint("knowledge-config", self.resolved_config),
                    document_encoding_identity(self.resolved_config), index_identity(self.resolved_config))
        if expected != (self.config_fingerprint, self.document_encoding_fingerprint, self.index_fingerprint):
            raise ValueError("processing snapshot fingerprint mismatch")
        return self

    @classmethod
    def capture(cls, snapshot_id: UUID, config: KnowledgeConfig) -> "ProcessingSnapshot":
        return cls(snapshot_id=snapshot_id, resolved_config=config,
                   config_fingerprint=fingerprint("knowledge-config", config),
                   document_encoding_fingerprint=document_encoding_identity(config),
                   index_fingerprint=index_identity(config))
