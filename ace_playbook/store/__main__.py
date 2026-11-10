"""CLI: `python -m ace_playbook.store rebuild|verify|list|search|pin|unpin|exempt|unexempt|stats|promote|provisional`."""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from .. import config as cfgmod
from . import DeltaLog, promote, view
from .schema import SchemaError


FLAG_VERBS = {"pin": ("pinned", True), "unpin": ("pinned", False),
              "exempt": ("holdout_exempt", True), "unexempt": ("holdout_exempt", False)}


def _lesson_line(row) -> str:
    pin = "*" if row["pinned"] else " "
    exempt = " [exempt]" if row["holdout_exempt"] else ""
    scope = f"{promote.shown(row['scope_type'])}:{promote.shown(row['scope_key'] or '-')}"
    shown = promote.shown
    return (f"{pin} {row['id']} [{shown(row['status'])}]{exempt} ({scope}) {shown(row['text'])} "
            f"(applies when: {shown(row['applies_when'])})")


def run(args: argparse.Namespace) -> int:
    cfg = cfgmod.load(args.config)
    log = DeltaLog(cfg.deltas, tuple(cfg.deny_terms))
    if args.cmd == "verify":
        print(f"ok: {log.verify()} records, chain intact")
        return 0
    if args.cmd == "provisional":
        return promote.provisional(log, args.limit)
    if args.cmd == "promote":
        rc = promote.promote(log, args)
        if rc == 0 and not args.dry_run:
            try:
                log.rebuild_view(cfg.sqlite)
            except Exception:
                print("promoted; view rebuild failed, run `rebuild`")
        return rc
    if args.cmd in FLAG_VERBS:
        flag, value = FLAG_VERBS[args.cmd]
        log.append({"op": "PATCH", "lesson_id": args.lesson_id, "fields": {flag: value}})
    if args.cmd == "rebuild" or args.cmd in FLAG_VERBS or view.view_head(cfg.sqlite) != log.head_hash():
        state = log.rebuild_view(cfg.sqlite)
        if args.cmd == "rebuild":
            print(f"rebuilt view: {len(state)} lessons -> {cfg.sqlite.name}")
            return 0
    con = view.open_view(cfg.sqlite)
    if args.cmd == "list":
        q = "SELECT * FROM lessons" + (" WHERE status=?" if args.status else "") + " ORDER BY id"
        rows = con.execute(q, (args.status,) if args.status else ()).fetchall()
        print("\n".join(_lesson_line(r) for r in rows))
    elif args.cmd == "search":
        print("\n".join(_lesson_line(r) for r in view.search(con, args.query)))
    elif args.cmd == "stats":
        c = Counter(r["status"] for r in con.execute("SELECT status FROM lessons"))
        print(dict(c))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ace_playbook.store")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("rebuild", "verify", "stats"):
        sub.add_parser(name)
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    sub.add_parser("search").add_argument("query")
    for name in FLAG_VERBS:
        sub.add_parser(name).add_argument("lesson_id")
    pr = sub.add_parser("promote", help="operator-only: provisional -> active with a typed quote")
    pr.add_argument("lesson_id")
    pr.add_argument("--evidence-quote",
                    help="your own words (12-400 chars); omit to be prompted on the terminal")
    pr.add_argument("--dry-run", action="store_true")
    sub.add_parser("provisional", help="list provisional lessons, newest first").add_argument(
        "--limit", type=int)
    try:
        return run(ap.parse_args(argv))
    except SchemaError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
