from ace_playbook.store import make_lesson_id

PROJECT = {"type": "project", "key": "demo"}
GLOBAL = {"type": "global"}


def evidence(origin="user_message", quote="Please always use python3, not python"):
    return [{"quote": quote, "origin": origin, "session_id": "s1"}]


def add_draft(text="Use python3, not python, for scripts", scope=PROJECT, **fields):
    base = {"text": text, "scope": scope, "kind": "gotcha",
            "applies_when": "running python scripts", "evidence": evidence()}
    base.update(fields)
    return {"op": "ADD", "lesson_id": make_lesson_id(scope, text), "fields": base}
