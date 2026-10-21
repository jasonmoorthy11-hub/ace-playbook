"""`compile digest` (docs/design/compiler.md 8.x): the nightly read-out, six plain-text blocks rendered from `status.build` and nothing else.

Blocks, in order: `status`, `needs you`, `detectors`, `health`, `changes`, `next`. Every figure comes from the status dict (so from
`report.collect`), and there is no rate arithmetic here: the spike rule compares integers. Each run appends ONE JSON line to
`<data_dir>/digest.log` (0600, O_APPEND) so that a night without a digest can be seen. Nothing reaches a model and the shadow hook
never imports this module. Imports: stdlib, `..config`, `.chains`, `.labels`, `.modes`, `.status`.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import sys
from datetime import timedelta

from ..config import Config
from . import chains, labels, modes, status

PROTOCOL, LOG, STALE_HOURS, SPIKE_FACTOR, SPIKE_MIN = "shadow-v1", "digest.log", 36, 3, 3
LABEL_CMD = "python3 -m ace_playbook.compile label --queue"
DATE = re.compile(r"\d{4}-\d\d-\d\d")


def _status_block(s: dict) -> list[str]:
    m = s["measurement"]
    return [f"protocol {PROTOCOL}  report {s['report_sha256'][:8]}  day {'-' if m['day'] is None else m['day']} of {status.WEEK}  "
            f"keyed {'yes' if m['keyed'] else 'no'}  chains {'ok' if all(v == 'ok' for v in m['chains'].values()) else 'BROKEN'}"]


def _needs(s: dict) -> list[str]:
    hidden = s.get("queue_retired", 0)
    more = [f"{hidden} more on retired proposals (label --queue --all)"] if hidden else []
    if not s["queue_total"]:
        return ["nothing to label", *more]
    return [f"{s['queue_total']} unlabelled fires (oldest {status.age(s['queue'][0]['ts'], s['generated_at'])})", "run: " + LABEL_CMD, *more]


def _spikes(s: dict) -> list[str]:
    """A proposal with at least three rows today and more than three times its daily mean over the window (compared as integers:
    rows today x window days against three x rows in the window)."""
    today, out = s["generated_at"][:10], []
    for pid in sorted({r["proposal_id"] for r in s["per_day"]}):
        mine = [r for r in s["per_day"] if r["proposal_id"] == pid]
        n, total = sum(r["rows"] for r in mine if r["date"] == today), sum(r["rows"] for r in mine)
        if n >= SPIKE_MIN and n * s["days"] > SPIKE_FACTOR * total:
            out.append(f"spike: {pid} {n} rows today, {total} in {s['days']} days")
    return out


def _health(s: dict) -> list[str]:
    h, now = s["heartbeat"], s["generated_at"]
    out = [f"silent: {n.removeprefix('silent:')}" for n in h["notes"] if n.startswith("silent:")]
    for name, ts in (("hook", h["last_hook_row"]), ("offline scan", h["last_offline_scan"]), ("nightly", h["last_nightly_ok"])):
        secs = status.seconds_since(ts, now)
        if secs is None or secs > STALE_HOURS * 3600:
            out.append(f"stale: {name} {status.age(ts, now)}")
    out += _spikes(s)
    out += [f"awaiting offline scan: {n} fires"] if (n := s["agreement"].get("awaiting_scan")) else []
    out += [f"chain {k} {v}" for k, v in s["measurement"]["chains"].items() if v != "ok"]
    out += [f"note: {n}" for n in h["notes"] if not n.startswith("silent:") and ":broken:" not in n]
    return out or ["all clear"]


def _changes(s: dict, since: str, rows: list[dict]) -> list[str]:
    found = [(r["ts"], f"{r['ts']} approval {r['proposal_id']} {r['mode']}") for r in rows]
    found += [(r["ts"], f"{r['ts']} demotion {r['proposal_id']} {r['to_mode']} {r['cause']}") for r in s["demotions"]]
    return [text for ts, text in sorted(found) if ts > since] or ["none"]


def _next(s: dict) -> list[str]:
    due = s["measurement"]["week1_due"]
    return [f"week 1 due {due}" if due else "week 1 not started",
            *(f"{pid} {p['detector']}: warn earliest {p['warn_earliest'][:10]}" for pid, p in s["proposals"].items()
              if p["warn_earliest"] and p["warn_earliest"] > s["generated_at"] and p["verdict"] != "retired")]


def render(s: dict, since: str | None, approvals: list[dict] = ()) -> str:
    """The digest of status dict `s`. `since` is a stamp (default: 24 hours before `s`); `approvals` are the approvals-chain rows
    (the status dict holds the demotion rows but not these)."""
    since = since or (status.parse_ts(s["generated_at"]) - timedelta(hours=24)).strftime(status.FMT)
    blocks = (("status", _status_block(s)), ("needs you", _needs(s)), ("detectors", status.table(s)), ("health", _health(s)),
              ("changes", _changes(s, since, list(approvals))), ("next", _next(s)))
    return "\n".join(line for name, lines in blocks for line in (name, *lines, "")) + "\n"


def append_log(cfg: Config, s: dict) -> dict:
    """Append the one-line record of this digest to digest.log (0600, O_APPEND, no-follow, locked); returns the row."""
    row = {"ts": s["generated_at"], "report_sha256": s["report_sha256"], "queue": s["queue_total"], "notes": s["heartbeat"]["notes"]}
    path = cfg.path(LOG)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600), "ab") as fh:
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


def last_logged(cfg: Config) -> str | None:
    """The stamp of the newest readable line of digest.log (its last 4 KiB; a symlink or non-file counts as none), else None."""
    try:
        fd = os.open(cfg.path(LOG), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        fh.seek(max(0, os.fstat(fd).st_size - 4096))
        tail = fh.read().decode("utf-8", "replace")
    for line in reversed(tail.splitlines()):
        try:
            ts = json.loads(line).get("ts")
        except (ValueError, AttributeError):
            continue
        if status.parse_ts(ts):
            return ts
    return None


def run_quiet(cfg: Config, days: int = 14, write: bool = True) -> dict:
    """The nightly entry: build the status; unless `write` is false, refresh `status.json` (the status line reads it) and append the log
    line; return `{"queue", "notes"}`. Prints nothing."""
    s = status.build(cfg, days)
    if write:
        status.write_json(cfg, s)
        append_log(cfg, s)
    return {"queue": s["queue_total"], "notes": s["heartbeat"]["notes"]}


def run(cfg: Config, since: str | None = None, days: int = 14) -> int:
    """Entry of `compile digest`: print the digest, then append its log line. `since` defaults to the last digest logged, else 24 hours back.
    Exit 0; 2 for a bad `--since` or `--days`."""
    try:
        s = status.build(cfg, days)
        since = since + "T00:00:00Z" if since and DATE.fullmatch(since) else since or last_logged(cfg)
        if since is not None and status.parse_ts(since) is None:
            raise labels.Refusal("--since must be YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ", 2)
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    print(render(s, since, modes.read_approvals(cfg.data_dir, chains.key_of(cfg))[0]), end="")
    append_log(cfg, s)
    return 0


def add_parser(sub) -> None:
    """Register `digest` on the CLI's subparsers."""
    p = sub.add_parser("digest", help="six plain-text blocks from status (read-only; appends one line to digest.log)")
    p.add_argument("--since", metavar="ISO", help="list approvals and demotions after this stamp or date (default: the last digest logged, else 24 hours)")
    p.add_argument("--days", type=int, default=14, help="days in the report window (default 14, 1 to 3660)")
    p.set_defaults(run=lambda c, a: run(c, a.since, a.days))
