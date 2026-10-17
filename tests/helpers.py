import json

from ace_playbook.store import DeltaLog, make_lesson_id

PROJECT = {"type": "project", "key": "demo"}
GLOBAL = {"type": "global"}


def evidence(origin="user_message", quote="Please always use python3, not python"):
    return [{"quote": quote, "origin": origin, "session_id": "s1"}]


def add_draft(text="Use python3, not python, for scripts", scope=PROJECT, **fields):
    base = {"text": text, "scope": scope, "kind": "gotcha",
            "applies_when": "running python scripts", "evidence": evidence()}
    base.update(fields)
    return {"op": "ADD", "lesson_id": make_lesson_id(scope, text), "fields": base}


def seeded(cfg):
    """A delta log holding one pinned, active global lesson."""
    log = DeltaLog(cfg.deltas)
    log.append(add_draft("Pinned global lesson about python3", GLOBAL, status="active", pinned=True),
               ts="2026-01-01T00:00:00Z")
    return log


def tamper(cfg):
    """Edit the first stored record in place so the hash chain no longer verifies."""
    lines = cfg.deltas.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["fields"]["text"] = "Tampered lesson text goes here"
    lines[0] = json.dumps(rec)
    cfg.deltas.write_text("\n".join(lines) + "\n")
