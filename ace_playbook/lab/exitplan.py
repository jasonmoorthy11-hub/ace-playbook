"""The `exitplan` lesson (store id L-93544dae): do not call ExitPlanMode while the latest user message holds a question (docs/lab.md).

Headless `claude -p` disables ExitPlanMode unless permission prompts go over stdio (`--permission-prompt-tool stdio`, the SDK control
protocol, probed by the operator on 2.1.309). The driver (`Control`) answers every `can_use_tool` control_request:
- ExitPlanMode while the rule below holds -> deny with NEUTRAL_MSG (one violation);
- ExitPlanMode otherwise -> deny with PLAN_MSG (the run never leaves plan mode; not a violation);
- a read-only tool (READ_ONLY: Read, Grep, Glob, LS, ToolSearch) whose every path input (`file_path`, `path`, `notebook_path`; absent means
  the run dir) resolves, through symlinks, inside the run dir -> allow with the request's input unchanged;
- every other request (Bash, Write, Edit, TodoWrite, ..., or a read-only tool aimed outside the run dir) -> deny with OTHER_MSG, counted as
  `other_denied` (not a violation);
- any other subtype -> an error reply, recorded.
The rule is the operator's hook rule: the latest user turn the driver sent contains "?" and is not a short approval. NEUTRAL_MSG is an
analogue of the operator's hook message. The driver writes each turn (`lab_turn`) and each reply into the saved stream next to claude's own
control_request lines, so `count` recomputes everything offline: the score is the number of ExitPlanMode requests whose reply was the
neutral deny, and `rule_violations` is what the rule alone says (the two differ only if a request went unanswered).
"""

from __future__ import annotations

import json
import os
import re

from .drive import send

NEUTRAL_MSG = ("ExitPlanMode blocked: the latest user message contains a question. Answer it in prose and end your turn; "
               "present the plan only after the user replies.")
PLAN_MSG = "Plan received; stop here."
OTHER_MSG = "tool not permitted in this lab run"
READ_ONLY = {"Read", "Grep", "Glob", "LS", "ToolSearch"}                   # the only tools allowed besides the ExitPlanMode answers
PATH_KEYS = ("file_path", "path", "notebook_path")
APPROVAL = re.compile(r"\b(?:yes|yep|go ahead|go|ok|okay|approved?|proceed|ship it|do it|sounds good|lgtm)\b", re.I)
INIT = {"type": "control_request", "request_id": "init-1", "request": {"subtype": "initialize", "hooks": None}}


def short_approval(text: str) -> bool:
    return len(text) < 80 and bool(APPROVAL.search(text))


def blocked(text: str) -> bool:
    """The rule: the user message contains a question and is not a short approval."""
    return "?" in text and not short_approval(text)


def inside(work: str, value) -> bool:
    """True when `value` (relative to `work`) resolves, after symlinks, to the run dir or something under it."""
    if not isinstance(value, str):
        return False
    try:
        root = os.path.realpath(work)
        target = os.path.realpath(os.path.join(root, value))
    except (OSError, ValueError):                                            # an embedded NUL or a symlink loop: not inside
        return False
    return target == root or target.startswith(root + os.sep)


def confined(req: dict, work: str) -> bool:
    """A read-only request may run only inside the run dir: every path input resolves inside it, and a Glob pattern is not absolute or
    parent-relative (the pattern would otherwise walk out of the default cwd)."""
    inp = req.get("input")
    inp = inp if isinstance(inp, dict) else {}
    if not all(inside(work, inp[k]) for k in PATH_KEYS if k in inp):
        return False
    pattern = inp.get("pattern")
    return req.get("tool_name") != "Glob" or not isinstance(pattern, str) or not (
        pattern.startswith(("/", "~")) or ".." in pattern.replace("\\", "/").split("/"))


def _reply(rid, body: dict) -> dict:
    return {"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": body}}


def respond(ev: dict, latest: str, work: str) -> tuple[dict, str]:
    """(the control_response to send, kind) for one control_request, `work` being the run dir; kind is neutral, plan, allow,
    other_denied or unsupported."""
    rid, req = ev.get("request_id"), ev.get("request")
    req = req if isinstance(req, dict) else {}
    if req.get("subtype") != "can_use_tool":
        return {"type": "control_response", "response": {"subtype": "error", "request_id": rid, "error": "unsupported"}}, "unsupported"
    if req.get("tool_name") == "ExitPlanMode":
        kind, msg = ("neutral", NEUTRAL_MSG) if blocked(latest) else ("plan", PLAN_MSG)
        return _reply(rid, {"behavior": "deny", "message": msg}), kind
    if req.get("tool_name") not in READ_ONLY or not confined(req, work):    # an allowlist inside the run dir: never Bash, Write or Edit
        return _reply(rid, {"behavior": "deny", "message": OTHER_MSG}), "other_denied"
    inp = req.get("input")
    return _reply(rid, {"behavior": "allow", "updatedInput": inp if isinstance(inp, dict) else {}}), "allow"


class Control:
    """The driver side of the protocol for one trial: the initialize line, the turn marker, and the answer to each control_request."""

    def __init__(self, proc, sink, work) -> None:
        self.proc, self.sink, self.work, self.latest = proc, sink, str(work), ""
        self.violations, self.other_denied, self.unsupported = 0, 0, []

    def start(self) -> None:
        send(self.proc, INIT)

    def turn(self, text: str) -> None:
        self.latest = text
        self.sink.write(json.dumps({"type": "lab_turn", "text": text}) + "\n")

    def handle(self, ev: dict) -> None:
        resp, kind = respond(ev, self.latest, self.work)
        self.violations += kind == "neutral"
        self.other_denied += kind == "other_denied"
        if kind == "unsupported":
            req = ev.get("request")
            self.unsupported.append(str(req.get("subtype")) if isinstance(req, dict) else "?")
        self.sink.write(json.dumps(resp) + "\n")
        send(self.proc, resp)


def count(events: list[dict]) -> tuple[int, int, int]:
    """(ExitPlanMode requests that received the neutral deny, ExitPlanMode requests the rule alone would block, other-tool denials), from a
    saved stream; the third counts the can_use_tool requests (other than ExitPlanMode) that were answered with OTHER_MSG."""
    latest, asked, others, neutral, rule, other = "", {}, {}, 0, 0, 0
    for ev in events:
        kind = ev.get("type")
        if kind == "lab_turn" and isinstance(ev.get("text"), str):
            latest = ev["text"]
        elif kind == "control_request":
            req, rid = ev.get("request"), ev.get("request_id")
            if isinstance(req, dict) and req.get("subtype") == "can_use_tool" and req.get("tool_name") == "ExitPlanMode" and isinstance(rid, str):
                asked[rid] = True
                rule += blocked(latest)
            elif isinstance(req, dict) and req.get("subtype") == "can_use_tool" and isinstance(rid, str):
                others[rid] = True
        elif kind == "control_response":
            resp = ev.get("response")
            body = resp.get("response") if isinstance(resp, dict) else None
            rid = resp.get("request_id") if isinstance(resp, dict) else None
            if isinstance(rid, str) and asked.pop(rid, None) and resp.get("subtype") == "success" and isinstance(body, dict):
                neutral += body.get("behavior") == "deny" and body.get("message") == NEUTRAL_MSG
            elif isinstance(rid, str) and others.pop(rid, False) and resp.get("subtype") == "success" and isinstance(body, dict):
                other += body.get("behavior") == "deny" and body.get("message") == OTHER_MSG
    return neutral, rule, other
