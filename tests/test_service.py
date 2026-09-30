"""HTTP contract tests for both note editor integrations."""

from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest

import numpy as np

from badger_ragger.config import Config
from badger_ragger.server import create_server
from badger_ragger.service import BadgerService


class TestEmbedder:
    """Deterministic vector source that records model reuse."""

    def __init__(self) -> None:
        self.document_calls = 0
        self.query_calls = 0

    def documents(self, texts: list[str]) -> np.ndarray:
        """Return unit vectors tied to the passage's subject."""
        self.document_calls += 1
        return np.array([[1.0, 0.0] if "patch" in text.lower() else [0.0, 1.0]
                         for text in texts], dtype=np.float32)

    def query(self, text: str) -> np.ndarray:
        """Return a unit query vector."""
        self.query_calls += 1
        return np.array([1.0, 0.0] if "patch" in text.lower() else [0.0, 1.0], dtype=np.float32)


class ServiceTests(unittest.TestCase):
    """Exercise status, indexing, stale checks, search, and authentication."""

    def setUp(self) -> None:
        """Start a real loopback HTTP listener over a temporary notes folder."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.notes = root / "notes"
        self.notes.mkdir()
        (self.notes / "a.md").write_text(
            "Note Status: Completed\n\n# Heading\n\n"
            "Patching an image makes smaller visual tokens.", encoding="utf-8"
        )
        self.config = Config(self.notes, root / "index.sqlite3")
        self.model = TestEmbedder()
        self.factory_calls = 0

        def model_factory(config: Config) -> TestEmbedder:
            self.factory_calls += 1
            return self.model

        self.engine = BadgerService(self.config, model_factory=model_factory)
        self.server = create_server(self.config, port=0, token="secret", service=self.engine)
        self.worker = Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self) -> None:
        """Stop the listener before the temporary directory is removed."""
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)

    def request(self, method: str, path: str, body: dict | None = None, token: str = "secret") -> tuple[int, dict]:
        """Send a JSON request to the real listener and parse its response."""
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            headers = {"Authorization": "Bearer " + token}
            data = None if body is None else json.dumps(body)
            if data is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_full_editor_workflow_and_hash_notifications(self) -> None:
        """Index once, search fast, reject changed files, then refresh them."""
        code, body = self.request("GET", "/status")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"]["new"], ["a.md"])
        self.assertFalse(body["status"]["indexed"])
        self.assertEqual(self.factory_calls, 0)

        code, body = self.request("POST", "/search", {"query": "patching"})
        self.assertEqual(code, 409)
        self.assertEqual(body["status"]["new"], ["a.md"])
        self.assertFalse(body["status"]["indexed"])

        code, body = self.request("POST", "/index", {})
        self.assertEqual(code, 200)
        self.assertEqual(body["embedded_paragraphs"], 1)
        self.assertEqual(self.factory_calls, 1)
        code, body = self.request("POST", "/search", {"query": "patching", "top_k": 1})
        self.assertEqual(code, 200)
        self.assertEqual(body["results"], [{
            "score": 1.0,
            "note_name": "a.md",
            "paragraph": "Patching an image makes smaller visual tokens.",
        }])

        (self.notes / "a.md").write_text(
            "Note Status: Completed\n\nPatching transforms image sequences efficiently.",
            encoding="utf-8",
        )
        code, body = self.request("POST", "/search", {"query": "patching"})
        self.assertEqual(code, 409)
        self.assertEqual(body["status"]["changed"], ["a.md"])
        code, body = self.request("POST", "/index", {})
        self.assertEqual(code, 200)
        self.assertEqual(body["before"]["changed"], ["a.md"])
        self.assertEqual(body["embedded_paragraphs"], 1)
        self.assertEqual(self.factory_calls, 1)
        self.assertEqual(self.model.document_calls, 2)

    def test_in_progress_notes_do_not_invalidate_the_index(self) -> None:
        """Only the transition to Completed adds vectors; later edits refresh them."""
        draft = self.notes / "draft.md"
        draft.write_text(
            "29/09/2026 10:00\nNote Status: #in-progress\nTags: #ideas\n\n"
            "A preliminary thought about patching images.", encoding="utf-8"
        )
        (self.notes / "unmarked.md").write_text(
            "This note does not declare a status and is not searchable.", encoding="utf-8"
        )
        self.assertEqual(self.request("GET", "/status")[1]["status"]["new"], ["a.md"])
        self.assertEqual(self.request("POST", "/index", {})[1]["embedded_paragraphs"], 1)
        self.assertEqual(self.model.document_calls, 1)

        draft.write_text(
            "29/09/2026 10:05\nNote Status: #in-progress\nTags: #ideas\n\n"
            "A revised preliminary thought about patching images.", encoding="utf-8"
        )
        status = self.request("GET", "/status")[1]["status"]
        self.assertFalse(status["needs_update"])
        self.assertEqual(self.request("POST", "/index", {})[1]["embedded_paragraphs"], 0)
        self.assertEqual(self.model.document_calls, 1)
        self.assertEqual(len(self.request("POST", "/search", {"query": "patching"})[1]["results"]), 1)

        draft.write_text(
            "29/09/2026 10:06\nNote Status: Completed\nTags: #ideas\n\n"
            "A finished thought about patching images.", encoding="utf-8"
        )
        self.assertEqual(self.request("GET", "/status")[1]["status"]["new"], ["draft.md"])
        self.assertEqual(self.request("POST", "/index", {})[1]["embedded_paragraphs"], 1)
        self.assertEqual(self.model.document_calls, 2)

        draft.write_text(
            "29/09/2026 10:07\nNote Status: Completed\nTags: #ideas\n\n"
            "Another finished thought about patching images.", encoding="utf-8"
        )
        self.assertEqual(self.request("GET", "/status")[1]["status"]["changed"], ["draft.md"])
        self.assertEqual(self.request("POST", "/index", {})[1]["embedded_paragraphs"], 1)
        self.assertEqual(self.model.document_calls, 3)

        draft.write_text(
            "29/09/2026 10:08\nNote Status: #in-progress\nTags: #ideas\n\n"
            "This unfinished idea is no longer searchable.", encoding="utf-8"
        )
        self.assertEqual(self.request("GET", "/status")[1]["status"]["removed"], ["draft.md"])
        self.assertFalse(self.request("GET", "/status")[1]["status"]["rebuild"])
        self.assertEqual(self.request("POST", "/index", {})[1]["embedded_paragraphs"], 0)
        self.assertEqual(self.model.document_calls, 3)
        self.assertEqual(self.request("POST", "/index", {})[1]["indexed_notes"], 1)


    def test_http_validation_and_token(self) -> None:
        """Reject unauthenticated, invalid, and unexpectedly large requests."""
        self.assertEqual(self.request("GET", "/status", token="bad")[0], 401)
        self.assertEqual(self.request("POST", "/search", {"query": ""})[0], 400)
        self.assertEqual(self.request("POST", "/search", {"query": "x", "top_k": 0})[0], 400)
        self.assertEqual(self.request("POST", "/index", {"rebuild": "yes"})[0], 400)
        self.assertEqual(self.request("GET", "/missing")[0], 404)

    def test_model_failure_is_json_and_does_not_mark_notes_indexed(self) -> None:
        """Clients receive a useful error and can retry an interrupted index."""
        class BrokenEmbedder(TestEmbedder):
            def documents(self, texts: list[str]) -> np.ndarray:
                raise RuntimeError("embedding model unavailable")

        self.engine._model_factory = lambda config: BrokenEmbedder()
        code, body = self.request("POST", "/index", {})
        self.assertEqual(code, 500)
        self.assertIn("embedding model unavailable", body["error"])
        self.assertEqual(self.request("GET", "/status")[1]["status"]["new"], ["a.md"])

    def test_nonlocal_listener_requires_token(self) -> None:
        """Do not expose the note index on a network without authentication."""
        with self.assertRaisesRegex(ValueError, "BADGER_API_TOKEN"):
            create_server(self.config, host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
