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

from ace_playbook.compile import catalogue, detectors, state as state_mod
from ace_playbook.compile.events import end_event, event_from_hook, events_from_records

FIXTURE = Path(__file__).parent / "fixtures" / "compile_payloads.json"
PARITY = Path(__file__).resolve().parent.parent / "ace_playbook" / "fixtures" / "parity" / "step0_parity.json"
STEP0_COMMIT = "17f3891b402aab2b39bfe6c6737460cb3de99216"
PARITY_DETECTORS = ("rm_rf", "rm_rf_nonscratch", "edit_unread_session", "edit_unread_turn",
                    "exitplan_question_block", "preclaim_audit_block", "commit_without_push",
                    "user_correction")
SID = "sess-fixture"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def _text(step: dict) -> str:
    return step["human"] + "x" * step.get("pad", 0)


def records(steps: list[dict], sid: str = SID, day: str = "2026-09-15") -> list[dict]:
    """Invented transcript records, shaped like Claude Code JSONL."""
    out: list[dict] = []

    def add(rtype: str, **kw) -> None:
        rec = {"type": rtype, "sessionId": sid, "timestamp": f"{day}T10:00:{len(out) % 60:02d}Z",
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
    """The same scenario as live hook payloads: PreToolUse, prompts, and a Stop ending each turn."""
    out: list[dict] = []
    for i, step in enumerate(steps):
        if "human" in step:
            if any("human" in s for s in steps[:i]):
                out.append({"hook_event_name": "Stop", "session_id": sid})
            out.append({"hook_event_name": "UserPromptSubmit", "session_id": sid, "prompt": _text(step)})
        elif "tool" in step:
            out.append({"hook_event_name": "PreToolUse", "session_id": sid, "tool_name": step["tool"],
                        "tool_input": step["input"], "tool_use_id": f"t{i}"})
    return out


def _tally(events, state=None) -> Counter:
    counts: Counter = Counter()
    state = state if state is not None else state_mod.new_state(SID)
    for ev in events:
        fires, state, _errors = catalogue.evaluate(ev, state, strict=True)
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
    """Frozen expectations: `scenarios` holds PORT counts; `deviations` lists every scenario where
    the port differs from the real Step 0, with both counts and the fixture's stated reason.
    An unexplained difference raises: a parity change is never recorded silently."""
    d = Path(step0_dir)
    reasons = {p["id"]: p.get("deviation") for p in load_fixture()["payloads"] + load_fixture()["extras"]}
    scen, devs = {}, {}
    for name, steps in scenarios().items():
        step0, port = step0_counts(step0_dir, steps), {k: run_offline(steps).get(k, 0) for k in PARITY_DETECTORS}
        scen[name] = port
        diff = [k for k in PARITY_DETECTORS if step0[k] != port[k]]
        if diff and not reasons.get(name):
            raise AssertionError(f"unexplained parity deviation in {name}: step0={step0} port={port}")
        if reasons.get(name) and not diff:
            raise AssertionError(f"{name} declares a deviation but matches Step 0")
        if diff:
            devs[name] = {"step0": {k: step0[k] for k in diff}, "port": {k: port[k] for k in diff},
                          "reason": reasons[name]}
    return {
        "v": 1,
        "step0_commit": STEP0_COMMIT,
        "source_sha256": hashlib.sha256((d / "detectors.py").read_bytes()).hexdigest(),
        "scanlib_sha256": hashlib.sha256((d / "scanlib.py").read_bytes()).hexdigest(),
        "detectors": list(PARITY_DETECTORS),
        "scenarios": scen,
        "deviations": devs,
    }


def dump_parity(data: dict) -> str:
    """One line per scenario and per deviation, so the file stays short and diffs read well."""
    keys = ("scenarios", "deviations")
    lines = ["{"] + [f" {json.dumps(k)}: {json.dumps(v)}," for k, v in sorted(data.items()) if k not in keys]
    for n, key in enumerate(keys):
        rows = sorted(data[key].items())
        lines.append(f' "{key}": {{' if rows else f' "{key}": {{}}' + ("," if n == 0 else ""))
        lines += [f"  {json.dumps(k)}: {json.dumps(v, sort_keys=True)}" + ("," if i < len(rows) - 1 else "")
                  for i, (k, v) in enumerate(rows)]
        if rows:
            lines.append(" }," if n == 0 else " }")
    return "\n".join([*lines, "}"]) + "\n"


# ---------------------------------------------------------------- step 2: synthetic world
CARDS_FIXTURE = Path(__file__).parent / "fixtures" / "compile_cards.json"
EXAMPLES = Path(__file__).resolve().parent.parent / "ace_playbook" / "fixtures" / "compile" / "examples.json"
RM_CMD = "rm -rf /w/src"


class World:
    """A temp data_dir holding one synthetic lesson, its transcripts and ledgers (all invented)."""

    def __init__(self, cfg, lesson_id: str, fx: dict) -> None:
        self.cfg, self.lesson_id, self.fx = cfg, lesson_id, fx

    def sid(self, key: str) -> str:
        return self.fx["sessions"][key]["id"]

    def ref(self, key: str, command: str = RM_CMD) -> str:
        """The card id a fire of `command` in session `key` would get (also the label ref)."""
        from ace_playbook.compile import evidence as evm
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        return evm.card_id(self.lesson_id, evm.sid(self.sid(key)), evm.payload_hash(payload))

    def add_session(self, key: str, steps: list[dict]) -> str:
        self.fx["sessions"][key] = {"id": f"sess-syn-{key}", "steps": steps}
        write_transcript(self.cfg, self.fx["sessions"][key])
        return self.fx["sessions"][key]["id"]


def write_transcript(cfg, session: dict) -> Path:
    d = Path(cfg.transcripts_root) / "proj"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session['id']}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records(session["steps"], session["id"], session.get("day", "2026-09-15"))))
    return p


