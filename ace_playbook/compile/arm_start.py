"""`python -m ace_playbook.compile.arm_start <detector> [--dry-run] [--at <UTC stamp>]`: start a detector's arm phase (PREREGISTRATION.md, Arm
assignment and Pre-arm-start conditions (2) and (6); the Amendments entry "arm_start hash list" makes the hash list THIRTEEN files).

Refuses (exit 1, one line, never a value) an unknown detector, an unsealed pre-registration, a stamp earlier than `sealed_at` or than the moment
the marker is written (`--at` is now or later), a detector that already has an `arm_start` line, unset config seeds, `holdout_enabled` true, an
approvals chain that is unreadable, broken or empty, a family that `arms_window.family` (the hashed analysis's own function) rejects (no approved
proposal at `warn`/`block`, one at `observe`, a lowering demotion in force, a broken demotions chain) and uncommitted changes to the thirteen
files (a working-tree check, not a proof of the commit). It prints the dated entry, asks y/N (`--dry-run` stops there), stamps the NEXT whole
second after the `y`, appends the entry, writes the one-line file the OpenTimestamps proof stamps and the signed phase marker (phase.py; written
nowhere else), and if those writes fail or finish after the stamp it RESTORES the file, the one-line file and the old marker and refuses.
Sessions starting in the stamp's second before the write lands are eligible yet ran with output in both arms (bounded by the write latency).
Then it prints the operator lines (commit, signed tag, push of the current branch, the line's sha256, `ots stamp`, `arms report`); off the hook path.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import textwrap
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from .. import config as cfgmod
from ..atomic import write_private
from ..canon import now_iso
from . import approvals, arms_window, catalogue, chains, phase, seal

REPO = seal.REPO
C = "ace_playbook/compile/"                                     # the thirteen files, by path from the repository root (the printed sha256 lines)
FILES = tuple(C + f for f in ("shadow.py", "offline.py", "events.py", "demotions.py", "modes.py", "approvals.py", "shadow_arm.py",
                              "shadow_guard.py", "phase.py", "offline_demote.py", "chains.py")) + ("ace_playbook/reflect/runner.py", "ace_playbook/sweep.py")


class Refused(Exception):
    """The one-line reason a start is refused (never a seed or a key)."""


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m ace_playbook.compile.arm_start", description=__doc__.split("\n\n")[0])
    ap.add_argument("detector", help="the detector whose arm phase starts")
    ap.add_argument("--dry-run", action="store_true", help="print the entry and write nothing")
    ap.add_argument("--at", metavar="STAMP", help="the arm start, YYYY-MM-DDTHH:MM:SSZ, now or later (default: now, taken after the y)")
    ap.add_argument("--config")
    return ap


def _vcs(*args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise Refused("git could not be run in the repository") from None
    if out.returncode:
        raise Refused(f"git {args[0]} failed in the repository")
    return out.stdout


def _tree() -> tuple[dict[str, str], str]:
    """({path: sha256 of its bytes}, HEAD): refuses uncommitted changes to the thirteen files and a file that cannot be read."""
    if dirty := _vcs("status", "--porcelain", "--", *FILES).splitlines():
        raise Refused("uncommitted changes to the hashed files (commit them first): " + ", ".join(ln[3:].rsplit("/", 1)[-1] for ln in dirty))
    try:
        return {f: hashlib.sha256((REPO / f).read_bytes()).hexdigest() for f in FILES}, _vcs("rev-parse", "HEAD").strip()
    except OSError:
        raise Refused("a hashed file cannot be read") from None


def _check(cfg, det: str, stamp: str, text: str, explicit: bool) -> tuple[str, list[str]]:
    """(approvals head, family ids), or Refused. The window and family rules are the hashed analysis's own (`arms_window`)."""
    if det not in catalogue.CATALOGUE:
        raise Refused("unknown detector (not in the catalogue)")
    head, sealed = seal._HEAD.search(text), arms_window.SEALED.search(text)
    if not (head and head.group(1).startswith("SEALED") and sealed and arms_window.AMENDMENTS.search(text)):
        raise Refused("the pre-registration is not sealed (no SEALED heading, sealed_at stamp and Amendments heading)")
    if (at := arms_window._dt(stamp)) is None:
        raise Refused("--at must be a UTC stamp YYYY-MM-DDTHH:MM:SSZ")
    if at < arms_window._dt(sealed.group(1)):
        raise Refused(f"the stamp is earlier than sealed_at ({sealed.group(1)})")
    if explicit and at < arms_window._dt(now_iso()):
        raise Refused("--at has already passed (sessions since then ran with output in both arms): use now or a later stamp")
    if det in arms_window.arm_starts(text):
        raise Refused(f"{det} already started (an arm_start line exists)")
    if missing := seal.unset(cfg):
        raise Refused(f"the config field(s) {', '.join(missing)} are unset")
    if getattr(cfg, "holdout_enabled", False):
        raise Refused("holdout_enabled is true (it stays false during any arm phase): turn it off first")
    try:
        rows, broken = approvals.read_chain(cfg.data_dir, chains.key_of(cfg))
    except (OSError, ValueError):
        raise Refused("the approvals chain is unreadable") from None
    if broken is not None:
        raise Refused(f"the approvals chain is broken {chains.where(broken)}")
    if not rows:
        raise Refused("the approvals chain is empty")
    try:
        ids = arms_window.family(cfg, {det: stamp}, {det: rows[-1]["hash"]}).get(det, [])
    except ValueError as exc:
        raise Refused(str(exc)) from None
    if not ids:
        raise Refused(f"no proposal of {det} is approved at warn or block at {stamp}")
    return rows[-1]["hash"], ids


