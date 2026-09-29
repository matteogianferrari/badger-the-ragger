"""Command line entry point for inspecting, updating, and searching notes."""

from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys

from .config import ConfigurationError, Config, load_config
from .embeddings import Embedder, SentenceTransformerEmbedder
from .index import (
    IndexStatus, IndexUnavailable, connect_index, inspect_index,
    load_paragraphs, paragraph_count, update_index,
)
from .notes import scan_notes
from .search import rank_notes


def make_parser() -> argparse.ArgumentParser:
    """Build the CLI with explicit commands for embedding and inference."""
    parser = argparse.ArgumentParser(prog="badger", description="Search your Markdown notes by concept")
    parser.add_argument("--config", type=Path, default=Path("badger.toml"), help="TOML settings file")
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="Embed new/changed notes and remove deleted notes")
    index.add_argument("--rebuild", action="store_true", help="Re-embed all notes")
    commands.add_parser("status", help="Check note SHA-256s without loading the model")
    search = commands.add_parser("search", help="Find the best paragraph per note")
    search.add_argument("query", help="Words, an idea, or a concept")
    search.add_argument("-k", "--top-k", type=int, help="Maximum number of notes to show")
    repl = commands.add_parser("repl", help="Search repeatedly while keeping the model in memory")
    repl.add_argument("-k", "--top-k", type=int, help="Maximum number of notes to show")
    serve = commands.add_parser("serve", help="Start the local API for Obsidian or SilverBullet")
    serve.add_argument("--host", default="127.0.0.1", help="Interface to bind (default: localhost)")
    serve.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    return parser


def print_status(status: IndexStatus, *, prefix: str = "") -> None:
    """Notify the user about every new, changed, or removed note."""
    if status.rebuild:
        print(f"{prefix}Index missing or embedding settings changed; full rebuild needed.", file=sys.stderr)
    print(
        f"{prefix}Notes: {len(status.new)} new, {len(status.changed)} changed, "
        f"{len(status.removed)} removed, {status.unchanged} unchanged.",
        file=sys.stderr,
    )
    for label, names in (("New", status.new), ("Changed", status.changed), ("Removed", status.removed)):
        for name in names:
            print(f"{prefix}{label}: {name}", file=sys.stderr)


def current_status(config: Config) -> IndexStatus:
    """Scan all source hashes and compare them with the persisted index."""
    snapshots = scan_notes(config.notes_root, config.exclude_globs)
    if not config.index_path.is_file():
        return IndexStatus(tuple(sorted(snapshots)), (), (), 0, rebuild=True)
    with closing(connect_index(config.index_path)) as conn:
        return inspect_index(conn, config, snapshots)


def run_search(config: Config, query: str, top_k: int, embedder: Embedder | None) -> Embedder | None:
    """Refuse stale indexes, retrieve distinct notes, and print matching paragraphs."""
    if not query.strip():
        raise ValueError("Query cannot be empty")
    if top_k < 1:
        raise ValueError("top_k must be positive")
    snapshots = scan_notes(config.notes_root, config.exclude_globs)
    with closing(connect_index(config.index_path)) as conn:
        status = inspect_index(conn, config, snapshots)
        if status.needs_update:
            print_status(status)
            raise IndexUnavailable("Notes have changed; run 'badger index' before searching")
        if paragraph_count(conn) == 0:
            print("No searchable paragraphs in the index.")
            return embedder
        passages = load_paragraphs(conn)
    if embedder is None:
        embedder = SentenceTransformerEmbedder(config)
    results = rank_notes(embedder.query(query), passages, top_k)
    for result in results:
        # Keep each record on one line while retaining internal paragraph breaks.
        paragraph = result.paragraph.replace("\\", "\\\\").replace("\n", "\\n")
        print(f"{result.score:.4f}; {result.note_name}; {paragraph}")
    return embedder


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the selected task, and return a shell exit code."""
    args = make_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "status":
            print_status(current_status(config))
            return 0
        if args.command == "index":
            snapshots = scan_notes(config.notes_root, config.exclude_globs)
            with closing(connect_index(config.index_path, write=True)) as conn:
                status = inspect_index(conn, config, snapshots)
                print_status(status)
                if args.rebuild or status.needs_update:
                    count = update_index(conn, config, snapshots, status, force=args.rebuild)
                    print(f"Indexed {count} paragraphs from {len(snapshots)} notes.")
                else:
                    print("Index is current; no embeddings needed.")
            return 0
        if args.command == "search":
            run_search(config, args.query, args.top_k if args.top_k is not None else config.top_k, None)
            return 0
        if args.command == "serve":
            from .server import create_server

            with create_server(
                config, host=args.host, port=args.port,
                token=os.environ.get("BADGER_API_TOKEN", ""),
            ) as server:
                print(f"Badger API listening at http://{args.host}:{server.server_port}")
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    print("\nBadger API stopped.")
            return 0
        if args.command == "repl":
            model: Embedder | None = None
            print("Enter a query (Ctrl-D or :quit to exit).")
            while True:
                try:
                    query = input("badger> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if query in (":quit", ":q"):
                    return 0
                if query:
                    try:
                        model = run_search(
                            config, query,
                            args.top_k if args.top_k is not None else config.top_k,
                            model,
                        )
                    except IndexUnavailable as exc:
                        print(f"Error: {exc}", file=sys.stderr)
        return 0
    except (ConfigurationError, IndexUnavailable, OSError, sqlite3.Error, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
