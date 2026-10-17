"""SessionEnd helper: append the session to a queue file and return immediately.

SessionEnd's default budget is 1.5 s, so this does no cleaning, scrubbing or API
work. Imports are kept minimal; a nightly sweep drains the queue.
"""

from __future__ import annotations

import json
import os
import sys
import time

from . import config as cfgmod


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        cfg = cfgmod.load()
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "session_id": payload.get("session_id", ""),
               "transcript_path": payload.get("transcript_path", ""),
               "reason": payload.get("reason", "")}
        fd = os.open(cfg.queue, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(rec, sort_keys=True) + "\n").encode())
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 - never block session exit
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