def entry(det: str, stamp: str, head: str, hashes: dict[str, str], ids: list[str], tree: str) -> tuple[str, str]:
    """(the Amendments entry, its arm_start line); the line starts at column 0 so `arms_window._lines` finds it."""
    line = f"arm_start {det} {stamp} approvals={head}"
    fam = [f"family {chunk}" for chunk in textwrap.wrap(" ".join(ids), 140)]
    body = [f"### {stamp[:10]} - arm start: {det}", "", line, *(f"sha256 {f} {h}" for f, h in hashes.items()), *fam, f"tree {tree}",
            f"phase marker arm_phase.json {det} {stamp} (signed with the chain key, written by compile.arm_start only)"]
    return "\n".join(body) + "\n", line


def append(text: str, ent: str) -> str:
    """`text` with `ent` added at the end of the top-level Amendments section (the next top-level heading, or the end of the file)."""
    top = arms_window.AMENDMENTS.search(text)
    end = text.find("\n## ", top.end())
    end = len(text) if end < 0 else end
    return text[:end].rstrip("\n") + "\n\n" + ent + text[end:]


def _plan(cfg, det: str, stamp: str, explicit: bool, text: str) -> tuple[str, str, str]:
    """(entry, arm_start line, the new file text) for a start at `stamp`, or Refused; the entry must be read back by the analysis."""
    head, ids = _check(cfg, det, stamp, text, explicit)
    hashes, tree = _tree()
    ent, line = entry(det, stamp, head, hashes, ids, tree)
    new = append(text, ent)
    starts = arms_window.arm_starts(new)
    if starts.get(det) != stamp or arms_window.arm_heads(new).get(det) != head:
        raise Refused("the entry would not be read back as an arm_start line (the Amendments section is malformed)")
    try:
        arms_window.check_starts(new, starts)
    except ValueError as exc:
        raise Refused(str(exc)) from None
    return ent, line, new


def _fail(code: int, msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return code


def run(cfg, args: argparse.Namespace, ask: Callable[[str], str], need_tty: Callable[[str], bool]) -> int:
    det, explicit = args.detector, bool(args.at)
    try:
        text = seal.PREREG.read_text(encoding="utf-8")
        ent, line, new = _plan(cfg, det, args.at or now_iso(), explicit, text)
        branch = _vcs("rev-parse", "--abbrev-ref", "HEAD").strip()
    except (Refused, OSError) as exc:
        return _fail(1, str(exc) if isinstance(exc, Refused) else "the pre-registration cannot be read")
    print(ent, end="")
    if args.dry_run:
        return 0
    if not need_tty("arm_start"):
        return 2
    if ask(f"Start the {det} arm phase at {args.at or 'the moment you answer'} with the entry above? [y/N] ").lower() not in ("y", "yes"):
        return _fail(1, "not started")
    if seal.PREREG.read_text(encoding="utf-8") != text:
        return _fail(1, "the pre-registration file changed while the prompt was open; nothing written")
    stamp = args.at or arms_window._fmt(arms_window._dt(now_iso()) + timedelta(seconds=1))      # the next whole second after the y
    try:
        again = _plan(cfg, det, stamp, explicit, text)
    except Refused as exc:
        return _fail(1, str(exc))
    if again[0] != ent:
        ent, line, new = again
        print(ent, end="")
    one, mark = Path(cfg.data_dir) / f"arm_start_{det}.txt", Path(cfg.data_dir) / phase.FILE
    old = mark.read_bytes() if mark.is_file() else None
    seal._write(seal.PREREG, new)
    try:
        write_private(one, line)                                 # no trailing newline: its sha256 is the line's (`printf '%s' | shasum`)
        phase.write(cfg, {det: stamp}, now_iso())
        why = "the writes finished after the stamp" if now_iso() > stamp else ""
    except (OSError, ValueError):
        why = "the marker write failed"
    if why:                                                      # restore everything: the entry, the one-line file and the previous marker
        seal._write(seal.PREREG, text)
        one.unlink(missing_ok=True)
        write_private(mark, old.decode("utf-8")) if old is not None else mark.unlink(missing_ok=True)
        return _fail(1, f"{why}: PREREGISTRATION.md, the one-line file and the marker are restored, nothing is written; fix the cause and rerun")
    vcs = f"git -C {REPO}"
    print(f'{vcs} add PREREGISTRATION.md && {vcs} commit -m "arm start: {det} {stamp}"\n{vcs} tag -s arm-{det} -m "arm start {det} {stamp}"\n'
          f"{vcs} push origin {branch} arm-{det}\nprintf '%s' '{line}' | shasum -a 256\nots stamp {one}\npython3 -m ace_playbook.compile.arms report")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        cfg = cfgmod.load(args.config)
    except (OSError, ValueError):
        return _fail(2, "the config cannot be loaded")
    from . import __main__ as cli                              # the shared terminal helpers; tests monkeypatch `cli._ask`
    return run(cfg, args, lambda prompt: cli._ask(prompt), cli._need_tty)


if __name__ == "__main__":
    sys.exit(main())
