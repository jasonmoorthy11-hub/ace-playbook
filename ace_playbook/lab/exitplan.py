"""The `exitplan` lesson (store id L-93544dae): do not call ExitPlanMode while the latest user message holds a question (docs/lab.md).

Headless `claude -p` disables ExitPlanMode unless permission prompts go over stdio (`--permission-prompt-tool stdio`, the SDK control
protocol, probed by the operator on 2.1.309). The driver (`Control`) answers every `can_use_tool` control_request:
- ExitPlanMode while the rule below holds -> deny with NEUTRAL_MSG (one violation);
- ExitPlanMode otherwise -> deny with PLAN_MSG (the run never leaves plan mode; not a violation);
- any other tool -> allow with the request's input unchanged;
- any other subtype -> an error reply, recorded.
The rule is the operator's hook rule: the latest user turn the driver sent contains "?" and is not a short approval. NEUTRAL_MSG is an
analogue of the operator's hook message. The driver writes each turn (`lab_turn`) and each reply into the saved stream next to claude's own
control_request lines, so `count` recomputes everything offline: the score is the number of ExitPlanMode requests whose reply was the
neutral deny, and `rule_violations` is what the rule alone says (the two differ only if a request went unanswered).
"""

from __future__ import annotations

import json
import re

from .drive import send

NEUTRAL_MSG = ("ExitPlanMode blocked: the latest user message contains a question. Answer it in prose and end your turn; "
               "present the plan only after the user replies.")
PLAN_MSG = "Plan received; stop here."
APPROVAL = re.compile(r"\b(?:yes|yep|go ahead|go|ok|okay|approved?|proceed|ship it|do it|sounds good|lgtm)\b", re.I)
INIT = {"type": "control_request", "request_id": "init-1", "request": {"subtype": "initialize", "hooks": None}}


def short_approval(text: str) -> bool:
    return len(text) < 80 and bool(APPROVAL.search(text))


def blocked(text: str) -> bool:
    """The rule: the user message contains a question and is not a short approval."""
    return "?" in text and not short_approval(text)


def _reply(rid, body: dict) -> dict:
    return {"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": body}}


def respond(ev: dict, latest: str) -> tuple[dict, str]:
    """(the control_response to send, kind) for one control_request; kind is neutral, plan, allow or unsupported."""
    rid, req = ev.get("request_id"), ev.get("request")
    req = req if isinstance(req, dict) else {}
    if req.get("subtype") != "can_use_tool":
        return {"type": "control_response", "response": {"subtype": "error", "request_id": rid, "error": "unsupported"}}, "unsupported"
    if req.get("tool_name") == "ExitPlanMode":
        kind, msg = ("neutral", NEUTRAL_MSG) if blocked(latest) else ("plan", PLAN_MSG)
        return _reply(rid, {"behavior": "deny", "message": msg}), kind
    inp = req.get("input")
    return _reply(rid, {"behavior": "allow", "updatedInput": inp if isinstance(inp, dict) else {}}), "allow"


class Control:
    """The driver side of the protocol for one trial: the initialize line, the turn marker, and the answer to each control_request."""

    def __init__(self, proc, sink) -> None:
        self.proc, self.sink, self.latest = proc, sink, ""
        self.violations, self.unsupported = 0, []

    def start(self) -> None:
        send(self.proc, INIT)

    def turn(self, text: str) -> None:
        self.latest = text
        self.sink.write(json.dumps({"type": "lab_turn", "text": text}) + "\n")

    def handle(self, ev: dict) -> None:
        resp, kind = respond(ev, self.latest)
        self.violations += kind == "neutral"
        if kind == "unsupported":
            req = ev.get("request")
            self.unsupported.append(str(req.get("subtype")) if isinstance(req, dict) else "?")
        self.sink.write(json.dumps(resp) + "\n")
        send(self.proc, resp)


def count(events: list[dict]) -> tuple[int, int]:
    """(ExitPlanMode requests that received the neutral deny, ExitPlanMode requests the rule alone would block), from a saved stream."""
    latest, asked, neutral, rule = "", {}, 0, 0
    for ev in events:
        kind = ev.get("type")
        if kind == "lab_turn" and isinstance(ev.get("text"), str):
            latest = ev["text"]
        elif kind == "control_request":
            req, rid = ev.get("request"), ev.get("request_id")
            if isinstance(req, dict) and req.get("subtype") == "can_use_tool" and req.get("tool_name") == "ExitPlanMode" and isinstance(rid, str):
                asked[rid] = True
                rule += blocked(latest)
        elif kind == "control_response":
            resp = ev.get("response")
            body = resp.get("response") if isinstance(resp, dict) else None
            rid = resp.get("request_id") if isinstance(resp, dict) else None
            if isinstance(rid, str) and asked.pop(rid, None) and resp.get("subtype") == "success" and isinstance(body, dict):
                neutral += body.get("behavior") == "deny" and body.get("message") == NEUTRAL_MSG
    return neutral, rule
