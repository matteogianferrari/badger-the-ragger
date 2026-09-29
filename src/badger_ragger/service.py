"""Reusable search service shared by the CLI and editor integrations."""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from threading import RLock
from typing import Callable

from .config import Config
from .embeddings import Embedder, SentenceTransformerEmbedder
from .index import (
    IndexStatus, IndexUnavailable, connect_index, inspect_index, load_paragraphs,
    paragraph_count, update_index,
)
from .notes import NoteSnapshot, scan_notes
from .search import rank_notes


class StaleIndexError(ValueError):
    """Raised when a request would otherwise use outdated paragraph vectors."""

    def __init__(self, status: IndexStatus) -> None:
        super().__init__("Notes have changed; run the index action before searching")
        self.status = status


def status_data(status: IndexStatus, *, indexed: bool) -> dict:
    """Turn an index diff into a JSON-compatible status for either editor."""
    return {**asdict(status), "indexed": indexed, "needs_update": status.needs_update}


class BadgerService:
    """Serialize operations and keep one embedding model warm between requests."""

    def __init__(
        self,
        config: Config,
        model_factory: Callable[[Config], Embedder] = SentenceTransformerEmbedder,
    ) -> None:
        self.config = config
        self._model_factory = model_factory
        self._model: Embedder | None = None
        self._lock = RLock()

    def _embedder(self) -> Embedder:
        """Load the model on first use and reuse it for later requests."""
        if self._model is None:
            self._model = self._model_factory(self.config)
        return self._model

    def _diff(self, snapshots: dict[str, NoteSnapshot]) -> IndexStatus:
        """Compare current note bytes to stored SHA-256s, including missing DBs."""
        if not self.config.index_path.is_file():
            return IndexStatus(tuple(sorted(snapshots)), (), (), 0, rebuild=True)
        with closing(connect_index(self.config.index_path)) as conn:
            return inspect_index(conn, self.config, snapshots)

    def status(self) -> dict:
        """Report all new, changed, and removed notes without loading the model."""
        with self._lock:
            snapshots = scan_notes(self.config.notes_root, self.config.exclude_globs)
            return status_data(self._diff(snapshots), indexed=self.config.index_path.is_file())

    def index(self, *, rebuild: bool = False) -> dict:
        """Apply one incremental index update and report the affected notes."""
        with self._lock:
            snapshots = scan_notes(self.config.notes_root, self.config.exclude_globs)
            existed = self.config.index_path.is_file()
            with closing(connect_index(self.config.index_path, write=True)) as conn:
                before = inspect_index(conn, self.config, snapshots)
                if rebuild or before.needs_update:
                    names = sorted(snapshots) if (rebuild or before.rebuild) else sorted((*before.new, *before.changed))
                    has_paragraphs = any(
                        snapshots[name].paragraphs(self.config.min_paragraph_chars) for name in names
                    )
                    count = update_index(
                        conn, self.config, snapshots, before,
                        embedder=self._embedder() if has_paragraphs else None,
                        force=rebuild,
                    )
                else:
                    count = 0
                total = paragraph_count(conn)
            return {
                "before": status_data(before, indexed=existed),
                "embedded_paragraphs": count,
                "indexed_paragraphs": total,
                "indexed_notes": len(snapshots),
            }

    def search(self, query: str, top_k: int | None = None) -> dict:
        """Return the best matching paragraph per note without printing text."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a nonempty string")
        limit = self.config.top_k if top_k is None else top_k
        if type(limit) is not int or limit < 1:
            raise ValueError("top_k must be a positive integer")
        with self._lock:
            snapshots = scan_notes(self.config.notes_root, self.config.exclude_globs)
            if not self.config.index_path.is_file():
                raise StaleIndexError(self._diff(snapshots))
            with closing(connect_index(self.config.index_path)) as conn:
                status = inspect_index(conn, self.config, snapshots)
                if status.needs_update:
                    raise StaleIndexError(status)
                if not paragraph_count(conn):
                    return {"query": query, "results": []}
                paragraphs = load_paragraphs(conn)
            results = rank_notes(self._embedder().query(query), paragraphs, limit)
            return {"query": query, "results": [asdict(result) for result in results]}
