"""CLI: `python -m ace_playbook.scrub check|scan`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .check import list_files, load_allow, numbered, scan_files
from .engine import LESSON, scan


def _check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    files = args.paths or list_files(root, staged=args.staged)
    findings = scan_files(root, files, args.mode, load_allow(root), tuple(args.deny_term),
                          staged=args.staged)
    for f in findings:
        print(f.render())
    outside = [f for f in findings if not f.allowlisted]
    print(f"scrub check ({args.mode}): {len(findings)} finding(s), "
          f"{len(outside)} outside allowlisted fixtures")
    return 1 if findings else 0


def _scan_stdin(args: argparse.Namespace) -> int:
    text = sys.stdin.read()
    hits = scan(text, LESSON, tuple(args.deny_term))
    for line, h in numbered(text, hits):
        print(f"line {line}: {h.rule} ({h.layer}) fp={h.fingerprint}")
    return 1 if hits else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ace_playbook.scrub")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="scan a repo (pre-commit or audit mode)")
    c.add_argument("--mode", choices=["pre-commit", "audit"], default="pre-commit")
    c.add_argument("--staged", action="store_true", help="only staged files")
    c.add_argument("--root", default=".")
    c.add_argument("--deny-term", action="append", default=[])
    c.add_argument("paths", nargs="*")
    c.set_defaults(fn=_check)
    s = sub.add_parser("scan", help="scan stdin with the lesson profile")
    s.add_argument("--deny-term", action="append", default=[])
    s.set_defaults(fn=_scan_stdin)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
