"""The shadow hook (docs/design/compiler.md 7): evaluate approved proposals on one live hook payload.

hooks/shadow.sh calls `main(["pre"|"stop"])`: one payload on stdin (cap 256 KiB), the approved proposals re-validated, their detectors run, one
`shadow.jsonl` row per fire; ALWAYS returns 0, failures are type-only lines in `shadow.log`. Observe prints nothing; warn echoes the message on stderr
and prints ONE `hookSpecificOutput.additionalContext` JSON on stdout (verified live for PreToolUse; Stop per the hooks reference only); block prints
the deny/block JSON and wins. `modes.resolve` decides the mode: a copy above observe needs a matching `approvals.jsonl` row (else observe, logged), a
revoked proposal is skipped, `demotions.jsonl` lowers it until a newer approval lifts the older rows. Both chains are read with the chain key (chains.py): without it a chain
holding a `v: 2` row is broken, logged once as `chain_key:missing`. Rows hold enums, ids, hashes, a timestamp and `dur_ms` (monotonic ms from the call's start to the
row write), never payload text. Stdlib plus this package; no inject/keys/reflect/network.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path

from .. import config as cfgmod
from ..canon import canonical, now_iso
from . import approvals, catalogue, chains, demotions, evidence, modes, proposal
from . import state as state_mod
from .events import event_from_hook

MAX_STDIN, MAX_FILES, MAX_FILE_BYTES, LOG_CAP = 262144, 50, 16384, 1000000
APPROVED, EVENTS, EXTRA_KEYS = "approved", {"pre": "PreToolUse", "stop": "Stop"}, {"approved_at", "proposal_sha256"}
MODES = ALLOWED_MODES = ("observe", "warn", "block")   # warn and block also need a matching approvals.jsonl row (7.3)


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
    if rec["reviewed_by"] != "operator-tty" or rec["mode"] not in MODES or not (
            isinstance(rec["approved_at"], str) and proposal.STAMP.fullmatch(rec["approved_at"])):
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
    base, path = approved_dir(cfg), approved_dir(cfg) / (pid + ".json")
    if base.is_symlink() or (base.exists() and not base.is_dir()) or path.is_symlink() or path.parent.resolve() != base.resolve():
        raise ValueError("approved directory or file is a symlink, or the path escapes the approved directory")
    return path


class _Approvals:
    """The approvals chain, read at most once per hook call (and on every call: a revocation must stop a copy)."""
    def __init__(self, cfg, log, ring: chains.Keyring | None = None) -> None:
        self.cfg, self.log, self.ring, self.loaded, self.rows, self.broken = cfg, log, ring, False, [], None

    def read(self) -> tuple[list[dict], modes.Broken]:
        """(rows, broken_at): a read error is logged (type only, not throttled: `error:` lines feed the error-rate rule) and counts as `unreadable`."""
        if not self.loaded:
            self.loaded = True
            try:
                self.rows, self.broken = approvals.read_chain(self.cfg.data_dir, self.ring)
            except Exception as exc:
                self.log(f"error:approvals:{type(exc).__name__}")
                self.rows, self.broken = [], "unreadable"
        return self.rows, self.broken


def _tail(cfg, n: int = 8192) -> str:
    """The last `n` bytes of shadow.log (room for MAX_FILES repeated lines), '' when it is missing or not a regular file."""
    try:
        with os.fdopen(os.open(Path(cfg.data_dir) / "shadow.log", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as fh:
            info = os.fstat(fh.fileno())
            fh.seek(max(0, info.st_size - n))
            return fh.read(n).decode("utf-8", "replace") if stat.S_ISREG(info.st_mode) else ""
    except OSError:
        return ""


class _Once:
    """A repeating log line is written only if it is not in the log's last 8 KiB, read ONCE per hook call (so it cannot fill the log)."""
    def __init__(self, cfg, log) -> None:
        self.cfg, self.log, self.tail = cfg, log, None

    def __call__(self, msg: str) -> None:
        self.tail = _tail(self.cfg) if self.tail is None else self.tail
        if msg not in self.tail:
            self.log(msg)
            self.tail += "\n" + msg + "\n"


def _demotions(cfg, log, ring: chains.Keyring | None = None) -> tuple[list[dict], modes.Broken]:
    """(rows, broken_at) of demotions.jsonl, once per call; a read error is logged (type only, unthrottled) and is `unreadable`."""
    try:
        return demotions.read_chain(cfg.data_dir, ring)
    except Exception as exc:
        log(f"error:demotions:{type(exc).__name__}")
        return [], "unreadable"


