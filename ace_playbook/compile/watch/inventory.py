"""The inventory snapshot (design 4.2): what this setup actually pins, built as the operator from the live files.

The private map file (`watch_inventory_map`) names the targets: `{"targets": {key: {"path": "~/...", "pattern": regex}}, "docs":
{docs page url: [target key, ...]}, "lead_keywords": [...]}`. For each target the snapshot keeps ONLY the lines that match (with their
line numbers) and the sha256 of each file, never a whole file; a file that is not there is `{"missing": true}`. At most `MAX_FILES` files
per target are read (sorted by path); a target with more is marked `truncated` and `pin` makes a lead, never a diff, for it.
`claude --version` is run (no shell, 10 s) only when `claude` is on PATH. `inventory.json` is 0600 in `watch_dir`. Imports `subprocess`, so it
is off the hook path.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .fsio import write_private
from ...canon import now_iso
from .store import INVENTORY, clean

MAX_BYTES, MAX_LINES, MAX_LINE, MAX_FILES = 4 * 1024 * 1024, 100, 300, 50
KEY = re.compile(r"[A-Za-z0-9_.:-]{1,60}")


def load_map(path: Path) -> dict:
    """The validated map `{targets: {key: {path, pattern}}, docs: {url: [key]}, lead_keywords: [str]}`; ValueError (one line) if bad."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("watch_inventory_map is not readable JSON") from None
    targets, docs, words = (raw.get(k) if isinstance(raw, dict) else None for k in ("targets", "docs", "lead_keywords"))
    docs, words = {} if docs is None else docs, [] if words is None else words
    ok = isinstance(targets, dict) and isinstance(docs, dict) and isinstance(words, list) and all(isinstance(w, str) and w for w in words)
    ok = ok and all(KEY.fullmatch(k) and isinstance(t, dict) and isinstance(t.get("path"), str) and isinstance(t.get("pattern"), str)
                    for k, t in (targets.items() if ok else ()))
    ok = ok and all(isinstance(u, str) and isinstance(v, list) and all(isinstance(k, str) and k in targets for k in v) for u, v in docs.items())
    if not ok:
        raise ValueError("watch_inventory_map has a bad shape (targets, docs, lead_keywords)")
    try:
        for t in targets.values():
            re.compile(t["pattern"])
    except re.error:
        raise ValueError("watch_inventory_map has a bad pattern") from None
    return {"targets": targets, "docs": docs, "lead_keywords": words}


def _display(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def _scan(path: str, rx: re.Pattern) -> dict:
    """The matching lines of one file, numbered like `grep -n` (lines end at b"\\n" only, so a form feed or U+2028 does not shift them; a
    trailing carriage return stays in the text). `truncated` is true when more than MAX_LINES lines matched."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return {"too_large": True}
        data = Path(path).read_bytes()
    except OSError:
        return {"missing": True}
    hits = [[n, ln.decode("utf-8", "replace")[:MAX_LINE]] for n, ln in enumerate(data.split(b"\n"), 1) if rx.search(ln.decode("utf-8", "replace"))]
    out = {"sha256": hashlib.sha256(data).hexdigest(), "lines": hits[:MAX_LINES]}
    return {**out, "truncated": True} if len(hits) > MAX_LINES else out


def claude_version(which=shutil.which, run=subprocess.run) -> str | None:
    """The first line of `claude --version`, or None when `claude` is not on PATH or the call fails."""
    exe = which("claude")
    if not exe:
        return None
    try:
        out = run([exe, "--version"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    first = clean(out.stdout.strip().splitlines()[0], 80) if out.returncode == 0 and out.stdout.strip() else ""
    return first or None


def build(mapping: dict, now: str | None = None, which=shutil.which, run=subprocess.run) -> dict:
    """The snapshot `{generated_at, claude_version, targets: {key: {files: {path: {sha256, lines} | {missing: true}}, truncated?: true}}}`."""
    targets = {}
    for key, t in sorted(mapping["targets"].items()):
        rx, pattern = re.compile(t["pattern"]), os.path.expanduser(t["path"])
        found = sorted(p for p in glob.glob(pattern) if os.path.isfile(p))
        targets[key] = {"files": {_display(p): _scan(p, rx) for p in found[:MAX_FILES]} or {_display(pattern): {"missing": True}}}
        if len(found) > MAX_FILES:
            targets[key]["truncated"] = True
    return {"generated_at": now or now_iso(), "claude_version": claude_version(which, run), "targets": targets}


def write(wd: Path, inv: dict) -> None:
    write_private(Path(wd) / INVENTORY, json.dumps(inv, indent=1, sort_keys=True) + "\n")
