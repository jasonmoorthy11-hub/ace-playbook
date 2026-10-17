"""Repo scan used as a pre-commit check and in CI (`python -m ace_playbook.scrub check`)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .engine import REPO, Hit, line_of, scan, try_decode

DEFAULT_ALLOW = ("ace_playbook/fixtures/frozen/",)
ALLOW_FILE = ".ace-scrub-allow"
SKIP_NAMES = {"uv.lock"}
SKIP_SUFFIXES = {".png", ".jpg", ".gif", ".pdf", ".sqlite", ".zip", ".gz"}
MAX_BYTES = 1_000_000
FAKE_MARKER = "FAKE"


@dataclass
class Finding:
    path: str
    line: int
    hit: Hit
    allowlisted: bool

    def render(self) -> str:
        h = self.hit.public()
        tag = " [allowlisted fixture]" if self.allowlisted else ""
        return f"{self.path}:{self.line}: {h['rule']} ({h['layer']}) len={h['len']} fp={h['fp']}{tag}"


def load_allow(root: Path) -> tuple[str, ...]:
    f = root / ALLOW_FILE
    extra = ()
    if f.exists():
        extra = tuple(
            ln.strip() for ln in f.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        )
    return DEFAULT_ALLOW + extra


def list_files(root: Path, staged: bool) -> list[str]:
    cmd = (["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
           if staged else ["git", "ls-files", "--cached", "--others", "--exclude-standard"])
    try:
        out = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=True).stdout
        return [ln for ln in out.splitlines() if ln]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [str(p.relative_to(root)) for p in root.rglob("*")
                if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts]


def staged_blob(root: Path, rel: str) -> bytes | None:
    """The staged (index) content of `rel`, not whatever is in the working tree."""
    try:
        return subprocess.run(["git", "show", f":{rel}"], cwd=root, capture_output=True,
                              check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _readable(path: Path, staged_root: Path | None = None) -> str | None:
    """File text, or None if skipped. With `staged_root`, read the staged blob via git."""
    if path.name in SKIP_NAMES or path.suffix in SKIP_SUFFIXES:
        return None
    if staged_root is not None:
        data = staged_blob(staged_root, str(path.relative_to(staged_root)))
        if data is None or len(data) > MAX_BYTES:
            return None
    else:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def has_fake_marker(match: str) -> bool:
    """True if the matched text, or its base64 decoding (up to depth 2), carries FAKE."""
    text = match
    for _ in range(3):
        if FAKE_MARKER in text:
            return True
        decoded = try_decode(text)
        if decoded is None:
            return False
        text = decoded
    return False


def scan_files(
    root: Path,
    files: list[str],
    mode: str,
    allow: tuple[str, ...],
    deny_terms: tuple[str, ...] = (),
    staged: bool = False,
) -> list[Finding]:
    """Findings for `files`.

    staged: scan the index blob of each file, not the working-tree copy.
    audit: every hit is reported, the allowlist is ignored.
    pre-commit: hits under an allowlisted prefix are tolerated only when the
    matched text (or its base64 decoding) contains the FAKE marker; everything else is reported.
    """
    findings: list[Finding] = []
    for rel in files:
        text = _readable(root / rel, root if staged else None)
        if text is None:
            continue
        allowed = rel.startswith(allow)
        for hit in scan(text, REPO, deny_terms):
            tolerated = allowed and has_fake_marker(hit.match)
            if mode == "pre-commit" and tolerated:
                continue
            findings.append(Finding(rel, line_of(text, hit.start), hit, allowed))
    return findings
