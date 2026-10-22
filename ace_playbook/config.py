"""Configuration: one JSON file, every path overridable, nothing under ~/.claude."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar

from . import atomic

ENV_CONFIG = "ACE_PLAYBOOK_CONFIG"
DEFAULT_CONFIG = Path.home() / ".config" / "ace-playbook" / "config.json"
DEFAULT_DATA = Path.home() / ".local" / "share" / "ace-playbook"
SUGGESTED_KEY = "chain_key"   # keygen's suggestion beside the config file; never read unless `chain_key_path` in the config names it
MAX_INJECT_CHARS = 7000


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: DEFAULT_DATA)   # looked up per instance, so the test suite can pin it away from the real one
    transcripts_root: Path | None = None
    api_key_path: Path | None = None
    engagements_path: Path | None = None
    chain_key_path: Path | None = None  # the HMAC key of the approvals and demotions chains; None unless the config file names it
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
    # Holdout is off until a private seed is configured. Keep the seed out of the repo and
    # commit only its SHA-256 (see PREREGISTRATION.md); holdout_seed_sha256 lets the injector
    # refuse a seed that does not match the committed hash.
    holdout_enabled: bool = False
    holdout_rate: float = 0.5
    holdout_seed: str = ""
    holdout_seed_sha256: str = ""
    # reflector limits
    max_new_per_run: int = 3
    max_touched_per_run: int = 5
    tool_result_max_chars: int = 2000
    untrusted_path_markers: list[str] = field(
        default_factory=lambda: ["upstream/", "node_modules/", ".venv/", "vendor/"]
    )
    deny_terms: list[str] = field(default_factory=list)
    # the brief on wake (docs/design/compiler.md 8.x "Brief"): read-only, its own private directory under data_dir
    brief_dir: Path | None = None  # None means data_dir / "brief"
    brief_exclusions: Path | None = None  # private JSON: drop_senders, drop_subjects, drop_bodies_from (regex lists)
    brief_model: str = "claude-sonnet-5-5"  # stage 1 (tools: reads calendar, mail, issues)
    brief_model_stage2: str = "claude-sonnet-5-5"  # stage 2 (no tools: writes the brief JSON from the cleaned digest)
    brief_dedupe_hours: int = 4
    brief_max_budget_usd: float = 1.0
    source: ClassVar[Path | None] = None  # not a config key: the file `load` read (or would have read), set on the instance by `load`

    def __post_init__(self) -> None:
        if not 200 <= self.inject_max_chars <= MAX_INJECT_CHARS:
            raise ValueError(f"inject_max_chars must be 200..{MAX_INJECT_CHARS}")
        if not 0.0 <= self.holdout_rate <= 1.0:
            raise ValueError("holdout_rate must be between 0 and 1")
        if self.brief_dedupe_hours < 0 or not 0 < self.brief_max_budget_usd <= 100:
            raise ValueError("brief_dedupe_hours must be >= 0 and brief_max_budget_usd in (0, 100]")

    def check_holdout(self) -> None:
        """Raise ValueError if holdout is on without a usable private seed."""
        if not self.holdout_enabled:
            return
        if len(self.holdout_seed) < 16:
            raise ValueError("holdout_enabled needs a private holdout_seed of 16+ characters")
        if self.holdout_seed_sha256 and (
                hashlib.sha256(self.holdout_seed.encode()).hexdigest() != self.holdout_seed_sha256):
            raise ValueError("holdout_seed does not match holdout_seed_sha256")

    # derived paths -------------------------------------------------------
    def path(self, name: str) -> Path:
        return Path(self.data_dir) / name

    @property
    def brief_path(self) -> Path:
        return Path(self.brief_dir) if self.brief_dir else self.path("brief")

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


_PATH_FIELDS = {"data_dir", "transcripts_root", "api_key_path", "engagements_path", "chain_key_path", "brief_dir", "brief_exclusions"}


def from_dict(raw: dict[str, Any]) -> Config:
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    kwargs = {k: (Path(os.path.expanduser(v)) if k in _PATH_FIELDS and v else v)
              for k, v in raw.items()}
    return Config(**kwargs)


def config_file(path: str | Path | None = None) -> Path:
    """The config file `load` reads: `path`, else $ACE_PLAYBOOK_CONFIG, else the default location."""
    explicit = path or os.environ.get(ENV_CONFIG)
    return Path(explicit) if explicit else DEFAULT_CONFIG


def load(path: str | Path | None = None) -> Config:
    """Load config from `path`, $ACE_PLAYBOOK_CONFIG, or the default location.

    A missing default file yields defaults; a missing explicit path is an error. No key is implied: `chain_key_path` is None
    unless the file names it.
    """
    target = config_file(path)
    if not target.exists():
        if path or os.environ.get(ENV_CONFIG):
            raise FileNotFoundError(f"config not found: {target}")
        cfg = from_dict({})
    else:
        cfg = from_dict(json.loads(target.read_text()))
    cfg.source = target.absolute()                       # a relative --config or env path must not outlive the working directory
    return cfg


def tilde(path: Path) -> str:
    """`path` with the home directory spelled `~` (in full when it is not under home)."""
    home = Path(os.path.expanduser("~"))
    return "~/" + path.relative_to(home).as_posix() if path.is_relative_to(home) else str(path)


def with_chain_key(conf: Path, key: Path) -> str:
    """The text of config file `conf` (a JSON object, or none yet) with `"chain_key_path"` added as `key` (home spelled `~`); every other
    key keeps its place and value, 2-space indent. ValueError when the file is not an object or already says `chain_key_path`."""
    raw = json.loads(conf.read_text()) if conf.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError("the configuration file is not a JSON object")
    if "chain_key_path" in raw:
        raise ValueError("chain_key_path is already in the configuration file (null means no key, and fails closed on an install that "
                         "has a chain_heads.json beside the config or v2 rows in a chain): set it by hand")
    return json.dumps({**raw, "chain_key_path": tilde(key)}, indent=2, ensure_ascii=False) + "\n"


def name_chain_key(conf: Path, key: Path) -> None:
    """Add `"chain_key_path": key` to config file `conf`, read again now so an edit made meanwhile is kept: an atomic 0600 write to the
    file a symlink points at, its directory made 0700 if missing."""
    real = Path(os.path.realpath(conf))
    real.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic.write_private(real, with_chain_key(conf, key))
