"""Hook entry points: `python -m ace_playbook.inject [start|cite]` reads hook JSON on stdin.

Both modes fail open: any error is logged by type only and the hook exits 0
without output, so a broken playbook never blocks a session.
"""

from __future__ import annotations

import json
import sys
import time

from .. import config as cfgmod
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


def start(cfg, payload: dict) -> int:
    inj = injector.run(cfg, payload.get("session_id", "unknown"), payload.get("cwd", "."),
                       payload.get("source", "startup"))
    if inj.block:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": inj.block}}))
    return 0


def cite(cfg, payload: dict) -> int:
    record_citations(cfg, payload.get("session_id", "unknown"),
                     payload.get("last_assistant_message", ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    mode = (argv or sys.argv[1:] or ["start"])[0]
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
