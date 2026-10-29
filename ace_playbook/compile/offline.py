"""The nightly offline pass (docs/design/compiler.md 7.3, step 5a): `compile offline [--dry-run]`. For idle transcripts it
runs every catalogue detector into `detector_events.jsonl`, pre-fills labels for live shadow rows (a human row always wins),
records evasion candidates, appends lowering-only demotions (demotions.py) and removes stale shadow_state files. Rows hold
enums, ids, hashes and counts, never transcript text; agent self-tags and `[L-id]` citations are never inputs. The store and
the transcript reader load inside the functions that need them (hook path stays light). `write_labels` and `demote` live in
offline_demote.py (re-exported here). During an arm phase of a detector (phase.py) NO auto label is written for it, in either arm: the
correction rule is symmetric by design, `auto_fp_shell_rerun` and `auto_tp_hook_block` follow a delivered block (warn arm only) and
`auto_fp_prompt_target` would cover the row and hide it from the blind human queue (`neutral_corrections` still counts the fires that carry
a user correction); demotion counts human labels only.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import posixpath
import re
import stat
import time
from datetime import datetime
from pathlib import Path

from ..atomic import write_private
from . import catalogue, demotions, detectors, evidence, labels, metrics, phase, report, shadow
from . import state as state_mod
from .events import end_event, events_from_records, hash16
from .offline_demote import DAY, MIN_SAMPLE, WITHDRAWN, current_mode, demote, iso, write_labels   # noqa: F401  (re-exported)
from .report import _read as read_jsonl
from .shell import command_segments

P95 = "not implemented"                                            # design 7.3 names the 200 ms rule but no demotion cause for it
SHELL = re.compile(r"\s*<bash-input>(.*)</bash-input>\s*", re.S)
EVENTS, EVASION, SEEN = "detector_events.jsonl", "evasion_candidates.jsonl", "offline_state.json"


def tsec(ts: object) -> float | None:
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()


def phash(tu) -> str:
    return evidence.payload_hash({"tool_name": tu.name, "tool_input": tu.input})


# ---------------------------------------------------------------- pure rules (design 7.3)
def span(events: list, i: int) -> list:
    """The events after index `i` up to and including the second human prompt."""
    rest = events[i + 1:]
    humans = [n for n, e in enumerate(rest) if e.human and not e.dup and not e.meta]
    return rest[:humans[1] + 1] if len(humans) > 1 else rest


def prompt_hashes(text: str) -> set[str]:
    """hash16 of prompt tokens, in memory only (quotes, trailing .,;: stripped; `~/` expanded; trailing `/` dropped); never of
    a token under 2 characters or made only of `/`, `~` and `.` (`/`, `~`, `.`, `..`, `//`)."""
    out = set()
    for tok in text.split():
        t = tok.rstrip(".,;:").strip("'\"`").rstrip(".,;:")
        if len(t) < 2 or not t.strip("/.~"):
            continue                                       # trivial tokens are skipped BEFORE `~` could be expanded to home
        for v in (t, os.path.expanduser(t) if t.startswith("~/") else t):
            out |= {hash16(x) for x in (v, v.rstrip("/")) if len(x) >= 2}
    return out


def rm_keys(segments) -> set[str]:
    """hash16 of every rm -rf target of the segments, raw and normalised (a fire key hashes the raw target)."""
    return {hash16(h) for toks in segments for t in detectors._rm_rf_targets(toks) or () for h in (t, posixpath.normpath(t))}


def shell_keys(events: list) -> set[str]:
    """rm targets of the operator's own shell-mode entries: non-meta user records whose WHOLE text is one `<bash-input>` block."""
    return set().union(set(), *(rm_keys(command_segments(m.group(1))) for e in events if e.rtype == "user"
                                and not (e.tool_results or e.dup or e.queued or e.meta) and (m := SHELL.fullmatch(e.user_text))))


def guard_blocked(events: list, tool_id: str) -> bool:
    """The tool result of `tool_id` is a hook error of the operator guard `guard-destructive.sh`."""
    results = [tr for e in events for tr in e.tool_results if tr.id == tool_id]
    m = detectors.HOOK_ERR.match(results[0].text) if results else None
    return bool(m) and detectors._hook_name(m.group(1)).endswith("guard-destructive.sh")


def corrected(events: list, i: int) -> bool:
    """A user correction within 2 prompts after the fire in events[i]."""
    return any(detectors.user_correction(e, {}) for e in span(events, i))


