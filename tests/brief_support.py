"""Synthetic fixtures of the brief tests: a digest, a brief, a stream-json run, a config. No real mail, names or addresses."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

MARKER = "UNIQUE-PRIVATE-SUMMARY-TEXT"


# Stand-ins for brief_excl._work. The exclusion child is a spawned interpreter (it sees nothing of the parent but its arguments and
# environment), so a worker must be an importable top-level function; this module imports almost nothing, so the child starts fast.
def work_hang(conn, lists, mail) -> None:
    Path(os.environ["BRIEF_TEST_CHILD_PID"]).write_text(str(os.getpid()))
    time.sleep(60)


def work_crash(conn, lists, mail) -> None:
    os._exit(7)


def work_boom(conn, lists, mail) -> None:
    raise RuntimeError(MARKER)


def work_answer(kind: str, conn, lists, mail) -> None:
    """Send a garbled answer: a dict, a str, a list of ints, the mail twice, a thread that was never an input, or an input with a changed field."""
    conn.send({"dict": {}, "str": "x", "ints": [1], "twice": mail + mail, "alien": [{"x": 1}], "forged": [{**mail[0], "subject": "forged"}],
               "blanked": [{**mail[0], "summary": ""}], "same": list(mail)}[kind])
    conn.close()


def work_spy(conn, lists, mail) -> None:
    """Report what the child can see of the parent: a module variable the parent set at run time, and the raw digest it holds."""
    from ace_playbook.compile import brief_excl
    Path(os.environ["BRIEF_TEST_CHILD_PID"]).write_text(json.dumps({"leak": getattr(brief_excl, "_PARENT_ONLY", None), "mail_arg": len(mail)}))
    conn.send(mail)
    conn.close()


def digest(**kw) -> dict:
    return {"generated_at": "2026-10-21T08:05:00-07:00",
            "calendar": [{"start": "2026-10-21T09:30:00-07:00", "end": "2026-10-21T10:00:00-07:00", "title": "Planning sync",
                          "organizer_is_me": True, "optional": False, "attendees_n": 4}],
            "mail": [{"thread_id": "t1", "sender": "Pat <pat@example.com>", "subject": "Question about the draft", "received": "2026-10-20T17:00:00Z",
                      "asked_me": True, "replied": False, "summary": f"Pat asks whether the draft is final. {MARKER}"},
                     {"thread_id": "t2", "sender": "Casey <casey@example.org>", "subject": "Private matter", "received": "2026-10-20T18:00:00Z",
                      "asked_me": True, "replied": False, "summary": "should never reach stage two"},
                     {"thread_id": "t3", "sender": "Billing <billing@example.net>", "subject": "Invoice 12", "received": "2026-10-20T19:00:00Z",
                      "asked_me": True, "replied": False, "summary": "account number in the body"},
                     {"thread_id": "t4", "sender": "Lee <lee@example.com>", "subject": "Re: HOLD-THIS-SUBJECT", "received": "2026-10-20T20:00:00Z",
                      "asked_me": True, "replied": False, "summary": "dropped by subject"}],
            "issues": [{"id": "ENG-1", "title": "Fix the thing", "state": "In Progress", "due": None}], **kw}


def brief(**kw) -> dict:
    item = {"title": "Reply to the draft question", "sentence": "Pat asked on Tuesday whether the draft is final.", "source": "in your inbox",
            "url": None}
    return {"generated_at": "2026-10-21T08:05:00-07:00", "day_class": "normal", "headline": "Meetings bookend the day, the middle is yours.",
            "acts": [{"range": "9:30 AM – 12 PM", "sentence": "Planning sync, then a review."}, {"range": "12 – 3 PM", "sentence": "Open."},
                     {"range": "3 PM onward", "sentence": "One call."}], "needs_you": [item], "resolved": [], **kw}


def exclusions(path: Path, **kw) -> Path:
    body = {"drop_senders": ["casey@"], "drop_subjects": ["hold-this-subject"], "drop_bodies_from": ["billing@"], **kw}
    path.write_text(json.dumps(body))
    return path


def config(tmp_path: Path, **kw) -> Path:
    """A tmp config file with its data and brief directories under tmp_path; the chain key is off (nothing here reads it)."""
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "brief_dir": str(tmp_path / "brief"), "chain_key_path": None, **kw}))
    return conf


def stream(result_text: str = "ok", tools=("mcp__claude_ai_Gmail__search_threads",), five=0.08, seven=0.01, is_error=False, init_tools=(), junk=True) -> str:
    """A stream-json file as `claude -p --output-format stream-json --verbose` prints it (the shapes seen in phase 0)."""
    window = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": five, "resetsAt": 1}, "seven_day": {"utilization": seven, "resetsAt": 2}}}
    events = [{"type": "system", "subtype": "init", "tools": list(init_tools), "mcp_servers": []},
              *({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}, {"type": "tool_use", "name": t, "input": {}}]}} for t in tools),
              {"type": "rate_limit_event", "rate_limit_info": window},
              {"type": "rate_limit_event", "rate_limit_info": {**window, "unifiedWindows": {"five_hour": {"utilization": five + 0.01},
                                                                                              "seven_day": {"utilization": seven}}}},
              {"type": "result", "subtype": "success", "is_error": is_error, "result": result_text, "total_cost_usd": 0.2424, "duration_ms": 20800,
               "num_turns": 5, "usage": {"input_tokens": 7, "cache_creation_input_tokens": 51891, "cache_read_input_tokens": 91229, "output_tokens": 2570},
               "modelUsage": {"claude-sonnet-5-5": {"costUSD": 0.2424}}}]
    return "\n".join(json.dumps(e) for e in events) + ("\nnot json\n" if junk else "\n")          # junk: a stray non-JSON line, as the readers tolerate
