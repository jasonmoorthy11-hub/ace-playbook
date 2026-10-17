"""Project key resolution: git common dir -> basename (normalized), else cwd basename.

Using the git *common* dir means worktrees and subdirectories of one repo share a key.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


def normalize(name: str, fold_separators: bool = False) -> str:
    """Lowercase, map anything outside [a-z0-9._-] to '-'. With fold_separators the
    separators are dropped too, so AcmeWidgets and acme-widgets collide."""
    key = re.sub(r"[^a-z0-9._\-]+", "-", name.strip().lower()).strip("-")
    if fold_separators:
        key = re.sub(r"[_\-]+", "", key)
    return key or "unknown"


def git_common_root(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    if not out:
        return None
    common = Path(out)
    return common.parent if common.name == ".git" else common.with_suffix("")


def load_engagements(path: Path | None) -> dict:
    if not path or not Path(path).exists():
        return {"aliases": {}, "paths": {}}
    raw = json.loads(Path(path).read_text())
    return {"aliases": raw.get("aliases", {}), "paths": raw.get("paths", {})}


def resolve_key(cwd: str | Path, engagements_path: Path | None = None,
                fold_separators: bool = False) -> str:
    """Return the project key for `cwd`.

    engagements.json: {"aliases": {"alt-name": "canonical"}, "paths": {"<dir prefix>": "key"}}.
    A path prefix match wins over the git-derived name; aliases apply last.
    """
    cwd = Path(cwd).resolve()
    eng = load_engagements(engagements_path)
    for prefix, key in sorted(eng["paths"].items(), key=lambda kv: -len(kv[0])):
        p = Path(prefix).expanduser()
        if cwd == p or p in cwd.parents:
            return normalize(key, fold_separators)
    root = git_common_root(cwd) if cwd.exists() else None
    key = normalize((root or cwd).name, fold_separators)
    aliases = {normalize(k, fold_separators): normalize(v, fold_separators)
               for k, v in eng["aliases"].items()}
    return aliases.get(key, key)
