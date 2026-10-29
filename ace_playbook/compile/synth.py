"""`compile replay-synthetic` (docs/design/compiler.md 8, step 5c): the reproducible synthetic corpus through the shadow evaluator.

Feeds every `fire` and `no_fire` payload of `fixtures/compile/examples.json` (one session per detector) and the frozen sessions of
`fixtures/frozen/shadow_corpus.json` through `shadow._evaluate` with a temporary state directory, and writes one row per event, `corpus:
"synthetic"`, to `shadow.synthetic.jsonl` (replaced on every run, `tier: "synthetic"`; never the private `shadow.jsonl`: `--out` is confined by `target`). A row that did not fire has
`would_fire: false`; `compile report` counts only the fires but needs the rest as the corpus's eligible sessions. The corpus is a regression
test: exit 1 when any `expect` disagrees with the outcome, or a detector errors; exit 2 for an unusable corpus. Detector names and tools are
checked against `catalogue.CATALOGUE`, never hard-coded. Prints counts only. Not imported by shadow.py: it is off the hook path.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

from ..atomic import write_private
from . import catalogue, labels, shadow
from .events import event_from_hook

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
EXAMPLES, CORPUS, OUT = FIXTURES / "compile" / "examples.json", FIXTURES / "frozen" / "shadow_corpus.json", "shadow.synthetic.jsonl"
EXPECT, MAX_OUT = ("fire", "no_fire"), 1 << 24
NOT_FILE = "--out is a symlink or not a regular file"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise labels.Refusal(f"cannot read {path.name}", 2) from None
    if not isinstance(data, dict) or data.get("v", 1) != 1:
        raise labels.Refusal(f"{path.name} is not a v1 object", 2)
    return data


def _check(det: object, expect: object, payload: object) -> tuple[str, str, dict]:
    """(detector, expect, payload) once the detector is in the catalogue and consumes the payload's tool, else a Refusal."""
    row = catalogue.CATALOGUE.get(det) if isinstance(det, str) else None
    tool = payload.get("tool_name") if isinstance(payload, dict) else None
    if row is None or expect not in EXPECT or not isinstance(tool, str) or not isinstance(payload.get("tool_input"), dict):
        raise labels.Refusal("a corpus event has an unknown detector, expect or payload", 2)
    if not {f"PreToolUse:{tool}", "PreToolUse:*"} & set(row.consumes):
        raise labels.Refusal(f"detector {det} does not consume the tool of a corpus event", 2)
    return det, expect, {"tool_name": tool, "tool_input": payload["tool_input"]}


def plan() -> list[tuple[str, list[tuple[str, str, dict]]]]:
    """[(session id, [(detector, expect, payload)])]: one session per detector of examples.json, then the frozen sessions."""
    try:
        examples, corpus = _read(EXAMPLES)["detectors"], _read(CORPUS)["sessions"]
        out = [(f"synthetic-ex-{n:02d}", [_check(det, e, p) for e, p in
                                          [*(("no_fire", x) for x in ex["no_fire"]), ("fire", ex["fire"])]])
               for n, (det, ex) in enumerate(examples.items(), 1)]
        return out + [(str(s["session_id"]), [_check(e["detector"], e["expect"], e) for e in s["events"]]) for s in corpus]
    except (KeyError, TypeError, AttributeError):
        raise labels.Refusal("the synthetic corpus is malformed", 2) from None


def proposal_of(det: str) -> dict:
    """The stand-in proposal of a detector: a stable id (never one of the private proposals) and its catalogue lesson."""
    h = hashlib.sha256(f"synthetic|{det}".encode()).hexdigest()[:8]
    return {"id": "P-" + h, "lesson_id": next(iter(catalogue.CATALOGUE[det].lessons), "L-" + h)}


def replay(cfg, sessions: list | None = None) -> tuple[list[dict], dict]:
    """(rows, counts): evaluate every event with a throwaway state directory; counts hold events, fired, disagreements, errors."""
    rows, counts, errors = [], dict.fromkeys(("sessions", "events", "fired", "disagreements", "errors"), 0), []
    with tempfile.TemporaryDirectory() as tmp:
        scratch = dataclasses.replace(cfg, data_dir=Path(tmp), shadow_seed=None)   # synthetic rows say `unassigned`; the seed stays out
        scratch.source = getattr(cfg, "source", None)           # `replace` copies fields only: `key_of` looks for the heads file beside it
        for sid, events in sessions or plan():
            counts["sessions"] += 1
            for det, expect, payload in events:
                ev = event_from_hook({**payload, "session_id": sid, "hook_event_name": "PreToolUse", "tool_use_id": "synthetic"})
                t0 = time.monotonic()
                fired = det in shadow._evaluate(scratch, ev, sid, [det], errors.append)
                row = shadow._row(scratch, proposal_of(det), "observe", "detector" if fired else "none", "PreToolUse", ev, sid, b"", t0)
                rows.append({**row, "would_fire": fired, "corpus": "synthetic", "tier": "synthetic", "detector": det})
                counts["events"] += 1
                counts["fired"] += fired
                counts["disagreements"] += fired != (expect == "fire")
    counts["errors"] = len([m for m in errors if m.startswith("error:")])
    return rows, counts


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def target(cfg, out: str | None) -> Path:
    """The file to replace: the default, or `--out`. Whether it is inside `data_dir` is decided by file identity (device and inode of
    the parent and its ancestors, so a differently cased path on a case-insensitive volume is caught); inside, only the synthetic file
    itself is allowed (name compared case-insensitively, then the canonical path is used), never the private corpus, labels,
    approvals or the store. A file already there must be a regular file (not a symlink) holding synthetic rows only. Refusal (exit 2)
    before anything is written."""
    path = Path(out) if out else cfg.path(OUT)
    parent = path.parent.resolve()
    if any(_same(p, cfg.data_dir) for p in (parent, *parent.parents)):
        if not (_same(parent, cfg.data_dir) and path.name.casefold() == OUT.casefold()):
            raise labels.Refusal(f"--out inside the data directory must be {OUT}", 2)
        path = cfg.path(OUT)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return path
    except OSError:
        raise labels.Refusal(NOT_FILE, 2) from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise labels.Refusal(NOT_FILE, 2)
    with os.fdopen(fd, "rb") as fh:
        raw = fh.read(MAX_OUT + 1)
    try:
        ok = len(raw) <= MAX_OUT and all(
            isinstance(r := json.loads(ln), dict) and r.get("corpus") == "synthetic" for ln in raw.decode().splitlines() if ln.strip())
    except (ValueError, RecursionError):
        ok = False
    if not ok:
        raise labels.Refusal("--out exists and is not a file of synthetic rows; nothing was written", 2)
    return path


def run(cfg, out: str | None = None) -> int:
    """Entry of `compile replay-synthetic [--out PATH]`: 0, 1 when the corpus disagrees with the catalogue, 2 for an unusable corpus
    or a refused `--out`."""
    try:
        path = target(cfg, out)
        rows, counts = replay(cfg)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private(path, "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    print("\n".join([*(f"{k}: {v}" for k, v in counts.items()), f"rows: {len(rows)}"]))
    return int(bool(counts["disagreements"] or counts["errors"]))
