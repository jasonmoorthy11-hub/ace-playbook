#!/usr/bin/env python3
"""A FAKE `claude -p` for the brief.sh tests. Stage 2 is the call that passes `--tools ""`; stage 1 is any other call.

Reads $ACE_FAKE_DIR: digest.json (the stage 1 answer), brief.json (the stage 2 answer); writes argv_<stage>.json, env_<stage>.json and
stdin_stage2.txt there so the test can see what the script passed. $ACE_FAKE_FAIL = stage1 | stage2 makes that stage exit 1 with an error
result; $ACE_FAKE_SLEEP sleeps first (and pid_<stage> records the pid); $ACE_FAKE_FENCE wraps the answer in a ```json fence. Synthetic data only.
"""

import json
import os
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
stage = "stage2" if "--tools" in argv and argv[argv.index("--tools") + 1] == "" else "stage1"
root = Path(os.environ["ACE_FAKE_DIR"])
(root / f"argv_{stage}.json").write_text(json.dumps(argv))
(root / f"pid_{stage}").write_text(str(os.getpid()))
time.sleep(float(os.environ.get("ACE_FAKE_SLEEP", "0")))
(root / f"env_{stage}.json").write_text(json.dumps({"has_api_key": "ANTHROPIC_API_KEY" in os.environ, "has_claudecode": "CLAUDECODE" in os.environ,
                                                    "repo": os.environ.get("ACE_PLAYBOOK_REPO"), "cwd": os.getcwd()}))
stdin = sys.stdin.read() if stage == "stage2" else ""
if stage == "stage2":
    (root / "stdin_stage2.txt").write_text(stdin)
answer = (root / ("digest.json" if stage == "stage1" else "brief.json")).read_text()
if os.environ.get("ACE_FAKE_FENCE"):
    answer = "```json\n" + answer + "\n```"
window = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.08 if stage == "stage1" else 0.09, "resetsAt": 1},
                                                  "seven_day": {"utilization": 0.01, "resetsAt": 2}}}
failing = os.environ.get("ACE_FAKE_FAIL") == stage
events = [{"type": "system", "subtype": "init", "model": argv[argv.index("--model") + 1], "tools": [], "mcp_servers": []}]
if stage == "stage1":
    events += [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__claude_ai_Gmail__search_threads", "input": {}}]}},
               {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "FAKE-MAIL-BODY-IN-THE-STREAM"}]}}]
events += [{"type": "rate_limit_event", "rate_limit_info": window},
           {"type": "result", "subtype": "error_during_execution" if failing else "success", "is_error": failing, "result": "" if failing else answer,
            "total_cost_usd": 0.2424 if stage == "stage1" else 0.012, "duration_ms": 1000, "num_turns": 3 if stage == "stage1" else 1,
            "usage": {"input_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 200, "output_tokens": 50},
            "modelUsage": {argv[argv.index("--model") + 1]: {"costUSD": 0.01}}}]
print("\n".join(json.dumps(e) for e in events))
sys.exit(1 if failing else 0)
