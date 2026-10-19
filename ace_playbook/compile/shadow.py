"""The shadow hook (docs/design/compiler.md 7): evaluate approved proposals on one live hook payload.

hooks/shadow.sh calls `main(["pre"|"stop"])`: one payload on stdin (cap 256 KiB), the approved proposals re-validated,
their detectors run, one `shadow.jsonl` row per fire; ALWAYS returns 0, failures are type-only lines in `shadow.log`.
Observe prints nothing; warn echoes the message on stderr and prints ONE `hookSpecificOutput.additionalContext` JSON
on stdout (verified live for PreToolUse; Stop per the hooks reference only); block prints the deny/block JSON and wins.
A copy above observe needs a matching `approvals.jsonl` row (else observe, logged); `demotions.jsonl` only lowers.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

from .. import config as cfgmod
from ..canon import canonical, now_iso
from . import approvals, catalogue, demotions, evidence, proposal
from . import state as state_mod
from .events import event_from_hook

MAX_STDIN, MAX_FILES, MAX_FILE_BYTES, LOG_CAP = 262144, 50, 16384, 1000000
APPROVED, EVENTS = "approved", {"pre": "PreToolUse", "stop": "Stop"}
MODES = ALLOWED_MODES = ("observe", "warn", "block")   # warn and block also need a matching approvals.jsonl row (7.3)
EXTRA_KEYS = {"approved_at", "proposal_sha256"}


# ---------------------------------------------------------------- the approved record
def record_sha256(rec: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in rec.items() if k != "proposal_sha256"}).encode()).hexdigest()


def approved_record(p: dict, mode: str = "observe", now: str | None = None) -> dict:
    """The proposal plus its approval fields; `proposal_sha256` covers every other field (tamper evidence)."""
    rec = {**p, "reviewed_by": "operator-tty", "approved_at": now or now_iso(), "mode": mode}
    rec["proposal_sha256"] = record_sha256(rec)
    return rec


def pending_body(rec: object) -> tuple[dict | None, str]:
    """(the proposal as it was before approval, its approved mode), or (None, reason)."""
    if not (isinstance(rec, dict) and set(rec) == proposal.KEYS | EXTRA_KEYS):
        return None, "record"
    at = rec["approved_at"]
    if rec["reviewed_by"] != "operator-tty" or rec["mode"] not in MODES or not (
            isinstance(at, str) and proposal.STAMP.fullmatch(at)):
        return None, "record"
    if rec["proposal_sha256"] != record_sha256(rec):
        return None, "hash"
    body = {k: v for k, v in rec.items() if k not in EXTRA_KEYS}
    return {**body, "reviewed_by": None, "mode": "observe"}, rec["mode"]


def approved_dir(cfg) -> Path:
    return Path(cfg.data_dir) / proposal.PROPOSALS / APPROVED


def approved_path(cfg, pid: str) -> Path:
    """`proposals/approved/<id>.json`; ValueError for a bad id, a symlink or an escaping path."""
    proposal.proposal_path(cfg, pid)
    base = approved_dir(cfg)
    path = base / (pid + ".json")
    if base.is_symlink() or (base.exists() and not base.is_dir()) or path.is_symlink() or path.parent.resolve() != base.resolve():
        raise ValueError("approved directory or file is a symlink, or the path escapes the approved directory")
    return path


class _Approvals:
    """The approvals chain, read at most once per hook call and only if a copy above observe needs it."""
    def __init__(self, cfg, log) -> None:
        self.cfg, self.log, self.loaded, self.rows, self.problem = cfg, log, False, [], None

    def check(self, rec: dict) -> str:
        """`ok`, `missing`, `mismatch` or `chain_broken` for one record (a read error counts as missing)."""
        if not self.loaded:
            self.loaded = True
            try:
                self.rows, broken = approvals.read_chain(self.cfg.data_dir)
                self.problem = "chain_broken" if broken else None
            except Exception as exc:                          # type only; fail toward observe
                self.log(f"error:approvals:{type(exc).__name__}")
                self.problem = "missing"
            if self.problem == "chain_broken":
                self.log("approvals:chain_broken")
        return self.problem or approvals.verify_copy(rec, self.rows)


def _tail(cfg, n: int = 1000) -> str:
    """The last `n` bytes of shadow.log, '' when it is missing or not a regular file."""
    try:
        with os.fdopen(os.open(Path(cfg.data_dir) / "shadow.log", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as fh:
            info = os.fstat(fh.fileno())
            fh.seek(max(0, info.st_size - n))
            return fh.read(n).decode("utf-8", "replace") if stat.S_ISREG(info.st_mode) else ""
    except OSError:
        return ""


def _once(cfg, log, msg: str) -> None:
    """Log a per-call repeat only if it is not in the last 1000 bytes of shadow.log: it can never fill the log to LOG_CAP."""
    if msg not in _tail(cfg):
        log(msg)


def _demotions(cfg, log) -> tuple[list[dict], int | None]:
    """(rows, broken_at) of demotions.jsonl, read once per call; a broken chain or a read error is logged once and caps all at observe."""
    try:
        rows, broken = demotions.read_chain(cfg.data_dir)
    except Exception as exc:
        _once(cfg, log, f"demotions:{type(exc).__name__}")
        return [], 0
    if broken is not None:
        _once(cfg, log, "demotions:chain_broken")
    return rows, broken


def _load_one(cfg, path: Path, pid: str, log, appr: _Approvals | None = None, dem=((), None)) -> tuple[dict, str] | None:
    try:
        if os.lstat(path).st_size > MAX_FILE_BYTES:
            return log(f"skipped:{pid}:oversize")
        rec = proposal.load(path)
        body, mode = pending_body(rec)
        if body is None or body["id"] != pid:
            return log(f"skipped:{pid}:invalid:{mode if body is None else 'id'}")
        if mode not in ALLOWED_MODES:
            return _once(cfg, log, f"skipped:{pid}:mode_disabled")
        rep = proposal.validate(body, cfg, hot=True)
        if not rep.ok:
            return log(f"skipped:{pid}:invalid:{rep.failed}")
        if mode != "observe":                                 # warn and block need a matching approvals row
            verdict = (appr or _Approvals(cfg, log)).check(rec)
            if verdict != "ok":
                if verdict != "chain_broken":
                    _once(cfg, log, f"approvals:{pid}:{verdict}")
                mode = "observe"
        cap = demotions.effective_cap(dem[0], pid)            # then a demotion can only lower what the chain allows
        if dem[1] is not None:
            cap = demotions.lower("observe", cap)
        if cap == "disabled":
            return _once(cfg, log, f"skipped:{pid}:demoted")
        return body, demotions.lower(mode, cap)
    except Exception as exc:                                  # type only; a bad file never stops the others
        return log(f"skipped:{pid}:invalid:{type(exc).__name__}")


def load_approved(cfg, log) -> list[tuple[dict, str]]:
    """[(proposal, mode)] for every approved file that is a regular, small, hash-matching, valid proposal."""
    base = approved_dir(cfg)
    if base.parent.is_symlink() or base.is_symlink():
        log("approved_dir_symlink")
        return []
    try:
        names = sorted(n for n in os.listdir(base) if n.endswith(".json"))
    except OSError:
        return []
    if len(names) > MAX_FILES:
        log("skipped:too_many_files")
    out, appr, dem = [], _Approvals(cfg, log), (_demotions(cfg, log) if names else ([], None))
    for name in names[:MAX_FILES]:
        if not proposal.PID.fullmatch(name[:-5]):
            log("skipped:file_name")
            continue
        item = _load_one(cfg, base / name, name[:-5], log, appr, dem)
        if item:
            out.append(item)
    return out


def approved_records(cfg) -> list[tuple[dict, str, str]]:
    """[(proposal, mode, approved_at)] of the approved copies whose hash holds; not re-validated (the offline pass only lowers)."""
    base, out = approved_dir(cfg), []
    for p in sorted(base.glob("*.json")) if base.is_dir() and not base.is_symlink() else []:
        try:
            rec = proposal.load(p)
            body, mode = pending_body(rec)
        except (ValueError, OSError):
            continue
        if body is not None and body["id"] == p.stem:
            out.append((body, mode, rec["approved_at"]))
    return out


# ---------------------------------------------------------------- files
def _append(path: Path, text: str, cap: int | None = None, sync: bool = False) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("not a regular file")
        if cap is None or info.st_size < cap:
            os.write(fd, text.encode())
            if sync:                                          # the offline pass; the hook never waits for a disk flush
                os.fsync(fd)
    finally:
        os.close(fd)


def _log(cfg, msg: str) -> None:
    try:
        _append(Path(cfg.data_dir) / "shadow.log", f"{now_iso()} {msg}\n", LOG_CAP)
    except Exception:
        pass


# ---------------------------------------------------------------- evaluation
def _evaluate(cfg, ev, sid: str, names: list[str], log) -> set[str]:
    """Detector names that fired. State is advanced under the step 1 lock; if that is skipped, only detectors
    that need no state still run (a stateful one on a blank state would fire falsely)."""
    outs = []
    def step(s: dict) -> dict:                                # noqa: E306
        outs.append(catalogue.evaluate(ev, s, names=names, strict=False))
        return outs[-1][1]

    _, status = state_mod.update(cfg.data_dir, sid, step)
    if status != "ok":
        log(status)
        names = [n for n in names if catalogue.CATALOGUE[n].state == "none"]
        if names:
            outs.append(catalogue.evaluate(ev, None, names=names, strict=False))
    for name, kind in (e for out in outs for e in out[2]):
        log(f"error:{name}:{kind}")
    return {f.detector for out in outs for f in out[0]}


def _applies(p: dict, event: str, tool: str) -> bool:
    return p["event"] == event and bool({"*", tool} & set(p["matcher"].split("|")))


def _row(p: dict, mode: str, cause: str, event: str, ev, sid: str, raw: bytes) -> dict:
    tu = ev.tool_uses[0] if ev.tool_uses else None
    phash = evidence.payload_hash({"tool_name": tu.name, "tool_input": tu.input}) if tu else hashlib.sha256(raw).hexdigest()
    return {"ts": now_iso(), "proposal_id": p["id"], "lesson_id": p["lesson_id"], "mode": mode, "event": event,
            "tool_name": tu.name if tu else "", "session": state_mod.session_hash(sid), "payload_sha256": phash,
            "would_fire": True, "cause": cause, "tier": "live"}


def _run(cfg, argv: list, log) -> None:
    event = EVENTS.get(argv[0]) if argv else None
    if event is None:
        return
    raw = sys.stdin.buffer.read(MAX_STDIN + 1)
    if len(raw) > MAX_STDIN:
        return log("skipped:oversize")
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        return
    sid = payload.get("session_id") if isinstance(payload, dict) else None
    if not (isinstance(sid, str) and sid) or (Path(cfg.data_dir) / proposal.PROPOSALS / "DISABLED").exists():
        return
    ev = event_from_hook({**payload, "hook_event_name": event})
    tool = ev.tool_uses[0].name if ev.tool_uses else ""
    todo = [(p, m) for p, m in load_approved(cfg, log) if _applies(p, event, tool)]
    if not todo:
        return
    fired = _evaluate(cfg, ev, sid, sorted({p["check"]["detector"] for p, _ in todo}), log)
    cut = any(tu.truncated for tu in ev.tool_uses)
    hits = [(p, m, "detector" if p["check"]["detector"] in fired else "truncated") for p, m in todo
            if p["check"]["detector"] in fired or (cut and p["check"]["on_truncated"] == "fire")]
    for p, mode, cause in hits:
        try:
            _append(Path(cfg.data_dir) / "shadow.jsonl",
                    json.dumps(_row(p, mode, cause, event, ev, sid, raw), sort_keys=True) + "\n")
        except OSError as exc:                                # still act on the decision below
            log(f"error:row:{type(exc).__name__}")
    warns = [p["message"] for p, mode, _cause in hits if mode == "warn"]
    for msg in warns:                                         # the user-visible echo; the model never sees stderr
        sys.stderr.write(msg + "\n")
    block = next((p["message"] for p, mode, _cause in hits if mode == "block"), None)
    out = None
    if event == "Stop" and payload.get("stop_hook_active"):   # a loop guard: nothing on stdout, fires still recorded
        pass
    elif block is not None:                                   # block wins over warn
        out = ({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": block}}
               if event == "PreToolUse" else {"decision": "block", "reason": block})
    elif warns:                                               # the model-visible channel (design 11 question 1)
        out = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(warns)}}
    if out is not None:
        sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")


def main(argv: list, cfg=None) -> int:
    """Hook entry. Returns 0 whatever happens: exit 2 would mean "block" to the harness."""
    log = lambda msg: None                                    # noqa: E731
    try:
        cfg = cfg or cfgmod.load()
        log = lambda msg: _log(cfg, msg)                      # noqa: E731
        _run(cfg, list(argv), log)
    except BaseException as exc:                              # fail open, type only
        log(f"error:{type(exc).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
