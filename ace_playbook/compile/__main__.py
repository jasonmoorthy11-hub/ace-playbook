"""CLI: `python -m ace_playbook.compile cards|propose|validate` (docs/design/compiler.md 10, steps 2 and 3).

No network, no model, stdlib only. Exit codes: 0 ok, 1 a proposal failed validation (the step is named),
2 usage, configuration, store or id errors. The review, approve and label commands come in later steps.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import config as cfgmod
from ..store.schema import SchemaError
from . import evidence, proposal


def _steps(rep: proposal.Report) -> None:
    for step, ok, detail in rep.results:
        print(f"  [{'ok' if ok else '--' if ok is None else 'FAIL'}] {step}" + ("" if ok else f": {detail}"))


def cmd_cards(cfg: cfgmod.Config, args: argparse.Namespace) -> int:
    cards, refused = evidence.build_cards(cfg, args.lesson)
    print(f"{'id':<10} {'lesson':<11} {'provenance':<11} replayable")
    for c in cards:
        print(f"{c['id']:<10} {c['lesson_id']:<11} {c['provenance']:<11} {str(c['replayable']).lower()}")
    for r in refused:
        print(f"refused    {r['lesson_id']:<11} {r['reason']}" + (f" (label ref {r['ref']})" if "ref" in r else ""))
    return 0


def cmd_propose(cfg: cfgmod.Config, args: argparse.Namespace) -> int:
    cards, _ = evidence.build_cards(cfg, args.lesson, write=not args.dry_run)
    known = {c["id"]: c for c in cards}
    p = proposal.build(cfg, args.lesson, args.detector, cards)
    if args.dry_run:
        rep = proposal.validate(p, cfg, known)
        _steps(rep)
        print(json.dumps(p, indent=1, sort_keys=True))
        return 0 if rep.ok else 1
    try:
        path = proposal.write(cfg, p, known)
    except proposal.ProposalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if exc.report:
            _steps(exc.report)
        return 1
    print(f"ok: all {len(proposal.STEPS)} validation steps passed")
    print(path)
    return 0


def cmd_validate(cfg: cfgmod.Config, args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        p = proposal.load(path)
    except proposal.ProposalError as exc:
        rep = proposal.Report([(s, False if i == 0 else None, exc.detail if i == 0 else "skipped")
                               for i, s in enumerate(proposal.STEPS)])
    else:
        rep = proposal.validate(p, cfg, path=path)
    _steps(rep)
    if rep.ok:
        print(f"ok: {len(proposal.STEPS)} of {len(proposal.STEPS)} steps passed")
        return 0
    print(f"error: validation failed at step '{rep.failed}'", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ace_playbook.compile")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cards", help="rebuild evidence cards and print them").add_argument("--lesson")
    pr = sub.add_parser("propose", help="build, validate and write a spec-only proposal")
    pr.add_argument("--lesson", required=True)
    pr.add_argument("--detector", required=True)
    pr.add_argument("--dry-run", action="store_true", help="print the proposal, write nothing")
    sub.add_parser("validate", help="run the ordered validation on a proposal file").add_argument("path")
    args = ap.parse_args(argv)
    try:
        cfg = cfgmod.load(args.config)
        return {"cards": cmd_cards, "propose": cmd_propose, "validate": cmd_validate}[args.cmd](cfg, args)
    except proposal.ProposalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (SchemaError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:                                   # type only: an errno message would carry a path
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
