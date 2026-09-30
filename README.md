# Badger the Ragger

<img src="assets/badger-icon.png" alt="Badger the Ragger icon" width="180">

Badger searches Markdown Zettelkasten notes by meaning. It saves one embedding
per meaningful paragraph, then returns **one result per note**: the paragraph
with that note's highest cosine similarity to your query. The output is the
retrieval part of a RAG workflow; it does not generate an answer.

## Install

Requires Python 3.11 or newer. From this project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Edit [`badger.toml`](badger.toml). Its `notes_root` is initialized from the
path in your prototype. Set it to the actual folder containing your Markdown
notes. Paths can contain spaces; relative paths are resolved from the config
file's directory. The index is kept at `.badger/index.sqlite3` by default.

The default embedding model is
[Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B),
released under Apache 2.0. The first indexing/search run downloads and caches
the model (about 1.2 GB of model weights); later runs reuse the download. The
`device = "auto"` setting chooses an available accelerator or CPU. A CUDA
machine can set `device = "cuda:0"`. CPU inference is usually slower.

## Use

```bash
badger index
badger search "patching reduces the cost of attention"
badger search "knowledge graph connections" -k 5
badger status
badger repl
```

## Use inside Obsidian or SilverBullet

Keep Badger running while either editor is open:

```bash
badger serve
```

The service listens at `http://127.0.0.1:8765` by default and loads the model
only when indexing or searching; subsequent requests reuse it. The existing
CLI `index`, `status`, `search`, and `repl` commands still work independently.
The editors also keep indexing separate from inference. Restart `badger serve`
after editing `badger.toml`.

For **Obsidian desktop**, follow
[`integrations/obsidian/README.md`](integrations/obsidian/README.md) to copy
the ready-to-use plugin into the vault. It adds search, status, and index
commands; a result shows a separate score and note button, renders the matching
paragraph with Markdown and LaTeX math, and can open its note at that paragraph.
Ctrl+left click on a result opens the note in a new tab.

For **SilverBullet 2**, follow
[`integrations/silverbullet/README.md`](integrations/silverbullet/README.md)
to add the Space Lua page. It adds search, status, incremental index, and
full rebuild commands. Its side panel shows the full `score; note; paragraph`
record and a link to each note. SilverBullet sends requests from its server,
so the Badger URL must be reachable from that server.

Both interfaces show affected Completed note names when SHA-256 detects changes.
They require an explicit index action before showing results from changed notes.

### Local JSON API

| Method and path | Request | Response |
| --- | --- | --- |
| `GET /status` | None | `{ "status": { "new": [], "changed": [], "removed": [], "needs_update": ... } }` |
| `POST /index` | `{}` or `{ "rebuild": true }` | Index counts and previous change status |
| `POST /search` | `{ "query": "...", "top_k": 10 }` | `{ "results": [{ "score": 0.83, "note_name": "...", "paragraph": "..." }] }` |

The server returns HTTP 409 plus the affected file names when an index is
stale, so clients cannot silently show out-of-date text. If you must bind
outside localhost, set `BADGER_API_TOKEN` in the service environment first,
then configure the same bearer token in the editor integration. Requests
are JSON; the server never exposes note files through an HTTP path.

The default `top_k` is 10 distinct notes. Each search line has this form:

```text
0.8342; 2024-attention.md; Patching reduces the number of tokens the transformer attends to.
```

Scores are cosine similarities between normalized query and paragraph
embeddings; higher means more similar. The printed paragraph is the one that
produced that note's score. Embedded newlines and backslashes in a paragraph
are escaped as `\n` and `\\` so each result stays on one line. Semicolons in
the paragraph are part of its text.

Badger scans Markdown notes whose leading metadata block or YAML front matter
contains exactly one `Note Status: Completed` field. For example:

```markdown
29/09/2026 10:06
Note Status: Completed
Tags: #ideas

The actual note begins here.
```

