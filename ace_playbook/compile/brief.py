"""The brief on wake, code side: `python -m ace_playbook.compile.brief <verb>` (docs/design/compiler.md 8.x "Brief (as built)", D-110).

hooks/brief.sh runs the model stages; this module owns what a model must not: the dedupe clock, the private exclusion list (applied by
code after the schema check), the page, the stream-json readers, the window ledger and the state file. No model call, no network, never
prints mail content. Exit: 0 ok, 1 error (type name only), 2 refused (schema, exclusions), 3 `due`: not due.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config as cfgmod
from ..atomic import write_private
from . import brief_audit as ba, brief_excl, brief_render as br

FMT, STATE, LEDGER = "%Y-%m-%dT%H:%M:%SZ", "brief_state.json", "brief_ledger.jsonl"
_jl, _num = ba.jl, ba.num


def read_json(path):
    """The first `{` to the last `}` of a file (a ```json fence or a stray sentence around the object is tolerated); None if unreadable."""
    try:
        text = Path(path).read_bytes()[:2_000_000].decode("utf-8", "replace")
    except OSError:
        return None
    a, b = text.find("{"), text.rfind("}")
    return _jl(text[a:b + 1] if 0 <= a < b else text)


def apply_exclusions(cfg, digest: dict) -> dict:
    """Remove mail threads whose sender or subject contain a private literal; a drop_bodies_from sender keeps sender and subject only.
    brief_excl does the matching in a child process under a timeout; any problem with the file or the child refuses (exit 2), so nothing
    is written without the list applied."""
    raw = json.loads(Path(cfg.brief_exclusions).read_text(encoding="utf-8")) if cfg.brief_exclusions else {}
    try:
        return {**digest, "mail": brief_excl.apply(raw, digest["mail"])}
    except brief_excl.Refused:
        raise br.SchemaError("exclusions") from None


def _now() -> str:
    return datetime.now(timezone.utc).strftime(FMT)


def _dir(cfg) -> Path:
    cfg.brief_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return cfg.brief_path


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def _util(event, window: str):
    """The utilization (0..1) of a rate-limit window (`five_hour`, `seven_day`) in a rate_limit_event, else None."""
    try:
        return _num(event["rate_limit_info"]["unifiedWindows"][window]["utilization"])
    except (KeyError, TypeError):
        return None


def extract(path: str, what: str, last: bool = False):
    """One thing out of a stream-json file: the first (or last) rate_limit_event, the result object (+ `_tools_called` and the count
    `_unknown_tools` of the tool names that may not be written down, see brief_audit) or its text."""
    events = ba.events(path)
    if what not in ("rate_limit", "result", "text"):
        raise ValueError("what")
    if what == "rate_limit":
        found = [e for e in events if e.get("type") == "rate_limit_event"]
        return (found[-1] if last else found[0]) if found else None
    res = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if res is None or what == "text" and (res.get("is_error") or not isinstance(res.get("result"), str)):
        raise ValueError("no usable result")
    return res["result"] if what == "text" else {**res, **ba.called_tools(events, _known())}


def _known() -> set:
    try:
        return ba.known_names()
    except (OSError, ValueError):          # the list files are checked by brief.sh first; unreadable here, only the init list names remain
        return set()


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
    names = sorted(k for k in models if isinstance(k, str) and ba.NAME.fullmatch(k))[:5]       # each name is cut BEFORE the join: a long one hides no other
    row = {"ts": _now(), "stage": a.stage, "model": ",".join(k[:40] for k in names) or "unknown",
           "cost_usd": _num(res.get("total_cost_usd")), **{k: _num(use.get(k)) for k in tokens},
           **{f"{w}_{t}": _util(ev, w) for w in ("five_hour", "seven_day") for t, ev in (("before", before), ("after", after))},
           "duration_ms": _num(res.get("duration_ms")), "num_turns": _num(res.get("num_turns")),
           "tools_called": sorted(t for t in (res.get("_tools_called") or []) if isinstance(t, str) and len(t) <= 100 and ba.NAME.fullmatch(t))[:50],
           "unknown_tools": res["_unknown_tools"] if type(res.get("_unknown_tools")) is int and res["_unknown_tools"] >= 0 else 0}
    ba.append_row(_dir(cfg) / LEDGER, row)


def cmd_tools_audit(cfg, a) -> int:
    """Prints the one-line summary `{"init": N, "drift": [...], "unknown": N}` (names of the checked-in lists only). Exit 2 and one ledger row
    when the init tool list or a call shows a tool outside the allowlist and `ToolSearch`, built-ins included."""
    r = ba.audit(a.stream, a.allow)
    print(json.dumps(r["summary"]))
    if not r["drift"]:
        return 0
    ba.append_row(_dir(cfg) / LEDGER, {"ts": _now(), "stage": "stage1", "drift": sorted(r["drift"])[:50], "unknown_tools": r["unknown"]})
    return 2


def cmd_timeout_mark(cfg, a) -> int:
    if not re.fullmatch(r"[a-z0-9_]{1,20}", a.stage):
        raise br.SchemaError("stage")
    ba.append_row(_dir(cfg) / LEDGER, {"ts": _now(), "stage": a.stage, "timeout": a.stage})


def cmd_refusal(cfg, a) -> int:
    ba.append_row(_dir(cfg) / LEDGER, {"ts": _now(), "stage": "run", "refused": ba.refusal_reason(a.reason)})


def cmd_extract(cfg, a) -> int:
    out = extract(a.stream, a.what, a.last)
    print(out if a.what == "text" else json.dumps(out))


def cmd_status(cfg, a) -> int:
    d = cfg.brief_path          # status only reads: it never creates the directory
    state = ba.as_dict(read_json(d / STATE))
    rows = [r for r in map(_jl, (d / LEDGER).read_text(errors="replace").split("\n") if (d / LEDGER).is_file() else []) if isinstance(r, dict)]
    by_stage = {str(r.get("stage"))[:20]: r.get("cost_usd") for r in rows if "cost_usd" in r}      # str(): a list or object stage must not be a dict key
    costs = ", ".join(f"{s} ${c}" for s, c in sorted(by_stage.items(), key=str))
    print(f"last brief: {state.get('last_brief', 'never')}\nneeds you: {state.get('needs_you_count', 0)}\nledger rows: {len(rows)}")
    print(f"last refusal: {ba.last_refusal(rows)}\nlast stage costs: {costs or 'none'}")


def cmd_conf(cfg, a) -> int:
    if not a.key.startswith("brief_") or not hasattr(cfg, a.key):
        raise ValueError("key")
    print(_dir(cfg) if a.key == "brief_dir" else getattr(cfg, a.key))


VERBS = {"due": ["--force"], "digest-apply": ["src", "--out"], "render": ["src"], "state-mark": [], "status": [],
         "ledger": ["before", "after", "stage", "result"], "extract": ["stream", "--what", "--last"], "conf": ["key"],
         "tools-audit": ["stream", "--allow"], "timeout-mark": ["stage"], "refusal": ["reason"]}


def main(argv=None) -> int:
    ap = ba.OneLineParser(prog="python -m ace_playbook.compile.brief")
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
