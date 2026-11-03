#!/usr/bin/env python3
"""A FAKE `claude -p` for the lab tests (modelled on tests/fixtures/brief/fake_claude.py). Synthetic data only.

Reads stream-json user lines from stdin; for each one prints the scripted events of the next turn, then its `result`, and flushes. The
script is the JSON file named by $ACE_FAKE_SCENARIO: {"turns": [{"events": [...], "result": {...}, "hang": bool}, ...], "exit": 0,
"ignore_term": false, "sleep": 0.0, "grandchild": false}; a turn with "die" exits 3 before its result; a turn beyond the list repeats the
last one, and no file means one empty successful turn. "grandchild" starts a child that ignores SIGTERM, keeps stdout open and records its
pid in $ACE_FAKE_DIR/grandchild_pid.
Before it prints each `result` it checks, with a zero-timeout select, that no further stdin line is already waiting (the driver must write
turn k+1 only after turn k's result); if one is, it exits 97 with a message on stderr.
Control protocol (exitplan): a stdin line of type control_request (the initialize handshake) is answered with a success control_response and is
not a turn. A turn may carry "control": [{"tool_name": ..., "input": {...}} or {"subtype": ...}]; each entry is printed as a control_request
(ids req-1, req-2, ...) and the fake blocks for the control_response (exit 96 if the next line is anything else) before it prints the turn's
events and result. "control_nowait" prints the requests and exits 3 without reading.
Records into $ACE_FAKE_DIR: argv.json, env.json (flags of interest only), settings.json (a copy of the --settings file), stdin_lines.json
(the user lines), stdin_all.json (every line, in order), control_responses.json, pid, and cwd.txt. It exits with the scenario's code once
stdin closes.
"""

import json
import os
import select
import signal
import subprocess
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
if scenario.get("grandchild"):
    gc = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"])
    (root / "grandchild_pid").write_text(str(gc.pid))

_buf, _eof = bytearray(), False


def emit(events):
    print("\n".join(json.dumps(e) for e in events), flush=True)


def _fill(block):
    """Read raw bytes from fd 0 (no Python-level buffering, so select sees what is really waiting)."""
    global _eof
    if _eof or (not block and not select.select([0], [], [], 0)[0]):
        return
    data = os.read(0, 65536)
    if data:
        _buf.extend(data)
    else:
        _eof = True


def next_line():
    while b"\n" not in _buf:
        if _eof:
            return None
        _fill(True)
    i = _buf.index(b"\n")
    line = bytes(_buf[:i]).decode()
    del _buf[:i + 1]
    return line


def check_nothing_waiting():
    _fill(False)
    if _buf:
        sys.stderr.write("fake claude: a further stdin line was already waiting before the result was printed\n")
        sys.stderr.flush()
        os._exit(97)


everything, answers, requests = [], [], [0]


def record_all(line):
    everything.append(line)
    (root / "stdin_all.json").write_text(json.dumps(everything))


def control_exchange(turn):
    """Print each scripted control_request ({"tool_name", "input"} -> can_use_tool; {"subtype"} -> any other) and, unless the turn says
    "control_nowait", block until the control_response arrives (recorded in control_responses.json)."""
    for entry in turn.get("control", []):
        requests[0] += 1
        rid = f"req-{requests[0]}"
        req = {"subtype": entry["subtype"]} if "subtype" in entry else {"subtype": "can_use_tool", "tool_name": entry["tool_name"],
                                                                         "input": entry.get("input", {}), "tool_use_id": f"tu-{rid}"}
        emit([{"type": "control_request", "request_id": rid, "request": req}])
        if turn.get("control_nowait"):
            continue
        got = next_line()
        record_all(got)
        msg = json.loads(got) if got else {}
        if msg.get("type") != "control_response":
            sys.stderr.write("fake claude: expected a control_response\n")
            sys.exit(96)
        answers.append(msg)
        (root / "control_responses.json").write_text(json.dumps(answers))
    if turn.get("control_nowait") and turn.get("control"):
        sys.exit(3)


emit([{"type": "system", "subtype": "init", "cwd": os.getcwd(), "session_id": "fake", "model": argv[argv.index("--model") + 1]}])
while True:
    line = next_line()
    if line is None:
        break
    if not line.strip():
        continue
    record_all(line)
    try:
        msg = json.loads(line)
    except ValueError:
        msg = None
    if isinstance(msg, dict) and msg.get("type") == "control_request":                    # the initialize handshake
        emit([{"type": "control_response", "response": {"subtype": "success", "request_id": msg.get("request_id"), "response": {}}}])
        continue
    received.append(line)
    (root / "stdin_lines.json").write_text(json.dumps(received))
    turn = turns[min(len(received) - 1, len(turns) - 1)]
    control_exchange(turn)
    emit(turn.get("events", []))
    if turn.get("hang"):
        time.sleep(600)
    if turn.get("die"):
        sys.exit(3)
    check_nothing_waiting()
    emit([{"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, **turn.get("result", {})}])
sys.exit(scenario.get("exit", 0))
