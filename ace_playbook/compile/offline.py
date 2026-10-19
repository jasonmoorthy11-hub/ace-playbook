"""The nightly offline pass (docs/design/compiler.md 7.3, step 5a): `compile offline [--dry-run]`.

For idle transcripts it runs every catalogue detector into `detector_events.jsonl`, pre-fills labels for live shadow rows
(auto fp, or `tp_candidate`; a human row always wins), records evasion candidates after a block, appends hash-chained
demotions (demotions.py, lowering only) and removes stale shadow_state files. Rows hold enums, ids, hashes, counts and
timestamps, never transcript text; agent self-tags and `[L-id]` citations are never inputs. `dry_run` writes nothing.
The store and the transcript reader load inside the functions that need them (hook path stays light).
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
from pathlib import Path

from ..atomic import write_private
from . import catalogue, demotions, detectors, evidence, labels, report, shadow
from . import state as state_mod
from .events import end_event, events_from_records, hash16
from .report import _read as read_jsonl
from .shell import command_segments

P95 = "not computable: no dur_ms in rows"
DAY, STAMP, BASH_INPUT = 86400, "%Y-%m-%dT%H:%M:%SZ", re.compile(r"<bash-input>(.*?)</bash-input>", re.S)
EVENTS, EVASION, SEEN = "detector_events.jsonl", "evasion_candidates.jsonl", "offline_state.json"


def iso(t: float) -> str:
    return time.strftime(STAMP, time.gmtime(t))


def phash(tu) -> str:
    return evidence.payload_hash({"tool_name": tu.name, "tool_input": tu.input})


# ---------------------------------------------------------------- pure rules (design 7.3)
def span(events: list, i: int) -> list:
    """The events after index `i` up to and including the second human prompt."""
    out, humans = [], 0
    for e in events[i + 1:]:
        out.append(e)
        humans += bool(e.human and not e.dup)
        if humans == 2:
            break
    return out


def prompt_hashes(text: str) -> set[str]:
    """hash16 of every token of a prompt (surrounding quotes and trailing .,;: stripped; also without `~`, without a `/` end)."""
    out = set()
    for tok in text.split():
        t = tok.rstrip(".,;:").strip("'\"`").rstrip(".,;:")
        for v in (t, t.replace("~", "")):
            out |= {hash16(v), hash16(v.rstrip("/"))}
    return out


def rm_keys(segments) -> set[str]:
    """hash16 of every rm -rf target of the segments, raw and normalised (a fire key hashes the raw target)."""
    return {hash16(h) for toks in segments for t in detectors._rm_rf_targets(toks) or () for h in (t, posixpath.normpath(t))}


def shell_keys(events: list) -> set[str]:
    """rm targets of the shell-mode commands (`<bash-input>`) in `events`; Claude Code does not count them as prompts."""
    return set().union(set(), *(rm_keys(command_segments(m.group(1))) for e in events if e.rtype == "user"
                                and not e.tool_results for m in BASH_INPUT.finditer(e.user_text)))


def guard_blocked(events: list, tool_id: str) -> bool:
    """The tool result of `tool_id` is a hook error of the operator guard `guard-destructive.sh`."""
    for tr in (tr for e in events for tr in e.tool_results if tr.id == tool_id):
        m = detectors.HOOK_ERR.match(tr.text)
        return bool(m) and detectors._hook_name(m.group(1)).endswith("guard-destructive.sh")
    return False


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
        fires, state, _ = catalogue.evaluate(ev, state, strict=True)
        tu = ev.tool_uses[0] if ev.tool_uses else None
        for f in fires:
            ph = phash(tu) if tu else ""
            rid = hashlib.sha256(f"{sh}|{f.detector}|{ts}|{ph}".encode()).hexdigest()[:16]
            rows.append({"ts": ts, "v": 1, "id": rid, "session": sh, "detector": f.detector, "tier": "offline",
                         "count": f.count, "key": f.key, "payload_sha256": ph, "tool_name": tu.name if tu else ""})
    return rows


def session_pass(rows: list[dict], events: list, detector_of) -> tuple[list[tuple[dict, str]], list[dict]]:
    """([(shadow row, auto rule)], evasion candidates) for the live shadow rows of one transcript. A row joins the fire
    with the same payload hash (the n-th row of a proposal and payload to the n-th use in the transcript)."""
    occ: dict[str, list] = {}
    for i, e in enumerate(events):
        for tu in e.tool_uses:
            occ.setdefault(phash(tu), []).append((i, tu))
    seen, picks, cands = Counter(), [], []
    for r in sorted(rows, key=lambda r: r["ts"]):
        slot = (r["payload_sha256"], r["proposal_id"])
        n, seen[slot] = seen[slot], seen[slot] + 1
        if n >= len(occ.get(slot[0], [])):
            continue
        i, tu = occ[slot[0]][n]
        det, key = detector_of(r["proposal_id"]), ""
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


def error_rate_high(cfg, rows: list[dict], now: float) -> bool:
    """Over seven days, `error:` lines of shadow.log are more than 1 % of (error lines + shadow rows)."""
    since = iso(now - 7 * DAY)
    text = (demotions.read_regular(cfg.path("shadow.log"), 2 * shadow.LOG_CAP) or b"").decode("utf-8", "replace")
    errors = sum(ln[:20] >= since and ln[21:].startswith("error:") for ln in text.splitlines())
    return errors > 0.01 * (errors + sum(r["ts"] >= since for r in rows))


def demote(cfg, rows: list[dict], labs: dict[str, str], cands: list[dict], now: float, dry: bool) -> int:
    """Append the demotion rows of design 7.3 for every approved proposal; returns how many were new."""
    drows, broken = demotions.read_chain(cfg.data_dir)
    if broken is not None:
        raise ValueError("demotions.jsonl chain broken")
    lessons = None
    if cfg.deltas.exists():
        from ..store import DeltaLog
        lessons = {k: v.get("status") for k, v in DeltaLog(cfg.deltas).state().items()}
    slow, new = error_rate_high(cfg, rows, now), 0
    for p, mode, at in shadow.approved_records(cfg):
        pid, det = p["id"], p["check"]["detector"]
        since = max([at, *(r["ts"] for r in drows if r["proposal_id"] == pid and r["cause"] != "evasion_pair")])
        fps = [(r["ts"], labels.row_id(r)) for r in rows if r["proposal_id"] == pid and r["ts"] >= since
               and labs.get(labels.row_id(r)) == "fp"]
        ref = demotions.pair_ref([c for c in cands if c.get("proposal_id") == pid], 14)
        flagged = any(r["proposal_id"] == pid and r["cause"] == "evasion_pair" and r["ref"] == ref for r in drows)
        stale = det not in catalogue.CATALOGUE or catalogue.CATALOGUE[det].source_sha256 != p["provenance"]["source_sha256"]
        disable = ("lesson_inactive" if lessons is not None and lessons.get(p["lesson_id"]) != "active" else
                   "source_sha256" if stale else "error_rate" if slow else None)
        cur = demotions.lower(mode, demotions.effective_cap(drows, pid))
        for f in demotions.decide(pid, cur, disable=disable, fps=sorted(set(fps)), evasion=None if flagged else ref):
            f = {**f, "ts": iso(now)}
            if demotions.duplicate(drows, f) is None:
                drows.append(f if dry else demotions.append(cfg.data_dir, f))
                new += 1
    return new


# ---------------------------------------------------------------- the pass
@contextlib.contextmanager
def stage(out: dict):
    try:
        yield
    except (OSError, ValueError, labels.Refusal):               # a failed stage is one error; the others still run
        out["errors"] += 1


def run(cfg, *, dry_run: bool = False, since_days: int = 7, idle_minutes: float = 60.0, now: float | None = None) -> dict:
    """The offline pass; counts only. Idempotent: ids de-duplicate rows, offline_state.json skips unchanged transcripts."""
    now = time.time() if now is None else now
    out = {"transcripts": 0, "skipped": 0, "detector_events": 0, "labels": dict.fromkeys(labels.AUTO, 0), "demotions": 0,
           "evasion_candidates": 0, "state_removed": 0, "errors": 0, "p95_rule": P95}
    rows, old_ev, old_ca, done, todo = [], [], [], {}, []
    with stage(out):
        rows = [r for r in report.read_corpora(cfg)[0] if r.get("tier") == "live"]
        old_ev, old_ca = read_jsonl(cfg.path(EVENTS)), read_jsonl(cfg.path(EVASION))
        seen = demotions.read_regular(cfg.path(SEEN), 1 << 22)
        done = d if isinstance(d := json.loads(seen or b"{}"), dict) else {}
        todo, out["skipped"] = discover(cfg, now, idle_minutes, since_days, done)
    by_session, known, metas, picks, new_ev, new_ca = {}, {r.get("id") for r in old_ev + old_ca}, {}, [], [], []
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
        done[sh] = {"size": size, "mtime": mtime}
        picks += got
        for bucket, fresh in ((new_ev, ev_rows), (new_ca, cands)):
            for r in fresh:
                if r["id"] not in known:
                    known.add(r["id"])
                    bucket.append(r)
    out["detector_events"], out["evasion_candidates"], labs = len(new_ev), len(new_ca), {}
    with stage(out):
        counts, labs = write_labels(cfg, picks, now, dry_run)
        out["labels"].update(counts)
    with stage(out):
        if not dry_run:
            for name, fresh in ((EVENTS, new_ev), (EVASION, new_ca)):
                if fresh:
                    shadow._append(cfg.path(name), "".join(json.dumps(r, sort_keys=True) + "\n" for r in fresh), sync=True)
            if todo:
                write_private(cfg.path(SEEN), json.dumps(done, sort_keys=True) + "\n")
    with stage(out):
        out["demotions"] = demote(cfg, rows, labs, old_ca + new_ca, now, dry_run)
    with stage(out):
        out["state_removed"] = state_mod.prune(cfg.data_dir, now - 7 * DAY, dry_run)
    return out


def lines(out: dict) -> list[str]:
    """One printable-ASCII `name: count` line per count; `labels` is followed by its reason_code breakdown."""
    head = [f"{k}: {out[k]}" for k in ("transcripts", "skipped", "detector_events")]
    head += [f"labels: {sum(out['labels'].values())}", *(f"labels.{k}: {v}" for k, v in out["labels"].items())]
    return head + [f"{k}: {out[k]}" for k in ("demotions", "evasion_candidates", "state_removed", "errors", "p95_rule")]
