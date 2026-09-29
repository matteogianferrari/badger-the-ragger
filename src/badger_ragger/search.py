"""Cosine similarity ranking, with the best matching paragraph per note."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .index import IndexedParagraph


@dataclass(frozen=True)
class SearchResult:
    """A note score and the exact paragraph that produced it."""

    score: float
    note_name: str
    paragraph: str


def rank_notes(
    query_vector: np.ndarray, paragraphs: list[IndexedParagraph], top_k: int
) -> list[SearchResult]:
    """Compute all paragraph scores and return the top K distinct notes."""
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if not paragraphs:
        return []
    vector = np.asarray(query_vector, dtype=np.float32).reshape(-1)
    matrix = np.stack([paragraph.vector for paragraph in paragraphs])
    if matrix.shape[1] != len(vector) or not np.isfinite(vector).all():
        raise ValueError("Query embedding does not match the index; run 'badger index --rebuild'")
    norm = np.linalg.norm(vector)
    if norm == 0 or not np.isfinite(norm):
        raise ValueError("The embedding model returned a zero or invalid query vector")
    vector = vector / norm
    scores = matrix @ vector
    best: dict[str, SearchResult] = {}
    for paragraph, score in zip(paragraphs, scores):
        candidate = SearchResult(float(score), paragraph.note_name, paragraph.text)
        if paragraph.note_name not in best or candidate.score > best[paragraph.note_name].score:
            best[paragraph.note_name] = candidate
    return sorted(best.values(), key=lambda result: (-result.score, result.note_name))[:top_k]