def append_jsonl(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def write_labels(cfg, rows: list[dict]) -> None:
    """A hash-chained labels.jsonl (record_hash chain from GENESIS)."""
    from ace_playbook.store.schema import GENESIS, record_hash
    prev = GENESIS
    for r in rows:
        row = {"reason_code": "real", "at": "2026-10-20T08:00:00Z", **r, "prev_hash": prev}
        row["hash"] = record_hash(row)
        prev = row["hash"]
        append_jsonl(cfg.path("labels.jsonl"), row)


def make_world(tmp_path, monkeypatch, *, origin: str | None = "origin", sessions=("later",), shown=("later",),
               legacy: bool = False, status: str = "active") -> World:
    """origin = fixture session the user said it in; sessions = later transcripts; shown = sessions whose
    injection record lists the lesson. legacy = migrated lesson with self-quoted `legacy:` evidence."""
    import dataclasses
    from ace_playbook.config import Config
    from ace_playbook.store import DeltaLog
    from .helpers import GLOBAL, add_draft
    fx = json.loads(CARDS_FIXTURE.read_text())
    cfg = Config(data_dir=tmp_path / "data", transcripts_root=tmp_path / "projects")
    Path(cfg.data_dir).mkdir(parents=True)
    les = fx["lesson"]
    if legacy:
        evid = [{"quote": les["text"], "origin": "user_file", "session_id": fx["legacy_session_id"]}]
    else:
        evid = [{"quote": les["quote"], "origin": "user_message", "session_id": fx["sessions"][origin]["id"]}]
    draft = add_draft(les["text"], GLOBAL, status=status, applies_when=les["applies_when"], evidence=evid)
    DeltaLog(cfg.deltas).append(draft, ts="2026-10-01T00:00:00Z")
    lid = draft["lesson_id"]
    for key in ([] if legacy or origin is None else [origin]) + list(sessions):
        write_transcript(cfg, fx["sessions"][key])
    for key in shown:
        append_jsonl(cfg.injections, {"session_id": fx["sessions"][key]["id"], "shown_ids": [lid], "holdout_ids": []})
    row = catalogue.CATALOGUE["rm_rf_nonscratch"]
    monkeypatch.setitem(catalogue.CATALOGUE, "rm_rf_nonscratch", dataclasses.replace(row, lessons=(lid,)))
    return World(cfg, lid, fx)


# ---------------------------------------------------------------- step 4: a real-catalogue world for the shadow hook
RM_LESSON = "L-b3d1fba9"   # the catalogue's own mapping: a subprocess cannot monkeypatch, so it must validate as is


def real_world(tmp_path, detectors=("rm_rf_nonscratch",), approve: bool = True):
    """data_dir with hand-built cards for RM_LESSON, one written proposal per detector and (optionally) its
    approved observe copy. Returns a namespace: cfg, conf (config file path), data, props {detector: proposal}."""
    from types import SimpleNamespace
    from ace_playbook.compile import evidence as evm, proposal as prop
    from ace_playbook.config import Config
    cfg = Config(data_dir=tmp_path / "data")
    Path(cfg.data_dir).mkdir(parents=True)
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(cfg.data_dir)}))
    w = SimpleNamespace(cfg=cfg, conf=str(conf), props={}, data=Path(cfg.data_dir))
    for det in detectors:
        payload = json.loads(EXAMPLES.read_text())["detectors"][det]["fire"]
        ph, inc = evm.payload_hash(payload), evm.sid("sess-live-incident")
        card = {"id": evm.card_id(RM_LESSON, inc, ph), "v": 1, "lesson_id": RM_LESSON, "provenance": "standard",
                "origin_session": evm.sid("sess-live-origin"), "incident_session": inc, "incident_kind": "later",
                "label_ref": None, "quote_offsets": [{"user_turn": 0, "start": 0, "end": 20,
                                                      "quote_sha256": "0123456789abcdef"}],
                "detector": det, "detector_sha256": "0" * 64, "tier": "offline", "incident_ts": "2026-10-01T00:00:00Z",
                "payload_sha256": ph, "payload_ref": payload, "replayable": True,
                "lesson_shown_in_incident_session": True, "agent_cited": False, "self_report": True,
                "scrub": {"profile": "LESSON", "hits": 0}, "created": "2026-10-01T00:00:00Z"}
        evm.private_dir(w.data / "cards")
        (w.data / "cards" / (card["id"] + ".json")).write_text(json.dumps(card))
        p = prop.build(cfg, RM_LESSON, det, [card])
        prop.write(cfg, p, {card["id"]: card})
        w.props[det] = p
        if approve:
            approve_file(cfg, p)
    return w


