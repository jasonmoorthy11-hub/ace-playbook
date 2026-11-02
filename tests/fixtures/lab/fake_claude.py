#!/usr/bin/env python3
"""A FAKE `claude -p` for the lab tests (modelled on tests/fixtures/brief/fake_claude.py). Synthetic data only.

Reads stream-json user lines from stdin; for each one prints the scripted events of the next turn, then its `result`, and flushes. The
script is the JSON file named by $ACE_FAKE_SCENARIO: {"turns": [{"events": [...], "result": {...}, "hang": bool}, ...], "exit": 0,
"ignore_term": false, "sleep": 0.0}; a turn with "die" exits 3 before its result; a turn beyond the list repeats the last one, and
no file means one empty successful turn.
Records into $ACE_FAKE_DIR: argv.json, env.json (flags of interest only), settings.json (a copy of the --settings file), stdin_lines.json
(every line received), pid, and cwd.txt. It exits with the scenario's code once stdin closes.
"""

import json
import os
import signal
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
root = Path(os.environ["ACE_FAKE_DIR"])
scenario = json.loads(Path(os.environ["ACE_FAKE_SCENARIO"]).read_text()) if os.environ.get("ACE_FAKE_SCENARIO") else {}
if scenario.get("ignore_term"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
(root / "argv.json").write_text(json.dumps(argv))
(root / "pid").write_text(str(os.getpid()))
(root / "cwd.txt").write_text(os.getcwd())
(root / "env.json").write_text(json.dumps({"has_api_key": "ANTHROPIC_API_KEY" in os.environ, "has_claudecode": "CLAUDECODE" in os.environ,
                                           **{k: os.environ.get(k) for k in ("HOME", "USER", "LOGNAME", "PATH")}}))
if "--settings" in argv:
    (root / "settings.json").write_text(Path(argv[argv.index("--settings") + 1]).read_text())
time.sleep(float(scenario.get("sleep", 0)))
turns, received = scenario.get("turns") or [{}], []


def emit(events):
    print("\n".join(json.dumps(e) for e in events), flush=True)


emit([{"type": "system", "subtype": "init", "cwd": os.getcwd(), "session_id": "fake", "model": argv[argv.index("--model") + 1]}])
for line in sys.stdin:
    if not line.strip():
        continue
    received.append(line.rstrip("\n"))
    (root / "stdin_lines.json").write_text(json.dumps(received))
    turn = turns[min(len(received) - 1, len(turns) - 1)]
    emit(turn.get("events", []))
    if turn.get("hang"):
        time.sleep(600)
    if turn.get("die"):
        sys.exit(3)
    emit([{"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, **turn.get("result", {})}])
sys.exit(scenario.get("exit", 0))
