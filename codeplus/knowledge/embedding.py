"""The S1 Qwen profile: CPU float32, left padding, last-token pooling, L2 norm."""

from __future__ import annotations

from dataclasses import replace

from codeplus.config import KnowledgeConfig
from . import QUERY_INSTRUCTION


class LocalEmbedding:
    def __init__(self, config: KnowledgeConfig):
        if not config.enabled:
            raise ValueError("Knowledge is disabled")
        for key in ("embedding_dimension", "max_input_tokens", "batch_size"):
            if type(getattr(config, key)) is not int or getattr(config, key) <= 0:
                raise ValueError(f"knowledge.{key} must be a positive integer")
        self.config = replace(config)
        self._model = None
        self._tokenizer = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModel, AutoTokenizer

        # Both artifacts use the same immutable revision and the normal HF cache.
        tokenizer = AutoTokenizer.from_pretrained(
            self.config.embedding_model, revision=self.config.embedding_revision,
            padding_side="left", trust_remote_code=False,
        )
        model = AutoModel.from_pretrained(
            self.config.embedding_model, revision=self.config.embedding_revision,
            dtype=torch.float32, trust_remote_code=False,
        ).to("cpu").eval()
        if model.config.hidden_size != self.config.embedding_dimension:
            raise ValueError("knowledge.embedding_dimension does not match the loaded model")
        if self.config.max_input_tokens > model.config.max_position_embeddings:
            raise ValueError("knowledge.max_input_tokens exceeds the loaded model context")
        self._tokenizer, self._model = tokenizer, model

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        """Encode raw passages without instructions; reject truncation."""
        if not texts:
            return []
        if any(not text.strip() for text in texts):
            raise ValueError("Embedding input must not be empty")
        self._load()
        import torch

        vectors = []
        for start in range(0, len(texts), self.config.batch_size):
            batch = self._tokenizer(
                texts[start:start + self.config.batch_size],
                padding=True, truncation=False, return_tensors="pt",
            )
            if batch["input_ids"].shape[1] > self.config.max_input_tokens:
                raise ValueError("Embedding input exceeds max_input_tokens; split it before encoding")
            with torch.inference_mode():
                output = self._model(**batch, use_cache=False).last_hidden_state[:, -1]
                output = torch.nn.functional.normalize(output, p=2, dim=1)
            if not torch.isfinite(output).all():
                raise ValueError("Embedding output contains non-finite values")
            vectors.extend(output.tolist())
        return vectors

    def encode_query(self, query: str) -> list[float]:
        """Use the pinned retrieval instruction only for the query."""
        if not query.strip():
            raise ValueError("Embedding query must not be empty")
        return self.encode_documents([f"Instruct: {QUERY_INSTRUCTION}\nQuery:{query}"])[0]
