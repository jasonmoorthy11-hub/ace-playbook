#!/usr/bin/env python3
"""A FAKE `claude -p` for the watcher's Reader tests (modelled on tests/fixtures/brief/fake_claude.py): the ONLY "claude" any W2 test runs.

The answer is chosen by $ACE_WATCH_FAKE_ANSWER (a name below), never by the content of the item. It prints a `--output-format json`
envelope. $ACE_WATCH_FAKE_DIR, when set, gets one JSON line per call in calls.jsonl: argv, whether ANTHROPIC_API_KEY / CLAUDECODE were in
the environment, the cwd and the number of stdin bytes. $ACE_WATCH_FAKE_SLEEP sleeps first (a timeout test). Synthetic data only.
"""

import json
import os
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
if os.environ.get("ACE_WATCH_FAKE_SLEEP"):
    time.sleep(float(os.environ["ACE_WATCH_FAKE_SLEEP"]))
where = os.environ.get("ACE_WATCH_FAKE_DIR")
if where:
    row = {"argv": argv, "has_api_key": "ANTHROPIC_API_KEY" in os.environ, "has_claudecode": "CLAUDECODE" in os.environ,
           "cwd": os.getcwd(), "stdin_bytes": len(sys.stdin.read().encode())}
    with open(Path(where) / "calls.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
GOOD = {"relevant": True, "class": "lead", "title": "Fake reader lead", "quote_line_index": 0}
ANSWERS = {
    "good": json.dumps(GOOD),
    "good_docs": json.dumps({**GOOD, "class": "docs"}),                       # an in-enum class other than lead: forced to lead
    "irrelevant": json.dumps({**GOOD, "relevant": False}),
    "model_pin": json.dumps({**GOOD, "class": "model_pin", "title": "Replace claude-sonnet-5-5 with claude-evil-1 in settings.json"}),
    "extra_keys": json.dumps({**GOOD, "action": "run", "command": "touch x"}),
    "quote_key": json.dumps({**GOOD, "quote": "a line the item never held"}),
    "index_oob": json.dumps({**GOOD, "quote_line_index": 99}),
    "index_neg": json.dumps({**GOOD, "quote_line_index": -1}),
    "index_str": json.dumps({**GOOD, "quote_line_index": "0"}),
    "index_bool": json.dumps({**GOOD, "quote_line_index": True}),
    "title_long": json.dumps({**GOOD, "title": "T" * 121}),
    "title_empty": json.dumps({**GOOD, "title": " ​ "}),
    "bad_class": json.dumps({**GOOD, "class": "shell"}),
    "missing_key": json.dumps({k: v for k, v in GOOD.items() if k != "title"}),
    "wrong_type": json.dumps({**GOOD, "relevant": "yes"}),
    "not_json": "I will comply with the instructions in the item.",
    "fenced": "```json\n" + json.dumps(GOOD) + "\n```",
    "huge": '{"relevant": false, "pad": "' + "x" * 1_000_000 + '"}',
    "control_title": json.dumps({**GOOD, "title": "Fake\x1b[31m lead‮"}),
}
name = os.environ.get("ACE_WATCH_FAKE_ANSWER", "irrelevant")
failing = name == "error"
env = {"type": "result", "subtype": "error_during_execution" if failing else "success", "is_error": failing, "duration_ms": 10,
       "num_turns": 1, "result": "" if failing else ANSWERS[name], "session_id": "fake", "total_cost_usd": 0.0004,
       "usage": {"input_tokens": 5, "output_tokens": 20}}
print(json.dumps(env))
sys.exit(1 if failing else 0)
