"""The brief on wake, code side: `python -m ace_playbook.compile.brief <verb>` (docs/design/compiler.md 8.x "Brief (as built)", D-110).

hooks/brief.sh runs the model stages; this module owns what a model must not: the dedupe clock, the private exclusion list (applied by
code after the schema check), the page, the stream-json readers, the window ledger and the state file. No model call, no network, never
prints mail content. Exit: 0 ok, 1 error (type name only), 2 refused (schema, exclusions), 3 `due`: not due.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config as cfgmod
from ..atomic import write_private
from . import brief_render as br

FMT, STATE, LEDGER = "%Y-%m-%dT%H:%M:%SZ", "brief_state.json", "brief_ledger.jsonl"
EXCLUSIONS = ("drop_senders", "drop_subjects", "drop_bodies_from")


def _jl(text: str):
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def read_json(path):
    """The first `{` to the last `}` of a file (a ```json fence or a stray sentence around the object is tolerated); None if unreadable."""
    try:
        text = Path(path).read_bytes()[:2_000_000].decode("utf-8", "replace")
    except OSError:
        return None
    a, b = text.find("{"), text.rfind("}")
    return _jl(text[a:b + 1] if 0 <= a < b else text)


def apply_exclusions(cfg, digest: dict) -> dict:
    """Remove mail threads whose sender or subject match the private lists; a drop_bodies_from sender keeps sender and subject only.
    Any problem with the exclusions file refuses (nothing is written without the list applied)."""
    raw = json.loads(Path(cfg.brief_exclusions).read_text(encoding="utf-8")) if cfg.brief_exclusions else {}
    if not isinstance(raw, dict) or set(raw) - set(EXCLUSIONS) or not all(
            isinstance(raw.get(k, []), list) and all(isinstance(p, str) and len(p) <= 300 for p in raw.get(k, [])) for k in EXCLUSIONS):
        raise br.SchemaError("exclusions")
    try:
        ex = {k: [re.compile(p, re.IGNORECASE) for p in raw.get(k, [])] for k in EXCLUSIONS}
    except re.error:
        raise br.SchemaError("exclusions") from None

    def hit(key, s):
        return any(p.search(s) for p in ex[key])
    mail = [{**m, "summary": ""} if hit("drop_bodies_from", m["sender"]) else m for m in digest["mail"]
            if not (hit("drop_senders", m["sender"]) or hit("drop_subjects", m["subject"]))]
    return {**digest, "mail": mail}


def _now() -> str:
    return datetime.now(timezone.utc).strftime(FMT)


def _dir(cfg) -> Path:
    cfg.brief_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return cfg.brief_path


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _util(event, window: str):
    """The utilization (0..1) of a rate-limit window (`five_hour`, `seven_day`) in a rate_limit_event, else None."""
    try:
        return _num(event["rate_limit_info"]["unifiedWindows"][window]["utilization"])
    except (KeyError, TypeError):
        return None


def extract(path: str, what: str, last: bool = False):
    """One thing out of a stream-json file: the first (or last) rate_limit_event, the result object (+ `_tools_called`) or its text."""
    events = [e for e in map(_jl, Path(path).read_bytes()[:50_000_000].decode("utf-8", "replace").splitlines()) if isinstance(e, dict)]
    if what not in ("rate_limit", "result", "text"):
        raise ValueError("what")
    if what == "rate_limit":
        found = [e for e in events if e.get("type") == "rate_limit_event"]
        return (found[-1] if last else found[0]) if found else None
    res = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if res is None or what == "text" and (res.get("is_error") or not isinstance(res.get("result"), str)):
        raise ValueError("no usable result")
    blocks = [b for e in events if e.get("type") == "assistant" and isinstance(e.get("message"), dict)
              for b in e["message"].get("content") or [] if isinstance(b, dict) and b.get("type") == "tool_use"]
    return res["result"] if what == "text" else {**res, "_tools_called": sorted({b["name"] for b in blocks if isinstance(b.get("name"), str)})}


def cmd_due(cfg, a) -> int:
    try:
        stamp = json.loads((_dir(cfg) / STATE).read_text())["last_brief"]
        age = datetime.now(timezone.utc) - datetime.strptime(stamp, FMT).replace(tzinfo=timezone.utc)
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return 0 if a.force or age >= timedelta(hours=cfg.brief_dedupe_hours) or age < -timedelta(minutes=1) else 3


def cmd_digest_apply(cfg, a) -> int:
    digest = br.validate_digest(read_json(a.src))
    clean = apply_exclusions(cfg, digest)
    write_private(Path(a.out) if a.out else _dir(cfg) / "digest_clean.json", _dump(clean))
    print(f"digest: calendar {len(clean['calendar'])}, mail {len(clean['mail'])} of {len(digest['mail'])}, issues {len(clean['issues'])}")


def cmd_render(cfg, a) -> int:
    brief, d = br.validate_brief(read_json(a.src)), _dir(cfg)
    write_private(d / "brief.json", _dump(brief))
    write_private(d / "brief.html", br.render(brief))
    print(d / "brief.html")


def cmd_state_mark(cfg, a) -> int:
    n = len(br.validate_brief(read_json(_dir(cfg) / "brief.json"))["needs_you"])
    write_private(_dir(cfg) / STATE, _dump({"last_brief": _now(), "needs_you_count": n}))
    print(n)


def cmd_ledger(cfg, a) -> int:
    if not re.fullmatch(r"[a-z0-9_]{1,20}", a.stage):
        raise br.SchemaError("stage")
    before, after, res = read_json(a.before), read_json(a.after), read_json(a.result)
    res = res if isinstance(res, dict) else {}
    tokens = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    use, models = (res.get(k) if isinstance(res.get(k), dict) else {} for k in ("usage", "modelUsage"))
    row = {"ts": _now(), "stage": a.stage, "model": ",".join(sorted(k for k in models if isinstance(k, str)))[:100],
           "cost_usd": _num(res.get("total_cost_usd")), **{k: _num(use.get(k)) for k in tokens},
           **{f"{w}_{t}": _util(ev, w) for w in ("five_hour", "seven_day") for t, ev in (("before", before), ("after", after))},
           "duration_ms": _num(res.get("duration_ms")), "num_turns": _num(res.get("num_turns")),
           "tools_called": [t[:100] for t in (res.get("_tools_called") or []) if isinstance(t, str)][:50]}
    fd = os.open(_dir(cfg) / LEDGER, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)       # 0600, O_APPEND, locked
    with os.fdopen(fd, "ab") as fh:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        fh.write(json.dumps(row, sort_keys=True).encode() + b"\n")


def cmd_extract(cfg, a) -> int:
    out = extract(a.stream, a.what, a.last)
    print(out if a.what == "text" else json.dumps(out))


def cmd_status(cfg, a) -> int:
    d = _dir(cfg)
    state = read_json(d / STATE) or {}
    rows = [r for r in map(_jl, (d / LEDGER).read_text(errors="replace").splitlines() if (d / LEDGER).is_file() else []) if isinstance(r, dict)]
    costs = ", ".join(f"{s} ${c}" for s, c in sorted({r.get("stage"): r.get("cost_usd") for r in rows}.items(), key=str))
    print(f"last brief: {state.get('last_brief', 'never')}\nneeds you: {state.get('needs_you_count', 0)}\nledger rows: {len(rows)}")
    print(f"last stage costs: {costs or 'none'}")


def cmd_conf(cfg, a) -> int:
    if not a.key.startswith("brief_") or not hasattr(cfg, a.key):
        raise ValueError("key")
    print(_dir(cfg) if a.key == "brief_dir" else getattr(cfg, a.key))


VERBS = {"due": ["--force"], "digest-apply": ["src", "--out"], "render": ["src"], "state-mark": [], "status": [],
         "ledger": ["before", "after", "stage", "result"], "extract": ["stream", "--what", "--last"], "conf": ["key"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ace_playbook.compile.brief")
    sub = ap.add_subparsers(dest="verb", required=True)
    for verb, names in VERBS.items():
        p = sub.add_parser(verb)
        for n in names:
            p.add_argument(n, **({"action": "store_true"} if n in ("--force", "--last") else {}))
    a = ap.parse_args(argv)
    try:
        return globals()["cmd_" + a.verb.replace("-", "_")](cfgmod.load(), a) or 0     # a cmd_ returns an exit code or None
    except br.SchemaError as e:
        print(f"brief: schema: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as e:
        print(f"brief: {type(e).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
