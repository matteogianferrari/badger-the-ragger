"""Focused tests of incremental indexing and exact paragraph attribution."""

from __future__ import annotations

from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from badger_ragger.cli import main
from badger_ragger.config import Config, load_config
from badger_ragger.index import (
    connect_index, inspect_index, load_paragraphs, paragraph_count, update_index,
)
from badger_ragger.notes import is_completed_note, scan_notes, split_paragraphs
from badger_ragger.search import rank_notes


class FakeEmbedder:
    """Small deterministic stand-in; tests never download a model."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def documents(self, texts: list[str]) -> np.ndarray:
        """Give paragraphs predictable unit vectors and record encoding work."""
        self.calls.append(texts)
        return np.stack([self._vector(text) for text in texts])

    def query(self, text: str) -> np.ndarray:
        """Return the same semantic direction as matching passages."""
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> np.ndarray:
        if "patch" in text.lower():
            return np.array([1.0, 0.0], dtype=np.float32)
        return np.array([0.0, 1.0], dtype=np.float32)


class BadgerTest(unittest.TestCase):
    """Verify splitting, diffing, transactional writes, ranking, and the CLI."""

    def setUp(self) -> None:
        """Create a disposable Zettelkasten and local database."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.notes = self.root / "notes"
        self.notes.mkdir()
        self.config = Config(self.notes, self.root / "index.sqlite3")
        (self.notes / "a.md").write_text(
            "---\nNote Status: Completed\ntags: [idea]\n---\n\n# Introduction\n\n"
            "Patching an image divides it into smaller pieces.\n\n"
            "# Transformer\n\nTransformer architectures process sequences of text.",
            encoding="utf-8",
        )
        (self.notes / "b.md").write_text(
            "Note Status: Completed\n\nSome ideas concern algorithms for learning from sequences.",
            encoding="utf-8",
        )

    def build(self, model: FakeEmbedder | None = None) -> FakeEmbedder:
        """Populate the test index with a deterministic embedder."""
        model = model or FakeEmbedder()
        snapshots = scan_notes(self.notes)
        with closing(connect_index(self.config.index_path, write=True)) as conn:
            status = inspect_index(conn, self.config, snapshots)
            update_index(conn, self.config, snapshots, status, embedder=model)
        return model

    def test_paragraphs_skip_headings_metadata_and_tiny_blocks(self) -> None:
        """Keep meaningful text and preserve exact displayed passage content."""
        text = "---\r\ntags: [idea]\r\n---\r\n\r\n# Introduction\r\n\r\nHi\r\n\r\nAn actual paragraph about conceptual links."
        self.assertEqual(split_paragraphs(text, 20), ["An actual paragraph about conceptual links."])

    def test_only_an_explicit_completed_header_is_eligible(self) -> None:
        """Support the vault preamble and YAML while excluding drafts and ambiguity."""
        cases = (
            (b"\xef\xbb\xbf29/09/2026 10:06\r\nNote Status: Completed\r\n\r\nText", True),
            (b"---\nNote Status: completed\ntags: [idea]\n---\n\nText", True),
            (b"Note Status: #in-progress\n\nText", False),
            (b"---\nNote Status: #in-progress\n---\n\nText", False),
            (b"Note Status: Completed\nNote Status: #in-progress\n\nText", False),
            (b"# Introduction\n\nNote Status: Completed\n\nText", False),
            (b"Text without a status", False),
        )
        for content, expected in cases:
            with self.subTest(content=content):
                self.assertEqual(is_completed_note(content, "sample.md"), expected)

        (self.notes / "draft.md").write_text(
            "Note Status: #in-progress\n\nA draft about models.", encoding="utf-8"
        )
        self.assertEqual(sorted(scan_notes(self.notes)), ["a.md", "b.md"])

    def test_vault_preamble_embeds_references_and_math(self) -> None:
        """Ignore note scaffolding without splitting a displayed equation."""
        text = (
            "31/08/2026 22:28\nNote Status: Completed\nTags: #forecasting #rnn\n\n"
            "---\n# Introduction\n\nThe model predicts a distribution over time.\n\n"
            "![[DeepAR Inference.png]]\n\n# Architecture\n\n"
            "The hidden state determines its parameters:\n$$\n\n"
            "\\theta_t=\\theta(h_t)\n$$\n\n"
            "A second explanation follows the complete equation.\n\n"
            "---\n# References\n[[@salinasDeepARProbabilisticForecasting2019]]"
        )
        self.assertEqual(split_paragraphs(text, 20), [
            "The model predicts a distribution over time.",
            "The hidden state determines its parameters:\n$$\n\n\\theta_t=\\theta(h_t)\n$$",
            "A second explanation follows the complete equation.",
        ])

    def test_note_ranking_uses_maximum_paragraph_and_its_text(self) -> None:
        """A note's score and output text come from the same best paragraph."""
        self.build()
        with closing(connect_index(self.config.index_path)) as conn:
            passages = load_paragraphs(conn)
        results = rank_notes(FakeEmbedder().query("patching"), passages, 2)
        self.assertEqual([result.note_name for result in results], ["a.md", "b.md"])
        self.assertAlmostEqual(results[0].score, 1.0)
        self.assertEqual(results[0].paragraph, "Patching an image divides it into smaller pieces.")
        self.assertEqual(len([result for result in results if result.note_name == "a.md"]), 1)

    def test_incremental_new_changed_removed_and_unchanged(self) -> None:
        """Only new and changed passages are embedded; removed notes vanish."""
        self.build()
        with closing(connect_index(self.config.index_path)) as conn:
            before = conn.execute(
                "SELECT embedding FROM paragraphs WHERE note_name = 'b.md'"
            ).fetchone()[0]
        (self.notes / "a.md").write_text(
            "Note Status: Completed\n\nA revised passage about patching and visual tokens.",
            encoding="utf-8",
        )
        (self.notes / "sub").mkdir()
        (self.notes / "sub" / "c.md").write_text(
            "Note Status: Completed\n\nNew paragraphs connect unrelated ideas across notes.",
            encoding="utf-8",
        )
        (self.notes / "headings.md").write_text(
            "Note Status: Completed\n\n# Only a heading", encoding="utf-8"
        )
        model = FakeEmbedder()
        snapshots = scan_notes(self.notes)
        with closing(connect_index(self.config.index_path, write=True)) as conn:
            status = inspect_index(conn, self.config, snapshots)
            self.assertEqual(status.changed, ("a.md",))
            self.assertEqual(status.new, ("headings.md", "sub/c.md"))
            self.assertEqual(status.unchanged, 1)
            self.assertEqual(update_index(conn, self.config, snapshots, status, embedder=model), 2)
            self.assertEqual(conn.execute(
                "SELECT embedding FROM paragraphs WHERE note_name = 'b.md'"
            ).fetchone()[0], before)
            self.assertEqual(paragraph_count(conn), 3)
            self.assertFalse(inspect_index(conn, self.config, scan_notes(self.notes)).needs_update)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(model.calls[0]), 2)

        (self.notes / "a.md").unlink()
        with closing(connect_index(self.config.index_path, write=True)) as conn:
            status = inspect_index(conn, self.config, scan_notes(self.notes))
            self.assertEqual(status.removed, ("a.md",))
            self.assertEqual(update_index(conn, self.config, scan_notes(self.notes), status), 0)
            self.assertEqual(paragraph_count(conn), 2)

    def test_embedding_failure_preserves_previous_index(self) -> None:
        """A failed model call must leave every saved hash and passage intact."""
        self.build()
        (self.notes / "b.md").write_text(
            "Note Status: Completed\n\nPatching has changed this passage significantly.",
            encoding="utf-8",
        )

        class BrokenEmbedder(FakeEmbedder):
            def documents(self, texts: list[str]) -> np.ndarray:
                raise RuntimeError("model failure")

        with closing(connect_index(self.config.index_path, write=True)) as conn:
            snapshots = scan_notes(self.notes)
            status = inspect_index(conn, self.config, snapshots)
            with self.assertRaisesRegex(RuntimeError, "model failure"):
                update_index(conn, self.config, snapshots, status, embedder=BrokenEmbedder())
            self.assertEqual(paragraph_count(conn), 3)
            self.assertEqual(inspect_index(conn, self.config, snapshots).changed, ("b.md",))

    def test_config_changes_trigger_rebuild_only_when_index_content_changes(self) -> None:
        """Query wording can change without regenerating paragraph vectors."""
        self.build()
        snapshots = scan_notes(self.notes)
        with closing(connect_index(self.config.index_path)) as conn:
            self.assertFalse(inspect_index(conn, replace(self.config, query_prompt="New query: "), snapshots).rebuild)
            self.assertTrue(inspect_index(conn, replace(self.config, min_paragraph_chars=50), snapshots).rebuild)
            self.assertTrue(inspect_index(
                conn, replace(self.config, exclude_globs=("CONFIG.md",)), snapshots
            ).rebuild)

    def test_old_splitter_index_requires_a_rebuild(self) -> None:
        """An existing index must not silently keep passages from old rules."""
        self.build()
        with closing(connect_index(self.config.index_path, write=True)) as conn:
            fingerprint = json.loads(self.config.index_fingerprint())
            fingerprint["splitter_version"] = 1
            conn.execute(
                "UPDATE metadata SET value = ? WHERE key = 'fingerprint'",
                (json.dumps(fingerprint, sort_keys=True),),
            )
            conn.commit()
            self.assertTrue(inspect_index(conn, self.config, scan_notes(self.notes)).rebuild)

    def test_configured_exclusions_skip_non_note_pages(self) -> None:
        """Meta pages are not hashed or embedded when explicitly excluded."""
        (self.notes / "CONFIG.md").write_text(
            "Note Status: Completed\n\nThis page contains configuration settings.",
            encoding="utf-8",
        )
        (self.notes / "Library").mkdir()
        (self.notes / "Library" / "Badger.md").write_text(
            "Note Status: Completed\n\nThis page contains Space Lua integration code.",
            encoding="utf-8",
        )
        names = scan_notes(self.notes, ("CONFIG.md", "Library/**"))
        self.assertEqual(sorted(names), ["a.md", "b.md"])

    def test_cli_reports_stale_files_and_formats_exact_best_paragraph(self) -> None:
        """The search command prints score; note; best paragraph and blocks stale results."""
        self.build()
        settings = self.root / "badger.toml"
        settings.write_text(f'notes_root = "{self.notes}"\nindex_path = "index.sqlite3"\n', encoding="utf-8")
        self.assertEqual(load_config(settings).notes_root, self.notes)
        out, err = io.StringIO(), io.StringIO()
        with patch("badger_ragger.cli.SentenceTransformerEmbedder", return_value=FakeEmbedder()):
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main(["--config", str(settings), "search", "patching", "-k", "1"]), 0)
        self.assertEqual(out.getvalue().strip(), "1.0000; a.md; Patching an image divides it into smaller pieces.")
        (self.notes / "b.md").write_text(
            "Note Status: Completed\n\nAn entirely new passage about patching algorithms.",
            encoding="utf-8",
        )
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(["--config", str(settings), "search", "patching"]), 2)
        self.assertIn("Changed: b.md", err.getvalue())
        self.assertIn("run 'badger index'", err.getvalue())
        self.assertEqual(out.getvalue(), "")

    def test_cli_index_runs_once_and_skips_unchanged_notes(self) -> None:
        """The CLI calls the model for initial indexing, then avoids re-encoding."""
        settings = self.root / "badger.toml"
        settings.write_text(f'notes_root = "{self.notes}"\nindex_path = "index.sqlite3"\n', encoding="utf-8")
        model = FakeEmbedder()
        with patch("badger_ragger.embeddings.SentenceTransformerEmbedder", return_value=model):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(settings), "index"]), 0)
                self.assertEqual(main(["--config", str(settings), "index"]), 0)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(model.calls[0]), 3)


if __name__ == "__main__":
    unittest.main()
