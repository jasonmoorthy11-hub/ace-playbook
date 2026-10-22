"""The watcher's private store (docs/design/compiler.md 8.x "Watch (as built)"): everything lives under `watch_dir`, nothing outside it.

`proposals/W-<8 hex>.json` (0600, written once, never replaced), `decisions.jsonl` (append-only, 0600, O_APPEND, flock like
`digest.append_log`), `seen.json` and `inventory.json` (0600, atomic) and the kill file `DISABLED`. The current status of a proposal is its
newest decision row, else the file's own `status`; `expired` once past `expires` while still open or queued. At most CAP proposals are
open at once, leads excepted; the rest are `queued` and a `promoted` decision row opens them in ranking order when a slot frees.
A file that cannot be read, is too deeply nested for the JSON reader, or has the wrong shape (`_shaped`) is skipped and counted
(`scan_proposals`, `summary`: so is a proposals folder or decisions log that is a link or the wrong kind); a decision row of the wrong type is
skipped; nothing a corrupt store holds raises out of a reader. `ensure` never changes the mode of a directory that exists and refuses a
watch directory or proposals folder that is a symlink (`UnsafeDir`). Stdlib, `.chars` and `.fsio` only: no network, so `summary.py` can use it.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from pathlib import Path

from ...config import Config
from .chars import control, invisible
from .fsio import odd, write_private

CAP, KILL, PROPOSALS, DECISIONS, SEEN, INVENTORY = 5, "DISABLED", "proposals", "decisions.jsonl", "seen.json", "inventory.json"
RANK = {"model_pin": 0, "version": 1, "docs": 2}                            # every other class (leads) ranks after these
ID = re.compile(r"W-[0-9a-f]{8}")
WORDS = {"accepted": "accepted", "dismissed": "dismissed", "closed": "auto-closed", "later": "open", "promoted": "open", "reopened": "open"}
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
FINAL = ("accepted", "dismissed", "closed")
NOTE_MAX = 500


def clean(text: object, limit: int = 120) -> str:
    """`text` without control, format and line-separator characters (so no escape sequence reaches a terminal), at most `limit` long."""
    s = text if isinstance(text, str) else ""
    return "".join(c for c in s if c >= " " and not control(c) and not invisible(c) and c not in "\u2028\u2029")[:limit]


def watch_dir(cfg: Config) -> Path:
    return Path(cfg.watch_dir) if cfg.watch_dir else Path(cfg.data_dir) / "watch"


class UnsafeDir(ValueError):
    """The proposals folder is a symlink or a file in the way: nothing is written through it (the message names no path)."""


def ensure(wd: Path) -> Path:
    """Create `wd` and `wd/proposals` (0700 when new; an existing directory keeps its mode) and return `wd`; UnsafeDir for a symlinked
    watch directory or a symlinked or non-directory proposals folder."""
    wd = Path(wd)
    if not wd.is_symlink() and not wd.is_dir():
        wd.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = wd / PROPOSALS
    if wd.is_symlink() or odd(folder, True):
        raise UnsafeDir("the watch directory or its proposals folder is a symlink or not a directory")
    folder.mkdir(mode=0o700, exist_ok=True)
    return wd


def disabled(wd: Path) -> bool:
    return os.path.lexists(Path(wd) / KILL)


def _json(path: Path) -> object:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):                       # ValueError covers bad UTF-8 and bad JSON
        return None


def _shaped(rec: dict) -> bool:
    """The fields that every reader of a proposal indexes, with the types it expects (a record from `records.record` has them all)."""
    ev, ch = rec.get("evidence"), rec.get("change")
    return (all(isinstance(rec.get(k), str) for k in ("class", "created", "title", "expires", "verify"))
            and isinstance(rec.get("inventory_hits"), list) and isinstance(ev, list) and bool(ev)
            and all(isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("url", "sha256", "quote")) and "tier" in e for e in ev)
            and isinstance(ch, dict) and isinstance(ch.get("target"), str) and "diff" in ch)


def scan_proposals(wd: Path) -> tuple[list[dict], int]:
    """(every readable, well-shaped proposal whose id matches its file name, oldest first; the number of `W-*.json` files skipped)."""
    folder = Path(wd) / PROPOSALS
    out, bad = [], int(odd(folder, True))                             # a link or a file in the folder's place reads as empty and is counted
    try:
        paths = [] if bad else sorted(folder.glob("W-*.json"))
    except OSError:
        paths, bad = [], 1
    for path in paths:
        rec = _json(path)
        if ID.fullmatch(path.stem) and isinstance(rec, dict) and rec.get("id") == path.stem and _shaped(rec):
            out.append(rec)
        else:
            bad += 1
    return sorted(out, key=lambda p: (p["created"], p["id"])), bad


def load_proposals(wd: Path) -> list[dict]:
    """Every readable, well-shaped proposal whose id matches its file name, oldest first."""
    return scan_proposals(wd)[0]


def write_proposal(wd: Path, rec: dict) -> bool:
    """Write `rec` once (0600, atomic); False when that id already exists. ValueError for an id that is not `W-<8 hex>`."""
    if not isinstance(rec.get("id"), str) or not ID.fullmatch(rec["id"]):
        raise ValueError("bad proposal id")
    path = Path(wd) / PROPOSALS / (rec["id"] + ".json")
    if os.path.lexists(path):
        return False
    write_private(path, json.dumps(rec, indent=1, sort_keys=True) + "\n")
    return True


def append_decision(wd: Path, pid: str, decision: str, note: str, now: str) -> dict:
    """Append one `{ts, id, decision, note}` row (0600, O_APPEND, no-follow, locked); ValueError for an unknown id or decision."""
    if not ID.fullmatch(pid) or decision not in WORDS:
        raise ValueError("bad decision")
    row = {"ts": now, "id": pid, "decision": decision, "note": clean(note, NOTE_MAX)}
    with os.fdopen(os.open(Path(wd) / DECISIONS, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600), "ab") as fh:
        fd = fh.fileno()
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        size = os.fstat(fd).st_size
        lead = b"\n" if size and os.pread(fd, 1, size - 1) != b"\n" else b""            # a cut last line
        fh.write(lead + json.dumps(row, sort_keys=True).encode() + b"\n")
        fh.flush()
        os.fsync(fd)
    return row


def read_decisions(wd: Path) -> list[dict]:
    """The readable decision rows in file order (a cut or foreign line is skipped)."""
    path = Path(wd) / DECISIONS
    try:
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() and not path.is_symlink() else ""
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(row, dict) and ID.fullmatch(str(row.get("id"))) and isinstance(row.get("decision"), str) and row["decision"] in WORDS:
            out.append(row)
    return out


def status_of(p: dict, decisions: list[dict], now: str) -> str:
    """open | queued | accepted | dismissed | auto-closed | expired for proposal `p` at `now`."""
    mine = [d for d in decisions if d.get("id") == p.get("id")]
    if mine and mine[-1]["decision"] in FINAL:
        return WORDS[mine[-1]["decision"]]
    base = "open" if mine else p.get("status") if p.get("status") in ("open", "queued") else "open"
    exp = p.get("expires")
    renewed = [d.get("note") for d in mine if d["decision"] == "reopened" and STAMP.fullmatch(str(d.get("note")))]
    exp = renewed[-1] if renewed else exp                                    # a `reopened` row carries the new expiry in its note
    return "expired" if isinstance(exp, str) and exp < now else base


def statuses(wd: Path, now: str) -> list[tuple[dict, str]]:
    decisions = read_decisions(wd)
    return [(p, status_of(p, decisions, now)) for p in load_proposals(wd)]


def rank_key(p: dict) -> tuple:
    return (RANK.get(p.get("class") if isinstance(p.get("class"), str) else "", 9), str(p.get("created")), str(p.get("id")))


def open_count(rows: list[tuple[dict, str]]) -> int:
    """Open proposals that count against the cap (leads do not)."""
    return sum(1 for p, s in rows if s == "open" and p.get("class") != "lead")


def rebalance(wd: Path, now: str) -> list[str]:
    """Promote queued proposals, in ranking order, while fewer than CAP are open; returns the promoted ids."""
    rows = statuses(wd, now)
    n, promoted = open_count(rows), []
    for p in sorted((p for p, s in rows if s == "queued"), key=rank_key):
        if n >= CAP:
            break
        append_decision(wd, p["id"], "promoted", "slot freed", now)
        promoted.append(p["id"])
        n += 1
    return promoted


def read_seen(wd: Path) -> dict:
    seen = _json(Path(wd) / SEEN)
    return seen if isinstance(seen, dict) else {}


def save_seen(wd: Path, seen: dict) -> None:
    write_private(Path(wd) / SEEN, json.dumps(seen, indent=1, sort_keys=True) + "\n")
