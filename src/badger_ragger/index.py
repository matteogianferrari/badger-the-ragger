"""Incremental SQLite index with transactional updates and source hash checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

import numpy as np

from .config import Config
from .embeddings import Embedder
from .notes import NoteSnapshot


SCHEMA_VERSION = 1


class IndexUnavailable(ValueError):
    """Raised when the index is absent or its schema is not supported."""


@dataclass(frozen=True)
class IndexStatus:
    """Differences between current note hashes and the saved index."""

    new: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: int
    rebuild: bool = False

    @property
    def needs_update(self) -> bool:
        """Whether the saved index is missing any current information."""
        return self.rebuild or bool(self.new or self.changed or self.removed)


@dataclass(frozen=True)
class IndexedParagraph:
    """A passage and its normalized float32 vector loaded from the index."""

    note_name: str
    ordinal: int
    text: str
    vector: np.ndarray


def connect_index(path: Path, *, write: bool = False) -> sqlite3.Connection:
    """Open an index; read-only access never creates a database or sidecars."""
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
    else:
        if not path.is_file():
            raise IndexUnavailable("Index is missing; run 'badger index' first")
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION) or (not write and version == 0):
            raise IndexUnavailable("Unsupported index schema; remove the old index and run 'badger index'")
        if write:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notes (name TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paragraphs (
                    note_name TEXT NOT NULL REFERENCES notes(name) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    PRIMARY KEY (note_name, ordinal)
                );
                """
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return conn
    except BaseException:
        conn.close()
        raise


def inspect_index(
    conn: sqlite3.Connection, config: Config, snapshots: dict[str, NoteSnapshot]
) -> IndexStatus:
    """Compare SHA-256s and detect configuration changes requiring a rebuild."""
    saved = dict(conn.execute("SELECT name, sha256 FROM notes"))
    old_fingerprint_row = conn.execute(
        "SELECT value FROM metadata WHERE key = 'fingerprint'"
    ).fetchone()
    rebuild = old_fingerprint_row is None or old_fingerprint_row[0] != config.index_fingerprint()
    new = tuple(sorted(snapshots.keys() - saved.keys()))
    changed = tuple(sorted(name for name in snapshots.keys() & saved.keys()
                           if snapshots[name].digest != saved[name]))
    removed = tuple(sorted(saved.keys() - snapshots.keys()))
    return IndexStatus(new, changed, removed, len(snapshots) - len(new) - len(changed), rebuild)


def update_index(
    conn: sqlite3.Connection,
    config: Config,
    snapshots: dict[str, NoteSnapshot],
    status: IndexStatus,
    *,
    embedder: Embedder | None = None,
    force: bool = False,
) -> int:
    """Embed only new/changed notes and commit all modifications atomically.

    An incompatible configuration or ``force`` re-embeds the full corpus. Model
    failures occur before the transaction, so the previous index stays usable.
    Returns the number of paragraphs embedded in this operation.
    """
    rebuild = force or status.rebuild
    names = sorted(snapshots) if rebuild else sorted((*status.new, *status.changed))
    paragraphs_by_note = {
        name: snapshots[name].paragraphs(config.min_paragraph_chars) for name in names
    }
    texts = [text for name in names for text in paragraphs_by_note[name]]
    vectors: np.ndarray | None = None
    if texts:
        if embedder is None:
            from .embeddings import SentenceTransformerEmbedder

            embedder = SentenceTransformerEmbedder(config)
        vectors = np.asarray(embedder.documents(texts), dtype=np.float32)
        if (vectors.ndim != 2 or vectors.shape[0] != len(texts)
                or vectors.shape[1] == 0 or not np.isfinite(vectors).all()):
            raise ValueError("The embedding model returned invalid paragraph vectors")
        norms = np.linalg.norm(vectors, axis=1)
        if (norms == 0).any() or not np.isfinite(norms).all():
            raise ValueError("The embedding model returned zero or invalid paragraph vectors")
        vectors = vectors / norms[:, None]

    old_dim_row = conn.execute(
        "SELECT value FROM metadata WHERE key = 'dimension'"
    ).fetchone()
    old_dim = int(old_dim_row[0]) if old_dim_row else None
    if vectors is not None and not rebuild and old_dim is not None and vectors.shape[1] != old_dim:
        raise ValueError("Embedding dimension changed; run 'badger index --rebuild'")

    with conn:
        if rebuild:
            conn.execute("DELETE FROM notes")
            conn.execute("DELETE FROM metadata")
        else:
            conn.executemany(
                "DELETE FROM notes WHERE name = ?",
                ((name,) for name in (*status.changed, *status.removed)),
            )
        conn.executemany(
            "INSERT INTO notes (name, sha256) VALUES (?, ?)",
            ((name, snapshots[name].digest) for name in names),
        )
        if vectors is not None:
            records = []
            vector_id = 0
            for name in names:
                for ordinal, paragraph in enumerate(paragraphs_by_note[name]):
                    vector = np.asarray(vectors[vector_id], dtype="<f4")
                    records.append((name, ordinal, paragraph, vector.tobytes()))
                    vector_id += 1
            conn.executemany(
                "INSERT INTO paragraphs (note_name, ordinal, text, embedding) VALUES (?, ?, ?, ?)",
                records,
            )
        conn.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('fingerprint', ?)",
            (config.index_fingerprint(),),
        )
        dim = vectors.shape[1] if vectors is not None else (None if rebuild else old_dim)
        if dim is not None:
            conn.execute("INSERT OR REPLACE INTO metadata VALUES ('dimension', ?)", (str(dim),))
    return len(texts)


def paragraph_count(conn: sqlite3.Connection) -> int:
    """Count searchable passages without loading vectors or the model."""
    return conn.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0]


def load_paragraphs(conn: sqlite3.Connection) -> list[IndexedParagraph]:
    """Load saved paragraphs in stable order for in-memory dot product search."""
    return [
        IndexedParagraph(name, ordinal, text, np.frombuffer(blob, dtype="<f4"))
        for name, ordinal, text, blob in conn.execute(
            "SELECT note_name, ordinal, text, embedding "
            "FROM paragraphs ORDER BY note_name, ordinal"
        )
    ]
