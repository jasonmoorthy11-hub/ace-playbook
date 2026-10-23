#!/usr/bin/env python3
"""A FAKE `claude -p` for the watcher's Reader tests (modelled on tests/fixtures/brief/fake_claude.py): the ONLY "claude" any W2 test runs.

The answer is chosen by $ACE_WATCH_FAKE_ANSWER (a name below), never by the content of the item. It prints a `--output-format json`
envelope. $ACE_WATCH_FAKE_DIR, when set, gets one JSON line per call in calls.jsonl: argv, whether ANTHROPIC_API_KEY / CLAUDECODE were in
the environment, the cwd, the number of stdin bytes and which of the five ANTHROPIC_* / CLAUDE* auth variables it saw. $ACE_WATCH_FAKE_SLEEP
sleeps first (a timeout test).
$ACE_WATCH_FAKE_SEQ ("iserror,good") names the answer of each call in turn (the last one repeats). Failing names: error (exit 1 and
is_error), iserror (is_error, exit 0), exit1, empty, garbage, hang. $ACE_WATCH_FAKE_POISON=<text> makes the call exit 1 whenever the prompt
(the item) contains that text, whatever the answer name says (a crafted item the model cannot read). Synthetic data only.
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
log = Path(where) / "calls.jsonl" if where else None
prior = len(log.read_text().splitlines()) if log is not None and log.exists() else 0        # calls made before this one (for ACE_WATCH_FAKE_SEQ)
if log is not None:
    row = {"argv": argv, "has_api_key": "ANTHROPIC_API_KEY" in os.environ, "has_claudecode": "CLAUDECODE" in os.environ,
           "cwd": os.getcwd(), "stdin_bytes": len(sys.stdin.read().encode()),
           "auth_env": sorted(k for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_OAUTH_TOKEN")
                              if k in os.environ)}
    with open(log, "a", encoding="utf-8") as fh:
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
    "model_pin_irrelevant": json.dumps({**GOOD, "relevant": False, "class": "model_pin"}),      # not relevant, yet it still asks for a pin
    "newline_title": json.dumps({**GOOD, "title": "line one\nnew W-0123456789abcdef lead open: x"}),
    "good_idx1": json.dumps({**GOOD, "quote_line_index": 1}),                                   # quotes the second line of an item
    "padded": json.dumps(GOOD),                                                                 # a good answer in an envelope over 200 KB (see below)
    "unicode_title": json.dumps({**GOOD, "title": "Café — ok"}, ensure_ascii=False),  # raw UTF-8 on the wire, as the real claude prints it
}
seq = (os.environ.get("ACE_WATCH_FAKE_SEQ") or "").split(",")                          # "iserror,good": one answer name per call, the last repeats
name = seq[min(prior, len(seq) - 1)] if seq != [""] else os.environ.get("ACE_WATCH_FAKE_ANSWER", "irrelevant")
if os.environ.get("ACE_WATCH_FAKE_POISON") and os.environ["ACE_WATCH_FAKE_POISON"] in (argv[-1] if argv else ""):
    name = "exit1"
if name == "hang":                                                                    # never answers: the caller's timeout must end it
    time.sleep(60)
if name in ("exit1", "empty", "garbage"):                                               # failed calls with no usable envelope
    print("not an envelope" if name == "garbage" else "", end="")
    sys.exit(1 if name == "exit1" else 0)
failing = name in ("error", "iserror")
env = {"type": "result", "subtype": "error_during_execution" if failing else "success", "is_error": failing, "duration_ms": 10,
       "num_turns": 1, "result": "" if failing else ANSWERS[name], "session_id": "fake", "total_cost_usd": 0.0004,
       "usage": {"input_tokens": 5, "output_tokens": 20}}
if name == "padded":
    env["pad"] = "x" * 300_000
sys.stdout.buffer.write((json.dumps(env, ensure_ascii=False) + "\n").encode("utf-8"))        # raw UTF-8 whatever the locale
sys.stdout.buffer.flush()
sys.exit(1 if name == "error" else 0)