def classify(mode: str, events: list, i: int, tu, key: str, phase: bool = False) -> str | None:
    """The auto label rule (a reason_code of `labels.AUTO`) for the fire of `tu` in events[i]; first match wins. `phase`: the fire's detector
    is in an arm phase: no auto rule labels it (every fire goes to the blind human queue; a correction is a neutral flag in both arms, and the
    shell-rerun and hook-block rules follow a delivered block, so they would differ by arm)."""
    if phase:
        return None
    after = span(events, i)
    said = corrected(events, i)
    prompt = next((e for e in reversed(events[:i]) if e.human and not e.meta), None)
    if mode in ("warn", "block") and said:
        return "auto_fp_correction"
    if key and prompt and key in prompt_hashes(prompt.user_text):
        return "auto_fp_prompt_target"
    if mode == "block" and key and key in shell_keys(after):
        return "auto_fp_shell_rerun"
    if guard_blocked(events[i + 1:], tu.id):
        return "auto_tp_hook_block"
    return "auto_tp_correction" if mode == "observe" and said else None


def evaded(events: list, i: int, tu, key: str) -> bool:
    """One of the next five tool uses is a different Bash command whose rm targets hash to the fire's key."""
    uses = [u for e in events[i:] for u in e.tool_uses]
    k = next(n for n, u in enumerate(uses) if u is tu)
    return bool(key) and any(u.name == "Bash" and phash(u) != phash(tu) and key in rm_keys(u.segments())
                             for u in uses[k + 1:k + 6])


# ---------------------------------------------------------------- transcripts
def discover(cfg, now: float, idle_minutes: float, since_days: int, done: dict) -> tuple[list[tuple], int]:
    """([(path, session hash, size, mtime)] to scan, oldest first; number skipped)."""
    root, todo, skipped = Path(cfg.transcripts_root or "."), [], 0
    for p in sorted(root.rglob("*.jsonl")) if cfg.transcripts_root and root.is_dir() else []:
        try:
            st = os.lstat(p)
        except OSError:
            skipped += 1
            continue
        sh = state_mod.session_hash(p.stem)
        if (stat.S_ISREG(st.st_mode) and "subagents" not in p.parts and st.st_size <= evidence.MAX_TRANSCRIPT
                and evidence.SESSION_ID.fullmatch(p.stem) and idle_minutes * 60 <= now - st.st_mtime <= since_days * DAY
                and done.get(sh) != {"size": st.st_size, "mtime": int(st.st_mtime)}):
            todo.append((st.st_mtime, p, sh, st.st_size))
        else:
            skipped += 1
    return [(p, sh, size, int(m)) for m, p, sh, size in sorted(todo)], skipped


def detector_rows(events: list, sh: str, stamp: str = "") -> list[dict]:
    """One row per detector fire plus its end (`commit_without_push`) and one `_session` summary row (metrics.py); ids are stable; `stamp` is the transcript's size and mtime."""
    state, rows, ts = None, [], ""
    for ev in [*events, end_event("offline")]:
        ts = ev.ts or ts
        at = "end" if ev.rtype == "session_end" else ts                  # a resumed session must not add a second end row
        fires, state, _ = catalogue.evaluate(ev, state, strict=True)
        tu = ev.tool_uses[0] if ev.tool_uses else None
        for f in fires:
            ph = phash(tu) if tu else ""
            rid = hashlib.sha256(f"{sh}|{f.detector}|{at}|{ph}".encode()).hexdigest()[:16]
            rows.append({"ts": ts, "v": 1, "id": rid, "session": sh, "detector": f.detector, "tier": "offline",
                         "count": f.count, "key": f.key, "payload_sha256": ph, "tool_name": tu.name if tu else ""})
    return [*rows, metrics.session_row(events, sh, ts, {n: d.consumes for n, d in catalogue.CATALOGUE.items()}, stamp)]


def join(uses: list, t: float):
    """(event index, tool use) of `uses` = [(seconds, i, tu)] nearest at or before whole second `t` (stamps carry milliseconds,
    so: before `t` + 1), else nearest after within 60 s."""
    before = [u for u in uses if u[0] is not None and u[0] < t + 1]
    after = [u for u in uses if u[0] is not None and t + 1 <= u[0] <= t + 60]
    pick = max(before, key=lambda u: u[0]) if before else min(after, key=lambda u: u[0]) if after else None
    return pick[1:] if pick else None


def session_pass(rows: list[dict], events: list, detector_of, live: frozenset = frozenset()) -> tuple[list[tuple[dict, str]], list[dict], int]:
    """([(shadow row, auto rule)], evasion candidates, neutral corrections) of one transcript; a row joins the tool use `join` picks by time.
    `live` is the detectors in an arm phase: a fire of one is classified with `phase` and counts as neutral when the withheld correction
    rule was the only thing that would have labelled it."""
    occ: dict[str, list] = {}
    for i, e in enumerate(events):
        for tu in e.tool_uses:
            occ.setdefault(phash(tu), []).append((tsec(e.ts), i, tu))
    picks, cands, neutral = [], [], 0
    for r in sorted(rows, key=lambda r: r["ts"]):
        t = tsec(r["ts"])
        hit = join(occ.get(r["payload_sha256"], []), t) if t is not None else None
        if not hit:
            continue
        (i, tu), det, key = hit, detector_of(r["proposal_id"]), ""
        if det in catalogue.CATALOGUE:
            fires = catalogue.evaluate(dataclasses.replace(events[i], tool_uses=[tu], tool_results=[]), None,
                                       names=[det], strict=True)[0]
            key = fires[0].key if fires else ""
        rule = classify(r.get("mode", ""), events, i, tu, key, det in live)
        picks += [(r, rule)] if rule else []
        neutral += det in live and rule is None and r.get("mode") in ("observe", "warn", "block") and corrected(events, i)
        if r.get("mode") == "block" and evaded(events, i, tu, key):
            cid = hashlib.sha256(f"{r['session']}|{r['proposal_id']}|{r['ts']}".encode()).hexdigest()[:16]
            cands.append({"ts": r["ts"], "v": 1, "id": cid, "session": r["session"], "proposal_id": r["proposal_id"], "key": key})
    return picks, cands, neutral