def _problem(pid: str, problem: str, once) -> None:
    """Log one resolver problem once: `approvals:<id>:missing|mismatch` per proposal, the chain ones as they are."""
    if problem.startswith("approvals:") and problem not in ("approvals:chain_broken", "approvals:head_mismatch"):
        once(f"approvals:{pid}:{problem[10:]}")
    elif problem not in ("revoked", "no_copy"):
        once(problem)


def _load_one(cfg, path: Path, pid: str, log, once, appr: _Approvals, dem, now: str) -> tuple[dict, str] | None:
    try:
        if os.lstat(path).st_size > MAX_FILE_BYTES:
            return once(f"skipped:{pid}:oversize")
        rec = proposal.load(path)
        body, mode = pending_body(rec)
        if body is None or body["id"] != pid:
            return once(f"skipped:{pid}:invalid:{mode if body is None else 'id'}")
        if mode not in ALLOWED_MODES:
            return once(f"skipped:{pid}:mode_disabled")
        rep = proposal.validate(body, cfg, hot=True)
        if not rep.ok:
            return once(f"skipped:{pid}:invalid:{rep.failed}")
        res = modes.resolve(pid, rec, *appr.read(), *dem, now)       # warn and block need a matching approvals row
        for problem in res.problems:
            _problem(pid, problem, once)
        if res.mode == "disabled":
            return once(f"skipped:{pid}:{'revoked' if 'revoked' in res.problems else 'demoted'}")
        return body, res.mode
    except Exception as exc:                                  # type only; a bad file never stops the others
        return once(f"skipped:{pid}:invalid:{type(exc).__name__}")


def load_approved(cfg, log) -> list[tuple[dict, str]]:
    """[(proposal, mode)] for every approved file that is a regular, small, hash-matching, valid proposal."""
    base, once = approved_dir(cfg), _Once(cfg, log)
    if base.parent.is_symlink() or base.is_symlink():
        return once("approved_dir_symlink") or []
    try:
        names = sorted(n for n in os.listdir(base) if n.endswith(".json"))
    except OSError:
        return []
    if len(names) > MAX_FILES:
        once("skipped:too_many_files")
    out, appr, dem, now = [], _Approvals(cfg, log, ring := chains.key_of(cfg)), (_demotions(cfg, log, ring) if names else ([], None)), now_iso()
    for name in names[:MAX_FILES]:
        if not proposal.PID.fullmatch(name[:-5]):
            once("skipped:file_name")
            continue
        item = _load_one(cfg, base / name, name[:-5], log, once, appr, dem, now)
        if item:
            out.append(item)
    for note in chains.notes(ring, (appr.broken, dem[1])):
        once(note)                                            # chain_key:missing, chain_heads:missing
    return out


def approved_records(cfg) -> list[tuple[dict, str, dict]]:
    """[(proposal, mode, approved record)] of the approved copies whose hash holds; not re-validated (the offline pass only lowers)."""
    base, out = approved_dir(cfg), []
    for p in sorted(base.glob("*.json")) if base.is_dir() and not base.is_symlink() else []:
        try:
            rec = proposal.load(p)
            body, mode = pending_body(rec)
        except (ValueError, OSError):
            continue
        if body is not None and body["id"] == p.stem:
            out.append((body, mode, rec))
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
    """Detector names that fired; state advances under the step 1 lock, and if that is skipped only stateless detectors run."""
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


def _row(p: dict, mode: str, cause: str, event: str, ev, sid: str, raw: bytes, t0: float) -> dict:
    tu = ev.tool_uses[0] if ev.tool_uses else None
    phash = evidence.payload_hash({"tool_name": tu.name, "tool_input": tu.input}) if tu else hashlib.sha256(raw).hexdigest()
    return {"v": 1, "ts": now_iso(), "proposal_id": p["id"], "lesson_id": p["lesson_id"], "mode": mode, "event": event,
            "tool_name": tu.name if tu else "", "session": state_mod.session_hash(sid), "payload_sha256": phash,
            "would_fire": True, "cause": cause, "tier": "live", "dur_ms": int((time.monotonic() - t0) * 1000)}


def _run(cfg, argv: list, log) -> None:
    t0 = time.monotonic()
    if (event := EVENTS.get(argv[0]) if argv else None) is None:
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
                    json.dumps(_row(p, mode, cause, event, ev, sid, raw, t0), sort_keys=True) + "\n")
        except OSError as exc:                                # still act on the decision below
            log(f"error:row:{type(exc).__name__}")
    if warns := [p["message"] for p, mode, _cause in hits if mode == "warn"]:       # the user-visible echo; the model never sees stderr
        sys.stderr.write("".join(m + "\n" for m in warns))
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
