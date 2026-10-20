"""`compile seal`: freeze the pre-registration's shadow-mode instruments (docs/design/compiler.md section 8, "Seal").

`--dry-run` prints the Frozen instruments block with live values and writes nothing. Without it the command needs a terminal,
asks y/N, rewrites the section heading of PREREGISTRATION.md to SEALED, fills the block in and prints the two git commands the
operator runs; it never runs git. Not on the hook path: stdlib and sibling modules only, nothing is imported by the shadow hook.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Callable

from ..canon import now_iso
from . import approvals, catalogue, chains, report

REPO = Path(__file__).resolve().parents[2]
PREREG = REPO / "PREREGISTRATION.md"                         # tests point this at a copy
TAG, FILLER = "prereg-v1", "(filled by compile seal)"
_HEAD = re.compile(r"^## Shadow-mode additions \((DRAFT until sealed|SEALED[^)\n]*)\)$", re.M)


def _proposals(cfg) -> tuple[list[str], str, object]:
    """(lines, measurement_start, broken): the newest row (in chain order) of each proposal id that is not revoked, sorted by id, and the
    greatest ts among those listed rows; `-` when none is listed."""
    try:
        rows, broken = approvals.read_chain(cfg.data_dir, chains.key_of(cfg))
    except (ValueError, OSError):
        rows, broken = [], "unreadable"
    newest = {pid: approvals.newest(rows, pid) for pid in {r["proposal_id"] for r in rows}}
    listed = {pid: r for pid, r in newest.items() if r["mode"] != "revoked"}
    return [f"  {pid} {r['mode']}" for pid, r in sorted(listed.items())], max((r["ts"] for r in listed.values()), default="-"), broken


def _notice(broken: object) -> list[str]:
    if broken is None:
        return []
    return ["  (approvals chain unreadable)" if broken == "unreadable" else f"  (approvals chain broken {chains.where(broken)})"]


def frozen_block(cfg, sealed_at: str = "-") -> str:
    """The Frozen instruments block: detector hashes, report hash, approved proposals, seed hash, arm rate, start, seal time."""
    lines, start, broken = _proposals(cfg)
    out = ["detectors:", *(f"  {n} {sha}" for n, sha in sorted(catalogue.source_table().items())),
           f"report_sha256 {report.source_sha256()}", "proposals:", *_notice(broken), *(lines or ([] if broken else ["  (none)"])),
           "shadow_seed_sha256 -", "arm_rate 0.5 (planned)", f"measurement_start {start}", f"sealed_at {sealed_at}"]
    return "\n".join(out) + "\n"


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def run(cfg, args: argparse.Namespace, ask: Callable[[str], str], need_tty: Callable[[str], bool]) -> int:
    if args.dry_run:
        print(frozen_block(cfg), end="")
        return 0
    if not need_tty("seal"):
        return 2
    text = PREREG.read_text(encoding="utf-8")
    if (head := _HEAD.search(text)) is None:
        print("error: the pre-registration has no shadow-mode section", file=sys.stderr)
        return 2
    if head.group(1).startswith("SEALED"):
        print("error: the pre-registration is already sealed; changes go in dated amendments", file=sys.stderr)
        return 1
    if text.count(FILLER + "\n") != 1:
        print(f"error: the Frozen instruments section must hold exactly one '{FILLER}' line", file=sys.stderr)
        return 2
    if (broken := _proposals(cfg)[2]) is not None:
        print(f"error: approvals.jsonl chain {'unreadable' if broken == 'unreadable' else 'broken ' + chains.where(broken)}; "
              "repair it before sealing", file=sys.stderr)
        return 1
    stamp = now_iso()
    block = frozen_block(cfg, stamp)
    print(block, end="")
    note = " (shadow_seed_sha256 is still '-')" * ("shadow_seed_sha256 -\n" in block)
    if ask(f"Seal the shadow-mode section as of {stamp[:10]} with the block above{note}? [y/N] ").lower() not in ("y", "yes"):
        print("error: not sealed", file=sys.stderr)
        return 1
    day = stamp[:10]
    text = text.replace(head.group(0), f"## Shadow-mode additions (SEALED {day} {TAG})", 1)
    _write(PREREG, text.replace(FILLER + "\n", f"```text\n{block}```\n", 1))
    print(f'git -C {REPO} tag -a {TAG} -m "pre-registration sealed {day}"\ngit -C {REPO} push origin {TAG}')
    return 0
