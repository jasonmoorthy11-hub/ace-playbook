#!/usr/bin/env python3
"""A FAKE `claude -p` for the brief.sh tests. Stage 2 is the call that passes `--tools ""`; stage 1 is any other call.

Reads $ACE_FAKE_DIR: digest.json (the stage 1 answer), brief.json (the stage 2 answer); writes argv_<stage>.json, env_<stage>.json and
stdin_stage2.txt there so the test can see what the script passed. $ACE_FAKE_FAIL = stage1 | stage2 makes that stage exit 1 with an error
result; $ACE_FAKE_SLEEP sleeps first (and pid_<stage> records the pid; $ACE_FAKE_SLEEP_ONLY limits it to one stage); $ACE_FAKE_INIT_TOOLS
(comma separated) is the `tools` list of the init event; $ACE_FAKE_CALLS (a JSON list of names) adds a stage 1 tool_use block for each name;
$ACE_FAKE_IGNORE_TERM ignores SIGTERM; $ACE_FAKE_FENCE wraps the answer in a ```json fence. The stage 1 init event lists the three read
connectors as connected; $ACE_FAKE_MCP (a JSON list) replaces that `mcp_servers` list on every stage 1 call and $ACE_FAKE_MCP_FIRST on the FIRST
stage 1 call only; $ACE_FAKE_NO_CALLS drops the default Gmail tool_use block (on every stage 1 call; $ACE_FAKE_NO_CALLS_FIRST on the first
only); $ACE_FAKE_FAIL_SECOND = stage1 makes the second stage 1 call fail. `stage1_calls` in $ACE_FAKE_DIR counts the stage 1 calls.
$ACE_FAKE_CALLS_RESULT = ok | error adds, after each $ACE_FAKE_CALLS block (ids c0, c1, ...), a tool_result event for it (`is_error` true for error; without
the knob a $ACE_FAKE_CALLS block has no result); $ACE_FAKE_DEFAULT_ERROR makes the result of the default Gmail call `is_error: true`. Synthetic data only.
"""

import json
import os
import signal
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
if os.environ.get("ACE_FAKE_IGNORE_TERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
stage = "stage2" if "--tools" in argv and argv[argv.index("--tools") + 1] == "" else "stage1"
root = Path(os.environ["ACE_FAKE_DIR"])
(root / f"argv_{stage}.json").write_text(json.dumps(argv))
(root / f"pid_{stage}").write_text(str(os.getpid()))
if os.environ.get("ACE_FAKE_SLEEP_ONLY", stage) == stage:
    time.sleep(float(os.environ.get("ACE_FAKE_SLEEP", "0")))
(root / f"env_{stage}.json").write_text(json.dumps({"has_api_key": "ANTHROPIC_API_KEY" in os.environ, "has_claudecode": "CLAUDECODE" in os.environ,
                                                    "repo": os.environ.get("ACE_PLAYBOOK_REPO"), "cwd": os.getcwd()}))
nth = 0
if stage == "stage1":
    counter = root / "stage1_calls"
    nth = (int(counter.read_text()) if counter.exists() else 0) + 1
    counter.write_text(str(nth))
stdin = sys.stdin.read() if stage == "stage2" else ""
if stage == "stage2":
    (root / "stdin_stage2.txt").write_text(stdin)
answer = (root / ("digest.json" if stage == "stage1" else "brief.json")).read_text()
if os.environ.get("ACE_FAKE_FENCE"):
    answer = "```json\n" + answer + "\n```"
window = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.08 if stage == "stage1" else 0.09, "resetsAt": 1},
                                                  "seven_day": {"utilization": 0.01, "resetsAt": 2}}}
failing = os.environ.get("ACE_FAKE_FAIL") == stage or (os.environ.get("ACE_FAKE_FAIL_SECOND") == stage and nth == 2)
CONNECTED = [{"name": n, "status": "connected", "source": "claude.ai"} for n in ("claude.ai Gmail", "claude.ai Google Calendar", "claude.ai Linear")]
override = os.environ.get("ACE_FAKE_MCP_FIRST" if nth == 1 and "ACE_FAKE_MCP_FIRST" in os.environ else "ACE_FAKE_MCP")
servers = CONNECTED if override is None else json.loads(override)
events = [{"type": "system", "subtype": "init", "model": argv[argv.index("--model") + 1], "mcp_servers": servers if stage == "stage1" else [],
           "tools": [t for t in os.environ.get("ACE_FAKE_INIT_TOOLS", "").split(",") if t]}]
if stage == "stage1":
    if not (os.environ.get("ACE_FAKE_NO_CALLS") or (nth == 1 and os.environ.get("ACE_FAKE_NO_CALLS_FIRST"))):
        events += [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__claude_ai_Gmail__search_threads",
                                                                  "input": {}}]}},
                   {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "FAKE-MAIL-BODY-IN-THE-STREAM",
                                                                   **({"is_error": True} if os.environ.get("ACE_FAKE_DEFAULT_ERROR") else {})}]}}]
    verdict = os.environ.get("ACE_FAKE_CALLS_RESULT")
    for i, n in enumerate(json.loads(os.environ.get("ACE_FAKE_CALLS", "[]"))):
        events.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"c{i}", "name": n, "input": {}}]}})
        if verdict:
            events.append({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"c{i}", "content": "FAKE-BODY",
                                                                    "is_error": verdict == "error"}]}})
events += [{"type": "rate_limit_event", "rate_limit_info": window},
           {"type": "result", "subtype": "error_during_execution" if failing else "success", "is_error": failing, "result": "" if failing else answer,
            "total_cost_usd": 0.2424 if stage == "stage1" else 0.012, "duration_ms": 1000, "num_turns": 3 if stage == "stage1" else 1,
            "usage": {"input_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 200, "output_tokens": 50},
            "modelUsage": {argv[argv.index("--model") + 1]: {"costUSD": 0.01}}}]
print("\n".join(json.dumps(e) for e in events))
sys.exit(1 if failing else 0)