def approve_file(cfg, p: dict, mode: str = "observe", *, row: bool = False, ts: str | None = None) -> Path:
    """Write the approved copy the way `compile approve` does (without the TTY); `row` also appends its approvals row."""
    from ace_playbook.atomic import write_private
    from ace_playbook.compile import approvals, shadow
    path = Path(cfg.data_dir) / "proposals" / "approved" / (p["id"] + ".json")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    rec = shadow.approved_record(p, mode)
    write_private(path, json.dumps(rec, indent=1, sort_keys=True) + "\n")
    if row:
        approvals.append(cfg.data_dir, {"proposal_id": p["id"], "proposal_sha256": rec["proposal_sha256"], "mode": mode,
                                         **({"ts": ts} if ts else {})})
    return path


def pre_payload(command: str, sid: str = "sess-live-1", tool: str = "Bash") -> dict:
    return {"hook_event_name": "PreToolUse", "session_id": sid, "tool_name": tool,
            "tool_input": {"command": command}, "tool_use_id": "toolu_1"}


def shadow_rows(data) -> list[dict]:
    p = Path(data) / "shadow.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


# ---------------------------------------------------------------- step 5a: the offline pass
NOW = 1792411200.0         # 2026-10-19T12:00:00Z: every offline test passes this as `now`
RM_PROMPT_CMD = "rm -rf /w/src"


def offline_world(tmp_path, mode: str = "observe", approved_at: str = "2026-10-01T00:00:00Z"):
    """real_world with a transcripts root and its approved copy re-written at `mode` and a fixed approval time."""
    from ace_playbook.atomic import write_private
    from ace_playbook.compile import approvals, shadow
    w = real_world(tmp_path)
    w.cfg.transcripts_root = tmp_path / "projects"
    w.pid = w.props["rm_rf_nonscratch"]["id"]

    def approve(m, at=approved_at):
        """Write the copy; above observe the hook also needs the matching approvals row (step 5b), so append it."""
        rec = shadow.approved_record(w.props["rm_rf_nonscratch"], m, at)
        write_private(w.data / "proposals" / "approved" / (w.pid + ".json"), json.dumps(rec, indent=1, sort_keys=True) + "\n")
        if m != "observe":
            approvals.append(w.data, {"proposal_id": w.pid, "proposal_sha256": rec["proposal_sha256"], "mode": m, "ts": at})

    w.approve = approve
    w.approve(mode)
    return w


def put_session(w, sid: str, steps: list[dict], age: float = 7200.0, day: str = "2026-10-18") -> Path:
    """An invented transcript (record `i` is stamped `<day>T10:00:<i>Z`) whose mtime is `age` seconds before NOW."""
    import os
    path = write_transcript(w.cfg, {"id": sid, "steps": steps, "day": day})
    os.utime(path, (NOW - age, NOW - age))
    return path


def put_shadow(w, sid: str, command: str, mode: str = "observe", ts: str = "2026-10-18T10:00:00Z", pid: str | None = None) -> dict:
    """A live shadow row the hook would have written for `command` in session `sid`."""
    from ace_playbook.compile import evidence as evm, state as st
    row = {"ts": ts, "proposal_id": pid or w.pid, "lesson_id": RM_LESSON, "mode": mode, "event": "PreToolUse",
           "tool_name": "Bash", "session": st.session_hash(sid), "would_fire": True, "cause": "detector", "tier": "live",
           "payload_sha256": evm.payload_hash({"tool_name": "Bash", "tool_input": {"command": command}})}
    append_jsonl(w.cfg.path("shadow.jsonl"), row)
    return row


def tree_state(root) -> dict:
    """Relative path -> (is a directory, size, mtime_ns) of every file AND directory under `root`, symlinks not followed: a
    new directory or a same-size rewrite shows (a dry run must leave it unchanged)."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        info = p.lstat()
        out[str(p.relative_to(root))] = (p.is_dir() and not p.is_symlink(), info.st_size, info.st_mtime_ns)
    return out


if __name__ == "__main__":  # python -m tests.compile_support <rebuild-lab>/ace/step0
    PARITY.parent.mkdir(exist_ok=True)
    PARITY.write_text(dump_parity(regen_parity(sys.argv[1])))
    print(f"wrote {PARITY}")
