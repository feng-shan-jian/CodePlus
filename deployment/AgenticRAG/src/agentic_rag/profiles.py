"""R03 model identities. These describe supported contracts, not live adapters."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ._schema import PositiveInt, Record, Sha256, Text, fingerprint

EMBEDDING_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
RERANK_REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"
INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
RERANK_PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on '
                 'the Query and the Instruct provided. Note that the answer can only be "yes" or "no".'
                 '<|im_end|>\n<|im_start|>user\n')
RERANK_SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'


class TokenizerIdentity(Record):
    model: Text
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    tokenizer_sha256: Sha256
    config_sha256: Sha256 = "253153d0738ceb4c668d2eff957714dd2bea0b56de772a9fdccd96cbf517e6a0"
    vocab_sha256: Sha256 = "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"
    merges_sha256: Sha256 = "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"
    padding_side: Literal["left"] = "left"
    truncation: Literal[False] = False


class InputLimits(Record):
    """Conservative measured R03 envelope, not a platform maximum or SLA."""

    max_input_tokens: Annotated[int, Field(gt=0, le=2048)] = 2048
    max_batch_size: Annotated[int, Field(gt=0, le=4)] = 4
    max_padded_tokens: Annotated[int, Field(gt=0, le=4096)] = 4096

    @model_validator(mode="after")
    def fits_one_input(self):
        if self.max_padded_tokens < self.max_input_tokens:
            raise ValueError("padded token limit must admit one maximum-length input")
        return self


class LocalRuntime(Record):
    dtype: Literal["bfloat16"] = "bfloat16"
    attention: Literal["sdpa"] = "sdpa"
    device: Literal["cuda:0"] = "cuda:0"
    implementation: Literal["transformers"] = "transformers"
    torch_version: Literal["2.14.0+cu130"] = "2.14.0+cu130"
    transformers_version: Literal["5.17.0"] = "5.17.0"
    tokenizers_version: Literal["0.23.2"] = "0.23.2"
    use_cache: Literal[False] = False
    trust_remote_code: Literal[False] = False
    local_files_only: Literal[True] = True
    allocator_cap_mib: Annotated[int, Field(gt=0, le=2048)] = 2048


def _tokenizer(capability: str) -> TokenizerIdentity:
    embedding = capability == "embedding"
    return TokenizerIdentity(
        model="Qwen/Qwen3-Embedding-0.6B" if embedding else "Qwen/Qwen3-Reranker-0.6B",
        revision=EMBEDDING_REVISION if embedding else RERANK_REVISION,
        tokenizer_sha256=("def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a"
                          if embedding else "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"),
    )


class _LocalProfile(Record):
    name: Text
    backend: Literal["qwen3_local"] = "qwen3_local"
    runtime: LocalRuntime = Field(default_factory=LocalRuntime)
    limits: InputLimits = Field(default_factory=InputLimits)
    document_template: Literal["title_text_v1"] = "title_text_v1"
    document_with_title: Literal["Title: {title}\n{text}"] = "Title: {title}\n{text}"
    document_without_title: Literal["{text}"] = "{text}"
    instruction: Text = INSTRUCTION

    @property
    def identity(self) -> str:
        # Display/profile selection names are not model identity.
        return fingerprint("model-profile", self.model_dump(mode="json", exclude={"name"}))


class EmbeddingProfile(_LocalProfile):
    capability: Literal["embedding"] = "embedding"
    model: Literal["Qwen/Qwen3-Embedding-0.6B"] = "Qwen/Qwen3-Embedding-0.6B"
    revision: Literal[EMBEDDING_REVISION] = EMBEDDING_REVISION
    tokenizer: TokenizerIdentity = Field(default_factory=lambda: _tokenizer("embedding"))
    query_template: Literal["Instruct: {instruction}\nQuery:{query}"] = "Instruct: {instruction}\nQuery:{query}"
    add_special_tokens: Literal[True] = True
    dimension: Literal[1024] = 1024
    pooling: Literal["last_nonpadding_token"] = "last_nonpadding_token"
    normalization: Literal["float32_L2"] = "float32_L2"

    @model_validator(mode="after")
    def verified_tokenizer(self):
        if self.tokenizer != _tokenizer("embedding"):
            raise ValueError("tokenizer must match the locked R03 embedding assets")
        return self


class RerankProfile(_LocalProfile):
    capability: Literal["rerank"] = "rerank"
    model: Literal["Qwen/Qwen3-Reranker-0.6B"] = "Qwen/Qwen3-Reranker-0.6B"
    revision: Literal[RERANK_REVISION] = RERANK_REVISION
    tokenizer: TokenizerIdentity = Field(default_factory=lambda: _tokenizer("rerank"))
    add_special_tokens: Literal[False] = False
    prefix: Literal[RERANK_PREFIX] = RERANK_PREFIX
    body: Literal["<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"] = (
        "<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"
    )
    suffix: Literal[RERANK_SUFFIX] = RERANK_SUFFIX
    score_type: Literal["softmax_float32([no,yes])[yes]"] = "softmax_float32([no,yes])[yes]"
    logits_to_keep: Literal[1] = 1

    @model_validator(mode="after")
    def verified_tokenizer(self):
        if self.tokenizer != _tokenizer("rerank"):
            raise ValueError("tokenizer must match the locked R03 rerank assets")
        return self


ModelProfile = Annotated[EmbeddingProfile | RerankProfile, Field(discriminator="capability")]
