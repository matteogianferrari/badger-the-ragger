"""Lazy Sentence Transformers adapter for Qwen or another configured model."""

from __future__ import annotations

from typing import Protocol

import numpy as np

from .config import Config


class Embedder(Protocol):
    """Small interface used by indexing and search, allowing fake test models."""

    def documents(self, texts: list[str]) -> np.ndarray:
        """Return a matrix of normalized paragraph embeddings."""

    def query(self, text: str) -> np.ndarray:
        """Return one normalized query embedding."""


class SentenceTransformerEmbedder:
    """Load one model per command or REPL session and encode in batches."""

    def __init__(self, config: Config) -> None:
        from sentence_transformers import SentenceTransformer

        self.config = config
        self.model = SentenceTransformer(
            config.model_name,
            device=None if config.device == "auto" else config.device,
            model_kwargs={"attn_implementation": "sdpa"},
        )

    def documents(self, texts: list[str]) -> np.ndarray:
        """Embed passages with the document route; L2-normalize for cosine search."""
        if not texts:
            raise ValueError("documents requires at least one paragraph")
        return np.asarray(
            self.model.encode_document(
                texts,
                batch_size=self.config.batch_size,
                normalize_embeddings=True,
                show_progress_bar=len(texts) >= self.config.batch_size,
            ),
            dtype=np.float32,
        )

    def query(self, text: str) -> np.ndarray:
        """Embed one query with its retrieval instruction."""
        return np.asarray(
            self.model.encode_query(
                [text],
                prompt=self.config.query_prompt,
                normalize_embeddings=True,
                show_progress_bar=False,
            )[0],
            dtype=np.float32,
        )

