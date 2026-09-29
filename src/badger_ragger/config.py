"""Validated TOML configuration and embedding index compatibility settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import tomllib


SPLITTER_VERSION = 2


class ConfigurationError(ValueError):
    """Raised when a configuration file is missing or has invalid values."""


@dataclass(frozen=True)
class Config:
    """Resolved settings for scanning notes, building an index, and querying it."""

    notes_root: Path
    index_path: Path
    model_name: str = "Qwen/Qwen3-Embedding-0.6B"
    device: str = "auto"
    batch_size: int = 32
    min_paragraph_chars: int = 20
    top_k: int = 10
    exclude_globs: tuple[str, ...] = ()
    query_prompt: str = (
        "Instruct: Given an idea or concept, retrieve passages from personal "
        "notes that are semantically related.\nQuery: "
    )

    def index_fingerprint(self) -> str:
        """Identify settings that require every paragraph to be re-embedded."""
        settings = {
            "notes_root": str(self.notes_root),
            "model_name": self.model_name,
            "min_paragraph_chars": self.min_paragraph_chars,
            "splitter_version": SPLITTER_VERSION,
        }
        if self.exclude_globs:
            settings["exclude_globs"] = self.exclude_globs
        return json.dumps(settings, sort_keys=True)


def load_config(path: Path) -> Config:
    """Read TOML, resolve paths relative to it, and reject invalid settings."""
    path = path.expanduser().resolve()
    try:
        with path.open("rb") as handle:
            values = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Cannot read configuration {path}: {exc}") from exc

    allowed = set(Config.__dataclass_fields__)  # type: ignore[attr-defined]
    unknown = set(values) - allowed
    if unknown:
        raise ConfigurationError(f"Unknown configuration setting(s): {', '.join(sorted(unknown))}")
    if "notes_root" not in values:
        raise ConfigurationError("Configuration must set notes_root")

    def resolve(value: str, name: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"{name} must be a nonempty path string")
        expanded = Path(value).expanduser()
        return (path.parent / expanded).resolve()

    notes_root = resolve(values.pop("notes_root"), "notes_root")
    index_path = resolve(values.pop("index_path", ".badger/index.sqlite3"), "index_path")
    defaults = Config(notes_root, index_path)
    settings = {
        name: values.get(name, getattr(defaults, name))
        for name in ("model_name", "device", "batch_size", "min_paragraph_chars", "top_k", "query_prompt")
    }
    excludes = values.get("exclude_globs", [])
    if not isinstance(excludes, list) or any(
        not isinstance(item, str) or not item.strip() for item in excludes
    ):
        raise ConfigurationError("exclude_globs must be a list of nonempty path patterns")
    settings["exclude_globs"] = tuple(excludes)
    for name in ("model_name", "device", "query_prompt"):
        if not isinstance(settings[name], str) or not settings[name].strip():
            raise ConfigurationError(f"{name} must be a nonempty string")
    for name in ("batch_size", "top_k", "min_paragraph_chars"):
        value = settings[name]
        if type(value) is not int or value < (0 if name == "min_paragraph_chars" else 1):
            raise ConfigurationError(f"{name} must be a positive integer" if name != "min_paragraph_chars" else f"{name} must be a nonnegative integer")
    if not notes_root.is_dir():
        raise ConfigurationError(f"Notes directory does not exist: {notes_root}")
    return Config(notes_root=notes_root, index_path=index_path, **settings)
