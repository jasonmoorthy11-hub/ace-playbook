"""`compile seal`: freeze the pre-registration's shadow-mode instruments (docs/design/compiler.md section 8, "Seal").

`--dry-run` prints the Frozen instruments block with live values and writes nothing. Without it the command needs a terminal,
checks the file (one section heading, not sealed, one filler line in the Frozen instruments subsection, the entry gate over sessions and
a horizon line in the Timing and stopping subsection, readable chain, both seed hashes and an arm rate),
asks y/N, rewrites the section heading of PREREGISTRATION.md to SEALED, fills the block in and prints the two git
commands the operator runs; it never runs git. Not on the hook path: stdlib and sibling modules only, nothing is imported by the shadow hook.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
from pathlib import Path
from typing import Callable

from ..canon import now_iso
from . import approvals, catalogue, chains, measure, report

REPO = Path(__file__).resolve().parents[2]
PREREG = REPO / "PREREGISTRATION.md"                         # tests point this at a copy
TAG, FILLER, HAND, SEED = "prereg-v1", "(filled by compile seal)", "(filled by hand before the seal)", "shadow_seed_sha256"
PERM, MIN_GATE = "permutation_seed_sha256", 30
_HEAD = re.compile(r"^## Shadow-mode additions \((DRAFT until sealed|SEALED[^)\n]*)\)$", re.M)
_COUNT = re.compile(r"^ {0,3}##\s+Shadow-mode additions", re.M)           # looser than _HEAD: an indented or double-spaced heading counts
_GATE = re.compile(r"at least\s+(\d+)\s+human-labelled\s+sessions-with-fire[^.]*?\b0 FP\b[^.]*?\b0 unlabelled\b")   # the entry gate counts sessions
_HORIZON = re.compile(r"Horizon[^.\n]*?:\s*(\d+) calendar\s+weeks[^.]*?(\d+) eligible\s+sessions")      # weeks and eligible sessions, both numbers


def _sha(seed: str | None) -> str:
    """sha256 of a private seed, or `-` when it is not set. The seed itself is never printed or returned."""
    return hashlib.sha256(seed.encode()).hexdigest() if seed else "-"


def unset(cfg) -> list[str]:
    """The arm fields (config keys) the seal still lacks, in block order."""
    return [k for k in ("shadow_seed", "permutation_seed", "arm_rate") if getattr(cfg, k) is None]


def _timing(text: str, tm: int, tend: int) -> str | None:
    """Why the Timing and stopping subsection cannot be sealed, or None: exactly one entry gate over sessions (at least MIN_GATE) and
    exactly one horizon line, and no hand-fill placeholder."""
    sec = text[tm:tend]
    gate, horizon = _GATE.findall(sec), _HORIZON.findall(sec)
    if len(gate) != 1 or int(gate[0]) < MIN_GATE:
        return f"the Timing and stopping subsection must state the entry gate once: at least {MIN_GATE} human-labelled sessions-with-fire, 0 FP, 0 unlabelled"
    if len(horizon) != 1 or not all(int(n) > 0 for n in horizon[0]):
        return "the Timing and stopping subsection must hold exactly one 'Horizon ...: <weeks> calendar weeks ... <n> eligible sessions' line"
    return None


def _proposals(cfg) -> tuple[list[str], str, object]:
    """(lines, measurement_start, broken): the newest row (in chain order) of each proposal id that is not revoked, sorted by id, as
    `id mode proposal_sha256`, and `measure.start_of` the rows (the ladder's restart point, shared with `status`); `-` when none qualifies."""
    try:
        rows, broken = approvals.read_chain(cfg.data_dir, chains.key_of(cfg))
    except (ValueError, OSError):
        rows, broken = [], "unreadable"
    newest = {pid: approvals.newest(rows, pid) for pid in {r["proposal_id"] for r in rows}}
    listed = {pid: r for pid, r in newest.items() if r["mode"] != "revoked"}
    return ([f"  {pid} {r['mode']} {r['proposal_sha256']}" for pid, r in sorted(listed.items())],
            measure.start_of(rows) or "-", broken)


def _notice(broken: object) -> list[str]:
    if broken is None:
        return []
    return ["  (approvals chain unreadable)" if broken == "unreadable" else f"  (approvals chain broken {chains.where(broken)})"]


def frozen_block(cfg, sealed_at: str = "-") -> str:
    """The Frozen instruments block: detector strengths and hashes, report hash, approved proposals with their hashes, both seed hashes,
    arm rate, start, seal time."""
    lines, start, broken = _proposals(cfg)
    out = ["detectors:", *(f"  {n} {catalogue.CATALOGUE[n].strength} {sha}" for n, sha in sorted(catalogue.source_table().items())),
           f"report_sha256 {report.source_sha256()}", "proposals:", *_notice(broken), *(lines or ([] if broken else ["  (none)"])),
           f"{SEED} {_sha(cfg.shadow_seed)}", f"{PERM} {_sha(cfg.permutation_seed)}", f"arm_rate {'-' if cfg.arm_rate is None else cfg.arm_rate}",
           f"measurement_start {start}", f"sealed_at {sealed_at}"]
    return "\n".join(out) + "\n"


def _write(path: Path, text: str) -> None:
    """Fresh temp file beside `path` (mkstemp), same mode, fsynced, then `os.replace`; the temp file is removed on failure."""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _fail(code: int, msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return code


def run(cfg, args: argparse.Namespace, ask: Callable[[str], str], need_tty: Callable[[str], bool]) -> int:
    if args.dry_run:
        print(frozen_block(cfg), end="")
        return 0
    if not need_tty("seal"):
        return 2
    text = PREREG.read_text(encoding="utf-8")
    heads = list(_HEAD.finditer(text))
    if len(heads) != 1 or len(_COUNT.findall(text)) != 1:
        return _fail(2, "the pre-registration must hold exactly one '## Shadow-mode additions' heading")
    head = heads[0]
    if head.group(1).startswith("SEALED"):
        return _fail(1, "the pre-registration is already sealed; changes go in dated amendments")
    end = text.find("\n## ", head.end())
    end = len(text) if end < 0 else end                          # the shadow-mode section: this heading to the next top-level one
    fz = text.find("\n### Frozen instruments\n", head.end(), end)
    fend = text.find("\n### ", fz + 1, end) if fz >= 0 else -1
    fend = end if fend < 0 else fend
    if fz < 0 or text.count("\n" + FILLER + "\n", fz, fend) != 1:
        return _fail(2, f"the Frozen instruments subsection must hold exactly one '{FILLER}' line")
    tm = text.find("\n### Timing and stopping\n", head.end(), end)
    tend = text.find("\n### ", tm + 1, end) if tm >= 0 else -1
    why = "the Timing and stopping subsection is missing" if tm < 0 else _timing(text, tm, end if tend < 0 else tend)
    if HAND in text[head.end():end] or why:
        return _fail(1, why or f"the placeholder '{HAND}' is still in the section")
    if (broken := _proposals(cfg)[2]) is not None:
        return _fail(1, f"approvals.jsonl chain {'unreadable' if broken == 'unreadable' else 'broken ' + chains.where(broken)}; "
                        "repair it before sealing")
    stamp = now_iso()
    block = frozen_block(cfg, stamp)
    print(block, end="")
    if missing := unset(cfg):
        return _fail(1, f"the seal refuses until the {', '.join(missing)} config field(s) are set (the block records only the sha256 of a seed)")
    if ask(f"Seal the shadow-mode section as of {stamp[:10]} with the block above? [y/N] ").lower() not in ("y", "yes"):
        return _fail(1, "not sealed")
    if PREREG.read_text(encoding="utf-8") != text:
        return _fail(1, "the pre-registration file changed while the prompt was open; nothing written")
    day, at = stamp[:10], text.index("\n" + FILLER + "\n", fz, fend) + 1
    new = text[:at] + f"```text\n{block}```\n" + text[at + len(FILLER) + 1:]
    _write(PREREG, new[:head.start()] + f"## Shadow-mode additions (SEALED {day} {TAG})" + new[head.end():])
    print(f'git -C {REPO} tag -a {TAG} -m "pre-registration sealed {day}"\ngit -C {REPO} push origin {TAG}')
    return 0
