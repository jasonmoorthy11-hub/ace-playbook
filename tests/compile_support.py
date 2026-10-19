"""Shared setup for the compile tests: step lists -> transcript records or hook payloads.

A scenario is a list of steps (tests/fixtures/compile_payloads.json). `run_offline` replays it
through the transcript adapter, `run_live` through the hook adapter. `regen_parity` recomputes
the frozen Step 0 counts from a rebuild-lab checkout (local only; CI uses the frozen JSON).
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from ace_playbook.compile import detectors, state as state_mod
from ace_playbook.compile.events import end_event, event_from_hook, events_from_records

FIXTURE = Path(__file__).parent / "fixtures" / "compile_payloads.json"
PARITY = Path(__file__).resolve().parent.parent / "ace_playbook" / "fixtures" / "frozen" / "step0_parity.json"
STEP0_COMMIT = "17f3891b402aab2b39bfe6c6737460cb3de99216"
PARITY_DETECTORS = ("rm_rf", "rm_rf_nonscratch", "edit_unread_session", "edit_unread_turn",
                    "exitplan_question_block", "preclaim_audit_block", "commit_without_push",
                    "user_correction")
SID = "sess-fixture"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def _text(step: dict) -> str:
    return step["human"] + "x" * step.get("pad", 0)


def records(steps: list[dict], sid: str = SID) -> list[dict]:
    """Invented transcript records, shaped like Claude Code JSONL."""
    out: list[dict] = []

    def add(rtype: str, **kw) -> None:
        rec = {"type": rtype, "sessionId": sid, "timestamp": f"2026-09-15T10:00:{len(out) % 60:02d}Z",
               "isSidechain": False, "uuid": f"u{len(out)}"}
        rec.update(kw)
        out.append(rec)

    for step in steps:
        if "human" in step:
            add("user", message={"role": "user", "content": _text(step)})
        elif "queued" in step:
            add("attachment", attachment={"type": "queued_command", "commandMode": "prompt",
                                          "origin": {"kind": "human"}, "prompt": step["queued"]})
        else:
            tid = f"t{len(out)}"
            add("assistant", message={"id": f"m{len(out)}", "content": [
                {"type": "tool_use", "id": tid, "name": step["tool"], "input": step["input"]}]})
            add("user", message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": step.get("result", "ok"),
                 "is_error": bool(step.get("error"))}]})
    return out


def hook_payloads(steps: list[dict], sid: str = SID) -> list[dict]:
    """The same scenario as live hook payloads (prompts and PreToolUse only)."""
    out: list[dict] = []
    for i, step in enumerate(steps):
        if "human" in step:
            out.append({"hook_event_name": "UserPromptSubmit", "session_id": sid, "prompt": _text(step)})
        elif "tool" in step:
            out.append({"hook_event_name": "PreToolUse", "session_id": sid, "tool_name": step["tool"],
                        "tool_input": step["input"], "tool_use_id": f"t{i}"})
    return out


def _tally(events, state=None) -> Counter:
    counts: Counter = Counter()
    state = state if state is not None else state_mod.new_state(SID)
    for ev in events:
        fires, state = detectors.evaluate(ev, state)
        for f in fires:
            counts[f.detector] += f.count
    return counts


def run_offline(steps: list[dict]) -> Counter:
    evs = list(events_from_records(records(steps)))
    counts = _tally(evs + [end_event("offline")])
    return counts


def run_live(steps: list[dict]) -> Counter:
    missing = {s["input"].get("file_path") for s in steps if s.get("exists") is False}

    def probe(path: str) -> bool:
        return path not in missing

    evs = [event_from_hook(p, probe=probe) for p in hook_payloads(steps)]
    return _tally(evs + [end_event("live")])


def step0_counts(step0_dir: str, steps: list[dict]) -> dict:
    """Run the real Step 0 scanner over the scenario (needs a rebuild-lab checkout)."""
    sys.dont_write_bytecode = True
    sys.path.insert(0, step0_dir)
    try:
        import scanlib  # type: ignore
        events = scanlib.scan_lines([json.dumps(r) for r in records(steps)]).events
    finally:
        sys.path.remove(step0_dir)
        for name in ("scanlib", "detectors"):
            sys.modules.pop(name, None)
    return {name: int(events.get(name, 0)) for name in PARITY_DETECTORS}


def scenarios() -> dict[str, list[dict]]:
    fx = load_fixture()
    return {p["id"]: p["steps"] for p in fx["payloads"] + fx["extras"]}


def regen_parity(step0_dir: str) -> dict:
    d = Path(step0_dir)
    return {
        "v": 1,
        "step0_commit": STEP0_COMMIT,
        "source_sha256": hashlib.sha256((d / "detectors.py").read_bytes()).hexdigest(),
        "scanlib_sha256": hashlib.sha256((d / "scanlib.py").read_bytes()).hexdigest(),
        "detectors": list(PARITY_DETECTORS),
        "scenarios": {name: step0_counts(step0_dir, steps) for name, steps in scenarios().items()},
    }


def dump_parity(data: dict) -> str:
    """One line per scenario, so the file stays short and diffs read well."""
    head = [f" {json.dumps(k)}: {json.dumps(v)}," for k, v in sorted(data.items()) if k != "scenarios"]
    rows = sorted(data["scenarios"].items())
    body = [f"  {json.dumps(n)}: {json.dumps(c, sort_keys=True)}" + ("," if i < len(rows) - 1 else "")
            for i, (n, c) in enumerate(rows)]
    return "\n".join(["{", *head, ' "scenarios": {', *body, " }", "}"]) + "\n"


if __name__ == "__main__":  # python -m tests.compile_support <rebuild-lab>/ace/step0
    PARITY.write_text(dump_parity(regen_parity(sys.argv[1])))
    print(f"wrote {PARITY}")