Notes marked `Note Status: #in-progress`, notes without a status, and notes
with conflicting status fields are excluded. An edit to an in-progress note
does not change index status or trigger embedding. When its status becomes
Completed, `badger status` reports it as new and `badger index` embeds its
paragraphs. Later edits to a Completed note are reported as changed and only
that note is re-embedded. If a Completed note becomes in-progress, the next
index action removes its old paragraphs without loading the model.

Run `badger index` after editing, adding, deleting, or completing notes. It
compares each eligible file's current SHA-256 with the saved hash and reports
the affected file names. A Completed note without a substantive paragraph is
still recorded, so it is not repeatedly processed. Index updates are
transactional: a model failure leaves the previous index intact.

`badger search` and each `badger repl` query check the file hashes before
inference. If the index is stale, Badger prints the affected file names and
asks you to run `badger index`; it never gives a result from stale content.
`badger status` does the same check without loading the model. To re-embed
everything explicitly, run `badger index --rebuild`.

The `repl` command keeps the model in memory between queries, which avoids
repeated model startup time. Search still needs to encode each query, but it
uses saved paragraph vectors and one NumPy matrix multiplication for ranking.
The index and embeddings stay on your machine; installing/downloading the
model requires network access once unless it is already cached.

Pass a different settings file with `badger --config /path/to/badger.toml
index`, and similarly for `search`, `status`, and `repl`.

## Configuration and processing

| Setting | Purpose |
| --- | --- |
| `notes_root` | Directory to scan recursively for `.md` notes (required) |
| `index_path` | Local SQLite file with SHA-256s, paragraphs, and embeddings |
| `model_name` | Open embedding model recognized by Sentence Transformers |
| `device` | `auto`, `cpu`, `cuda:0`, `mps`, etc. |
| `batch_size` | Number of paragraphs per embedding batch |
| `min_paragraph_chars` | Minimum number of visible characters (default 20) |
| `top_k` | Default number of distinct notes returned (default 10) |
| `exclude_globs` | Optional note-root-relative patterns for non-note Markdown pages |
| `query_prompt` | Instruction prepended to queries, not to documents |

Blank lines split notes into paragraphs, except inside `$$...$$` display math.
Markdown front matter, a leading date/status/tags block, standalone headings,
horizontal rules, image embeds, bibliography-only wiki links, and very short
fragments are skipped. Other paragraph text is preserved for output, including
its Markdown formatting. Files must be UTF-8. Note names in results are relative to
`notes_root`, so notes with the same filename in different folders remain
distinct.

If `notes_root` is an entire SilverBullet space, exclude script and metadata
pages with e.g. `exclude_globs = ["CONFIG.md", "Library/**", "Badger.md"]`.
Changing exclusions triggers an index rebuild, like changing paragraph rules.

Changing the note root, model name, minimum paragraph length, or splitter
version triggers a full rebuild on the next `badger index`. Changing query
prompt, device, batch size, or `top_k` does not change saved document vectors.
If the upstream model behind the same `model_name` has changed, run
`badger index --rebuild` manually.

After upgrading to version 0.2.4, restart a running `badger serve` process and
use **Update note embeddings** in the editor or run `badger index` once. Notes
that are no longer eligible appear as removed. Unchanged Completed notes retain
their existing embeddings; a full rebuild is unnecessary.

## Development

Run the focused tests without downloading a model:

```bash
python -m unittest discover -s tests -v
node --test tests/test_obsidian.cjs
```

`notes.py` owns scanning and paragraph selection; `embeddings.py` is the
replaceable model adapter; `index.py` manages hashes and SQLite updates;
`search.py` handles scoring; `service.py` shares indexing and retrieval with
`server.py`; `cli.py` handles terminal interaction. Tests inject a tiny
deterministic embedder to check incremental updates, HTTP responses, and
ranking. The Obsidian adapter has a mocked runtime test. Live editor and
model runs require your installed editor, notes, and model download.
