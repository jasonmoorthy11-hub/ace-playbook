"""One mode resolver (docs/design/compiler.md 7.3, "Mode resolution"): the hook, the ladder, the offline pass and the report
all ask `resolve` what a proposal runs at, so they cannot disagree. `resolve` is pure: rows in, a `Resolution` out (only
`read_approvals` and `read_demotions` touch a file). The copy's mode counts only when the approvals chain backs it (an observe
copy needs no row); the cap is the lowest `to_mode` of the demotion rows stamped at or after the newest approvals row (an
operator approval lifts every STRICTLY older row, a tie keeps the demotion; with no approvals row every row counts); a broken
or unreadable chain caps at observe. Imports: stdlib, `.approvals`, `.chains`, `.demotions`
(the shadow hook imports this module).
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from . import approvals, chains, demotions

V2 = re.compile(rb'"v"\s*:\s*2\s*[,}]')   # a candidate `v: 2` row anywhere in a chain file (`v2_rows`)
Broken = Optional[Union[int, str]]   # None: intact; an index: broken there; "unreadable": the read failed; "head_mismatch" / "heads_missing": see chains.head_state


@dataclass(frozen=True)
class Resolution:
    mode: str                      # what the hook runs: `demotions.lower(approved_mode, cap)`
    approved_mode: str             # what the copy and the approvals chain allow
    cap: str | None                # lowest `to_mode` of the demotion rows stamped at or after the newest approval, None without
    window_start: str              # start of the counting window ("" when none is known)
    source: str                    # what set `window_start`: approval | demotion | copy | none
    problems: tuple[str, ...] = ()  # approvals:missing|mismatch|chain_broken|head_mismatch, revoked, no_copy, demotions:chain_broken|head_mismatch|unreadable


def read_demotions(data_dir, ring: chains.Keyring | None = None) -> tuple[list[dict], Broken]:
    """`demotions.read_chain`, a read error (symlink, directory, ...) reported as `unreadable` instead of raised."""
    try:
        return demotions.read_chain(data_dir, ring)
    except Exception:
        return [], "unreadable"


def read_approvals(data_dir, ring: chains.Keyring | None = None) -> tuple[list[dict], Broken]:
    """`approvals.read_chain`, a read error reported as `unreadable` instead of raised."""
    try:
        return approvals.read_chain(data_dir, ring)
    except Exception:
        return [], "unreadable"


def v2_rows(data_dir) -> bool:
    """True when `approvals.jsonl` or `demotions.jsonl` holds a shape-valid `v: 2` row: the install was keyed, whatever the config names now
    (the marker `chains.key_of` falls back to when no key or heads file is found). An unreadable chain file counts as no row. One bytes regex
    over the whole file gates the JSON parse (a hook pays this per call: parsing 1 MiB of junk lines took 438 ms); it matches the writers'
    spelling with or without spaces, so a row that hides its key behind a `\\u0076` escape is not a marker (it breaks an unkeyed chain anyway)."""
    for mod in (approvals, demotions):
        with contextlib.suppress(OSError):
            raw = chains.read_regular(Path(data_dir) / mod.FILE, mod.MAX_BYTES) or b""
            if V2.search(raw) and chains.has_v2(raw, mod.shape):
                return True
    return False


def anchor(rows: list[dict], pid: str, now: str, broken: Broken = None) -> dict | None:
    """The newest approvals row of `pid` stamped at or before `now` (a later stamp is ignored, not clamped); None when the
    chain is broken, because its rows are then not trusted to lift anything."""
    if broken is not None:
        return None
    return next((r for r in reversed(rows) if r["proposal_id"] == pid and r["ts"] <= now), None)


def lifted(row: dict, ref: dict | None) -> bool:
    """A demotion row stamped strictly before the approval `ref` is lifted by it (a tie keeps it: re-approve a second later)."""
    return ref is not None and row["ts"] < ref["ts"]


def in_force(drows: list[dict], pid: str, arows: list[dict], now: str, abroken: Broken = None) -> list[dict]:
    """The demotion rows of `pid` that no approval has lifted (those `resolve` counts toward the cap)."""
    ref = anchor(arows, pid, now, abroken)
    return [r for r in drows if r["proposal_id"] == pid and not lifted(r, ref)]


def chain_notice(broken: Broken) -> str:
    """The operator line for a demotions chain that is broken (rows counted from 1, like approvals) or unreadable; '' if intact."""
    if broken is None:
        return ""
    where = "unreadable" if broken == "unreadable" else f"broken {chains.where(broken, 1)}"
    return f"demotions.jsonl is {where}: rows from the break on are not listed (the chain keeps capping at observe until it is repaired)"


def capped(rows: list[dict], now: str) -> str:
    """The operator line for in-force rows (of one proposal) that an approval stamped `now` cannot lift."""
    first, cap = min(r["ts"] for r in rows), demotions.effective_cap(rows, rows[0]["proposal_id"])
    if demotions.epoch(first) - demotions.epoch(now) > 1:
        return f"capped at {cap} until {first} ({len(rows)} row(s) not liftable now)"
    return f"capped at {cap} by {len(rows)} row(s) not liftable now (re-run in a second)"


def _approved(pid: str, copy: dict | None, rows: list[dict], broken: Broken) -> tuple[str, list[str]]:
    """(approved mode, problems): `disabled` for a revoked proposal or no copy, `observe` when the chain cannot back the copy."""
    bad = [f"approvals:{broken if broken == 'head_mismatch' else 'chain_broken'}"] if broken is not None else []
    newest = None if broken is not None else approvals.newest(rows, pid)
    if newest is not None and newest["mode"] == "revoked":
        return "disabled", ["revoked"]
    if copy is None:
        return "disabled", ["no_copy", *bad]
    if broken is not None or copy["mode"] == "observe":
        return "observe", bad
    verdict = approvals.verify_copy(copy, rows)
    return (copy["mode"], []) if verdict == "ok" else ("observe", [f"approvals:{verdict}"])


def _window(copy: dict | None, ref: dict | None, drows: list[dict], now: str) -> tuple[str, str]:
    """(start, source): the newest approvals row, else the copy's `approved_at` (the pre-chain observe-only case), then the
    newest counter-resetting demotion (not an `evasion_pair`) after it; stamps after `now` are ignored."""
    approved = copy.get("approved_at", "") if copy else ""
    start, source = (ref["ts"], "approval") if ref else (approved, "copy") if approved and approved <= now else ("", "none")
    for r in drows:
        if r["cause"] != "evasion_pair" and start < r["ts"] <= now:
            start, source = r["ts"], "demotion"
    return start, source


def resolve(pid: str, copy_rec: dict | None, approvals_rows: list[dict], approvals_broken: Broken,
            demotion_rows: list[dict], demotions_broken: Broken, now: str) -> Resolution:
    """What proposal `pid` runs at. `copy_rec` is its approved record (None: no copy); rows are the verified prefix of each chain."""
    approved, problems = _approved(pid, copy_rec, approvals_rows, approvals_broken)
    ref = anchor(approvals_rows, pid, now, approvals_broken)
    mine = [r for r in demotion_rows if r["proposal_id"] == pid]
    cap = demotions.effective_cap(in_force(demotion_rows, pid, approvals_rows, now, approvals_broken), pid)
    if demotions_broken is not None:                  # fail toward observe, never open
        cap = demotions.lower("observe", cap)
        problems.append("demotions:" + (demotions_broken if demotions_broken in ("unreadable", "head_mismatch") else "chain_broken"))
    start, source = _window(copy_rec, ref, mine, now)
    return Resolution(demotions.lower(approved, cap), approved, cap, start, source, tuple(problems))