@contextlib.contextmanager
def stage(out: dict):
    try:
        yield
    except (OSError, ValueError, labels.Refusal):               # a failed stage is one error; the others still run
        out["errors"] += 1


def run(cfg, *, dry_run: bool = False, since_days: int = 7, idle_minutes: float = 60.0, now: float | None = None) -> dict:
    """The offline pass; counts only. A session is marked done when its rows were written (and, with shadow rows, its labels)."""
    explicit, now = now is not None, time.time() if now is None else now
    out = {"transcripts": 0, "skipped": 0, "detector_events": 0, "sessions": 0, "labels": dict.fromkeys(labels.AUTO, 0), "demotions": 0,
           "evasion_candidates": 0, "neutral_corrections": 0, "state_removed": 0, "errors": 0, "lesson_check": "not run", "error_rate": "not run",
           "p95_rule": P95}
    rows, old_ev, old_ca, done, todo, corpus, live = [], [], [], {}, [], False, phase.active(cfg, iso(now))
    with stage(out):
        rows = [r for r in report.read_corpora(cfg)[0] if r.get("tier") == "live"]
        corpus = True
        old_ev, old_ca = read_jsonl(cfg.path(EVENTS)), read_jsonl(cfg.path(EVASION))
        seen = demotions.read_regular(cfg.path(SEEN), 1 << 22)
        done = d if isinstance(d := json.loads(seen or b"{}"), dict) else {}
        scanned = {k: v for have in [metrics.latest_sessions(old_ev)] for k, v in done.items() if k in have}    # no `_session` row: scan again
        todo, out["skipped"] = discover(cfg, now, idle_minutes, since_days, scanned)
    by_session, known, metas, picks, new_ev, new_ca, marks = {}, {r.get("id") for r in old_ev + old_ca}, {}, [], [], [], {}
    for r in rows:
        by_session.setdefault(r["session"], []).append(r)
    detector_of = lambda pid: metas.setdefault(pid, report._meta(cfg, pid)[0])             # noqa: E731
    for path, sh, size, mtime in todo:
        try:
            from ..clean.transcript import read_records
            events = list(events_from_records(read_records(path)))
            ev_rows, (got, cands, neutral) = detector_rows(events, sh, f"{size}:{mtime}"), session_pass(by_session.get(sh, []), events, detector_of, live)
        except Exception:                                      # one bad transcript never stops the pass
            out["errors"] += 1
            continue
        out["transcripts"] += 1
        marks[sh] = {"size": size, "mtime": mtime}
        picks += got
        out["neutral_corrections"] += neutral
        for bucket, fresh in ((new_ev, ev_rows), (new_ca, cands)):
            bucket += [r for r in fresh if r["id"] not in known and not known.add(r["id"])]     # de-duplicate by id
    out["sessions"] = sum(r["detector"] == metrics.SESSION for r in new_ev)          # summary rows; detector_events counts fires only
    out["detector_events"], out["evasion_candidates"], labs, human, before = len(new_ev) - out["sessions"], len(new_ca), {}, {}, out["errors"]
    with stage(out):
        counts, labs, human = write_labels(cfg, picks, now, dry_run)
        out["labels"].update(counts)
    keep = {sh: v for sh, v in marks.items() if out["errors"] == before or sh not in by_session}
    with stage(out):
        if not dry_run:
            for name, fresh in ((EVENTS, new_ev), (EVASION, new_ca)):
                if fresh:
                    shadow._append(cfg.path(name), "".join(json.dumps(r, sort_keys=True) + "\n" for r in fresh), sync=True)
            if keep:
                write_private(cfg.path(SEEN), json.dumps({**done, **keep}, sort_keys=True) + "\n")
    if corpus:                                                 # an unreadable corpus is not "no fires": never demote on it
        with stage(out):
            demote(cfg, rows, labs, old_ca + new_ca, now, dry_run, out, explicit, human)
    with stage(out):
        out["state_removed"] = state_mod.prune(cfg.data_dir, now - 7 * DAY, dry_run)
    return out
