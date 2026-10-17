"""Configuration: one JSON file, every path overridable, nothing under ~/.claude."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

ENV_CONFIG = "ACE_PLAYBOOK_CONFIG"
DEFAULT_CONFIG = Path.home() / ".config" / "ace-playbook" / "config.json"
DEFAULT_DATA = Path.home() / ".local" / "share" / "ace-playbook"


@dataclass
class Config:
    data_dir: Path = DEFAULT_DATA
    transcripts_root: Path | None = None
    api_key_path: Path | None = None
    engagements_path: Path | None = None
    fold_key_separators: bool = False
    model: str = "claude-haiku-5-5"
    api_url: str = "https://api.anthropic.com/v1/messages"
    effort: str | None = "low"
    max_tokens: int = 4096
    timeout_s: float = 90.0
    # injector
    inject_mode: str = "pinned"  # pinned (stage 1) | ranked
    inject_max_chars: int = 7000
    max_global: int = 4
    max_project: int = 8
    max_exploratory: int = 3
    holdout_enabled: bool = True
    holdout_rate: float = 0.5
    holdout_seed: str = "ace-playbook-v1"
    # reflector limits
    max_new_per_run: int = 3
    max_touched_per_run: int = 5
    tool_result_max_chars: int = 2000
    untrusted_path_markers: list[str] = field(
        default_factory=lambda: ["upstream/", "node_modules/", ".venv/", "vendor/"]
    )
    deny_terms: list[str] = field(default_factory=list)

    # derived paths -------------------------------------------------------
    def path(self, name: str) -> Path:
        return Path(self.data_dir) / name

    @property
    def deltas(self) -> Path:
        return self.path("deltas.jsonl")

    @property
    def sqlite(self) -> Path:
        return self.path("playbook.sqlite")

    @property
    def injections(self) -> Path:
        return self.path("injections.jsonl")

    @property
    def citations(self) -> Path:
        return self.path("citations.jsonl")

    @property
    def reflections(self) -> Path:
        return self.path("reflections.jsonl")

    @property
    def queue(self) -> Path:
        return self.path("queue.jsonl")

    @property
    def quarantine(self) -> Path:
        return self.path("quarantine.jsonl")

    @property
    def log(self) -> Path:
        return self.path("reflect.log")


_PATH_FIELDS = {"data_dir", "transcripts_root", "api_key_path", "engagements_path"}


def from_dict(raw: dict[str, Any]) -> Config:
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    kwargs = {k: (Path(os.path.expanduser(v)) if k in _PATH_FIELDS and v else v)
              for k, v in raw.items()}
    return Config(**kwargs)


def load(path: str | Path | None = None) -> Config:
    """Load config from `path`, $ACE_PLAYBOOK_CONFIG, or the default location.

    A missing default file yields defaults; a missing explicit path is an error.
    """
    explicit = path or os.environ.get(ENV_CONFIG)
    target = Path(explicit) if explicit else DEFAULT_CONFIG
    if not target.exists():
        if explicit:
            raise FileNotFoundError(f"config not found: {target}")
        return Config()
    return from_dict(json.loads(target.read_text()))
