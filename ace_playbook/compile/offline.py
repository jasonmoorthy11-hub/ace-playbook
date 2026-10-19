"""The nightly offline pass (docs/design/compiler.md 7.3, step 5a): `compile offline [--dry-run]`. For idle transcripts it
runs every catalogue detector into `detector_events.jsonl`, pre-fills labels for live shadow rows (a human row always wins),
records evasion candidates, appends lowering-only demotions (demotions.py) and removes stale shadow_state files. Rows hold
enums, ids, hashes and counts, never transcript text; agent self-tags and `[L-id]` citations are never inputs. The store and
the transcript reader load inside the functions that need them (hook path stays light).
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
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..atomic import write_private
from . import catalogue, demotions, detectors, evidence, labels, modes, report, shadow
from . import state as state_mod
from .events import end_event, events_from_records, hash16
from .report import _read as read_jsonl
from .shell import command_segments

P95 = "not computable: no dur_ms in rows"
DAY, STAMP, SHELL = 86400, "%Y-%m-%dT%H:%M:%SZ", re.compile(r"\s*<bash-input>(.*)</bash-input>\s*", re.S)
MIN_SAMPLE = 100
WITHDRAWN = {"dormant", "superseded", "retired", "quarantined"}      # store statuses that mean withdrawn (not compiled)
EVENTS, EVASION, SEEN = "detector_events.jsonl", "evasion_candidates.jsonl", "offline_state.json"


def iso(t: float) -> str:
    return time.strftime(STAMP, time.gmtime(t))


def tsec(ts: object) -> float | None:
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()


def phash(tu) -> str:
    return evidence.payload_hash({"tool_name": tu.name, "tool_input": tu.input})


# ---------------------------------------------------------------- pure rules (design 7.3)
def span(events: list, i: int) -> list:
    """The events after index `i` up to and including the second human prompt."""
    rest = events[i + 1:]
    humans = [n for n, e in enumerate(rest) if e.human and not e.dup]
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


def classify(mode: str, events: list, i: int, tu, key: str) -> str | None:
    """The auto label rule (a reason_code of `labels.AUTO`) for the fire of `tu` in events[i]; first match wins."""
    after = span(events, i)
    said = any(detectors.user_correction(e, {}) for e in after)
    prompt = next((e for e in reversed(events[:i]) if e.human), None)
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


def detector_rows(events: list, sh: str) -> list[dict]:
    """One row per detector fire over the transcript plus its end (`commit_without_push`); ids are stable across reruns."""
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
    return rows


def join(uses: list, t: float):
    """(event index, tool use) of `uses` = [(seconds, i, tu)] nearest at or before whole second `t` (stamps carry milliseconds,
    so: before `t` + 1), else nearest after within 60 s."""
    before = [u for u in uses if u[0] is not None and u[0] < t + 1]
    after = [u for u in uses if u[0] is not None and t + 1 <= u[0] <= t + 60]
    pick = max(before, key=lambda u: u[0]) if before else min(after, key=lambda u: u[0]) if after else None
    return pick[1:] if pick else None


def session_pass(rows: list[dict], events: list, detector_of) -> tuple[list[tuple[dict, str]], list[dict]]:
    """([(shadow row, auto rule)], evasion candidates) of one transcript; a row joins the tool use `join` picks by time."""
    occ: dict[str, list] = {}
    for i, e in enumerate(events):
        for tu in e.tool_uses:
            occ.setdefault(phash(tu), []).append((tsec(e.ts), i, tu))
    picks, cands = [], []
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
        rule = classify(r.get("mode", ""), events, i, tu, key)
        picks += [(r, rule)] if rule else []
        if r.get("mode") == "block" and evaded(events, i, tu, key):
            cid = hashlib.sha256(f"{r['session']}|{r['proposal_id']}|{r['ts']}".encode()).hexdigest()[:16]
            cands.append({"ts": r["ts"], "v": 1, "id": cid, "session": r["session"], "proposal_id": r["proposal_id"], "key": key})
    return picks, cands


# ---------------------------------------------------------------- labels, demotions
def write_labels(cfg, picks: list[tuple[dict, str]], now: float, dry: bool) -> tuple[Counter, dict[str, str]]:
    """(auto rows written per rule, effective labels). Never over a human row, never twice (newest row is an auto row)."""
    rows, broken = labels.read_chain(cfg)
    if broken is not None:
        raise labels.Refusal("labels.jsonl chain broken")
    human, done, added = {r.get("ref") for r in rows if r.get("reason_code") not in labels.AUTO}, Counter(), []
    newest = {r.get("ref"): r.get("reason_code") for r in rows}
    for row, rule in picks:
        ref = labels.row_id(row)
        if ref not in human and newest.get(ref) not in labels.AUTO:
            newest[ref] = rule
            done[rule] += 1
            added.append({"ref": ref, "label": labels.AUTO[rule], "reason_code": rule})
            if not dry:
                labels.append_row(cfg, labels.AUTO[rule], ref, None, reason_code=rule, at=iso(now))
    return done, labels.effective(rows + added)


def current_mode(copy: tuple[dict, str, dict], arows: list[dict], abroken, drows: list[dict], until: str = "~") -> modes.Resolution:
    """`modes.resolve` for an approved copy (proposal, mode, record) as of `until` (a stamp after it cannot blind FP counting)."""
    return modes.resolve(copy[0]["id"], copy[2], arows, abroken, drows, None, until)


def demote(cfg, rows: list[dict], labs: dict[str, str], cands: list[dict], now: float, dry: bool, out: dict, fixed=True) -> None:
    """Append design 7.3's demotion rows (counts to `out`); `fixed` stamps `iso(now)`, else `append` stamps at append time."""
    drows, broken = demotions.read_chain(cfg.data_dir)
    if broken is not None:
        raise ValueError("demotions.jsonl chain broken")
    lessons, out["lesson_check"] = report.lesson_statuses(cfg)
    out["errors"] += out["lesson_check"] == "chain invalid"
    errors, fires, full = report.hook_errors(cfg, rows, iso(now - 7 * DAY))
    n = errors + fires
    sure = n >= MIN_SAMPLE and not full
    out["error_rate"] = ("not computable (log full)" if full else f"computed (n={n})" if sure
                         else f"not computable (n={n}, need {MIN_SAMPLE})")
    arows, abroken = modes.read_approvals(cfg.data_dir)         # unreadable counts as broken: the resolver caps at observe
    for copy in shadow.approved_records(cfg):
        try:
            pid, det = (p := copy[0])["id"], p["check"]["detector"]
            res = current_mode(copy, arows, abroken, drows, iso(now))
            after = (a := modes.anchor(arows, pid, iso(now), abroken)) and a["ts"]     # rows before it are lifted: they hide no cause
            if (cur := res.mode) == "disabled":                # revoked or disabled: nothing left to lower
                continue
            fps = [(r["ts"], labels.row_id(r)) for r in rows if r["proposal_id"] == pid and r["ts"] >= res.window_start
                   and labs.get(labels.row_id(r)) == "fp"]
            ref = demotions.pair_ref([c for c in cands if c.get("proposal_id") == pid], 14)
            flagged = any(r["proposal_id"] == pid and r["cause"] == "evasion_pair" and r["ref"] == ref for r in drows)
            stale = det not in catalogue.CATALOGUE or catalogue.CATALOGUE[det].source_sha256 != p["provenance"]["source_sha256"]
            disable = ("lesson_inactive" if (lessons or {}).get(p["lesson_id"]) in WITHDRAWN else
                       "source_sha256" if stale else "error_rate" if sure and errors > 0.01 * n else None)
            for f in demotions.decide(pid, cur, disable=disable, fps=sorted(set(fps)), evasion=None if flagged else ref):
                f = {**f, "ts": iso(now)} if fixed else f
                if demotions.duplicate(drows, f, after) is None:
                    drows.append({"ts": iso(time.time()), **f} if dry else demotions.append(cfg.data_dir, f, current=cur, after=after))
                    out["demotions"] += 1
        except (KeyError, TypeError, ValueError, OSError):
            out["errors"] += 1


