"""Tiny builder for Claude Code-shaped transcript records."""

import json


def user(text, **kw):
    return {"type": "user", "sessionId": "sess-1", "cwd": "/work/demo",
            "message": {"role": "user", "content": text}, **kw}


def assistant(*blocks, **kw):
    content = [{"type": "text", "text": b} if isinstance(b, str) else b for b in blocks]
    return {"type": "assistant", "sessionId": "sess-1", "message": {"role": "assistant", "content": content}, **kw}


def tool_use(id_, name, **inp):
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


def tool_result(id_, text, is_error=False):
    return {"type": "user", "sessionId": "sess-1",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": id_, "content": text, "is_error": is_error}]}}


def write(path, records):
    path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in records) + "\n")
    return path
