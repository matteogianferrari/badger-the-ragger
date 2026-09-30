"""Read Markdown notes, hash source bytes, and select meaningful paragraphs."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path
import re
from typing import Iterator


_BLANK_LINES = re.compile(r"\n[ \t]*\n+")
_HEADING = re.compile(r"^ {0,3}#{1,6}(?:\s+|$)")
_RULE = re.compile(r"^ {0,3}(?:[-*_][ \t]*){3,}$")
_IMAGE = re.compile(r"^!\[[^\]]*\]\([^)]*\)$")
_EMBED = re.compile(r"^!\[\[[^\]]+\]\]$")
_CITATION = re.compile(r"^\[\[@[^\]]+\]\]$")
_NOTE_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}(?:[ \t]+\d{1,2}:\d{2})?[ \t]*$")
_NOTE_FIELD = re.compile(r"^(?:Note Status|Tags):", re.IGNORECASE)
_STATUS_LINE = re.compile(r"^[ \t]*Note Status:[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE)
_MARKUP = re.compile(r"[#*_`~>\[\]()!|]")


@dataclass(frozen=True)
class NoteSnapshot:
    """A source note's relative name, SHA-256 digest, and bytes at scan time."""

    name: str
    digest: str
    content: bytes

    def paragraphs(self, min_chars: int) -> list[str]:
        """Decode this note and return its nontrivial Markdown paragraphs."""
        try:
            text = self.content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Note is not UTF-8: {self.name}") from exc
        return split_paragraphs(text, min_chars)


def scan_notes(root: Path, exclude_globs: tuple[str, ...] = ()) -> dict[str, NoteSnapshot]:
    """Hash only Completed Markdown notes, skipping configured non-note pages."""
    snapshots: dict[str, NoteSnapshot] = {}
    for path in sorted(root.rglob("*.md")):
        if path.is_file():
            name = path.relative_to(root).as_posix()
            if any(fnmatchcase(name, pattern) for pattern in exclude_globs):
                continue
            content = path.read_bytes()
            if not is_completed_note(content, name):
                continue
            snapshots[name] = NoteSnapshot(name, sha256(content).hexdigest(), content)
    return snapshots


def is_completed_note(content: bytes, name: str) -> bool:
    """Check the note header for one explicit Completed status.

    Both this vault's leading date/status block and YAML front matter are
    supported. Missing, ambiguous, or other statuses are not searchable.
    """
    try:
        text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Note is not UTF-8: {name}") from exc
    if text.startswith("---\n"):
        closing = re.search(r"(?m)^---[ \t]*$", text[4:])
        if closing is not None:
            header = text[4:4 + closing.start()]
        else:
            header = ""
    else:
        boundary = _BLANK_LINES.search(text)
        header = text[:boundary.start()] if boundary else text
    statuses = [match.group(1).strip().casefold() for match in _STATUS_LINE.finditer(header)]
    return statuses == ["completed"]


def _without_note_header(text: str) -> str:
    """Remove this vault's date/status/tag preamble when it is a whole block."""
    boundary = _BLANK_LINES.search(text)
    head = text[:boundary.start()] if boundary else text
    lines = [line.strip() for line in head.splitlines()]
    if lines and (_NOTE_DATE.fullmatch(lines[0]) or _NOTE_FIELD.match(lines[0])) and all(
        _NOTE_FIELD.match(line) for line in lines[1:]
    ):
        return text[boundary.end():] if boundary else ""
    return text


def _paragraph_blocks(text: str) -> Iterator[str]:
    """Split on blank lines, except those inside a display math block."""
    start = 0
    for boundary in _BLANK_LINES.finditer(text):
        if text[start:boundary.start()].count("$$") % 2 == 0:
            yield text[start:boundary.start()]
            start = boundary.end()
    yield text[start:]


def split_paragraphs(text: str, min_chars: int) -> list[str]:
    """Keep substantive passages while removing note scaffolding and embeds."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if text.startswith("---\n"):
        end = re.search(r"(?m)^---[ \t]*$", text[4:])
        if end is not None:
            text = text[4 + end.end():].lstrip("\n")
    text = _without_note_header(text)

    paragraphs = []
    for block in _paragraph_blocks(text):
        kept_lines = [
            line for line in block.splitlines(keepends=True)
            if not (_HEADING.match(line.strip()) or _RULE.fullmatch(line.strip())
                    or _IMAGE.fullmatch(line.strip()) or _EMBED.fullmatch(line.strip()))
        ]
        paragraph = "".join(kept_lines).strip()
        if not paragraph or _CITATION.fullmatch(paragraph):
            continue
        visible = _MARKUP.sub("", paragraph).strip()
        if len(visible) < min_chars or not any(char.isalnum() for char in visible):
            continue
        paragraphs.append(paragraph)
    return paragraphs