@contextlib.contextmanager
def stage(out: dict):
    try:
        yield
    except (OSError, ValueError, labels.Refusal):               # a failed stage is one error; the others still run
        out["errors"] += 1


def run(cfg, *, dry_run: bool = False, since_days: int = 7, idle_minutes: float = 60.0, now: float | None = None) -> dict:
    """The offline pass; counts only. A session is marked done only when its rows were written and, if it has shadow rows,
    the label stage succeeded."""
    explicit, now = now is not None, time.time() if now is None else now
    out = {"transcripts": 0, "skipped": 0, "detector_events": 0, "labels": dict.fromkeys(labels.AUTO, 0), "demotions": 0,
           "evasion_candidates": 0, "state_removed": 0, "errors": 0, "lesson_check": "not run", "error_rate": "not run",
           "p95_rule": P95}
    rows, old_ev, old_ca, done, todo, corpus = [], [], [], {}, [], False
    with stage(out):
        rows = [r for r in report.read_corpora(cfg)[0] if r.get("tier") == "live"]
        corpus = True
        old_ev, old_ca = read_jsonl(cfg.path(EVENTS)), read_jsonl(cfg.path(EVASION))
        seen = demotions.read_regular(cfg.path(SEEN), 1 << 22)
        done = d if isinstance(d := json.loads(seen or b"{}"), dict) else {}
        todo, out["skipped"] = discover(cfg, now, idle_minutes, since_days, done)
    by_session, known, metas, picks, new_ev, new_ca, marks = {}, {r.get("id") for r in old_ev + old_ca}, {}, [], [], [], {}
    for r in rows:
        by_session.setdefault(r["session"], []).append(r)
    detector_of = lambda pid: metas.setdefault(pid, report._meta(cfg, pid)[0])             # noqa: E731
    for path, sh, size, mtime in todo:
        try:
            from ..clean.transcript import read_records
            events = list(events_from_records(read_records(path)))
            ev_rows, (got, cands) = detector_rows(events, sh), session_pass(by_session.get(sh, []), events, detector_of)
        except Exception:                                      # one bad transcript never stops the pass
            out["errors"] += 1
            continue
        out["transcripts"] += 1
        marks[sh] = {"size": size, "mtime": mtime}
        picks += got
        for bucket, fresh in ((new_ev, ev_rows), (new_ca, cands)):
            bucket += [r for r in fresh if r["id"] not in known and not known.add(r["id"])]     # de-duplicate by id
    out["detector_events"], out["evasion_candidates"], labs, before = len(new_ev), len(new_ca), {}, out["errors"]
    with stage(out):
        counts, labs = write_labels(cfg, picks, now, dry_run)
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
            demote(cfg, rows, labs, old_ca + new_ca, now, dry_run, out, explicit)
    with stage(out):
        out["state_removed"] = state_mod.prune(cfg.data_dir, now - 7 * DAY, dry_run)
    return out
