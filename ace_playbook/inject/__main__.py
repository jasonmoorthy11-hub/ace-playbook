"""Hook entry points: `python -m ace_playbook.inject [start|cite]` reads hook JSON on stdin.

`--health` is the exception: it reads no stdin, prints the nightly sweep's health.json (or
"no health record") and exits; it never touches the API key or the store.

Both modes fail open: any error is logged by type only and the hook exits 0
without output, so a broken playbook never blocks a session.
"""

from __future__ import annotations

import json
import sys
import time

from .. import config as cfgmod
from .. import health
from . import injector
from .citations import record_citations


def _log_error(cfg, where: str, exc: Exception) -> None:
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        with open(cfg.log, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {where} "
                     f"ERROR {type(exc).__name__}\n")
    except OSError:
        pass


def _session_id(payload: dict) -> str | None:
    sid = payload.get("session_id")
    return sid if isinstance(sid, str) and sid.strip() else None


def start(cfg, payload: dict) -> int:
    sid = _session_id(payload)
    if sid is None:  # no id means the ledger and holdout draw cannot be attributed
        _log_error(cfg, "inject.start", ValueError("missing session_id"))
        return 0
    inj = injector.run(cfg, sid, payload.get("cwd", "."), payload.get("source", "startup"))
    if inj.block:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": inj.block}}))
    return 0


def cite(cfg, payload: dict) -> int:
    sid = _session_id(payload)
    if sid is None:
        _log_error(cfg, "inject.cite", ValueError("missing session_id"))
        return 0
    record_citations(cfg, sid, payload.get("last_assistant_message", ""))
    return 0


def show_health(cfg) -> int:
    rec = health.read(cfg)
    print(json.dumps(rec, indent=2, sort_keys=True) if rec is not None else health.NO_RECORD)
    return 0


def main(argv: list[str] | None = None) -> int:
    mode = (argv or sys.argv[1:] or ["start"])[0]
    if mode == "--health":
        try:
            return show_health(cfgmod.load())
        except Exception as exc:  # noqa: BLE001 - a human is reading: say what failed, by type only
            print(f"health unavailable: {type(exc).__name__}", file=sys.stderr)
            return 1
    cfg = None
    try:
        cfg = cfgmod.load()
        payload = json.loads(sys.stdin.read() or "{}")
        return {"start": start, "cite": cite}[mode](cfg, payload)
    except Exception as exc:  # noqa: BLE001 - hooks must fail open
        if cfg is not None:
            _log_error(cfg, f"inject.{mode}", exc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
