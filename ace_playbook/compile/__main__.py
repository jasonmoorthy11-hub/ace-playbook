"""CLI: `python -m ace_playbook.compile cards|propose|validate|approve|unapprove|label|report|offline|shadow` (docs/design/compiler.md 10).

No network, no model, stdlib only. Exit codes: 0 ok, 1 a proposal failed validation or was not approved (the
step is named), 2 usage, configuration, store or id errors. `approve` and `unapprove` need a terminal; `approve`
appends every approval to the hash-chained `approvals.jsonl` (approvals.py) and `--mode warn|block` is the promotion
ladder (ladder.py, imported on demand); `label` writes human labels (labels.py; `--list` and `--rows` need no
terminal); `report` prints per-proposal shadow counts with labels (report.py; exit 2 on a corpus mix-up);
`offline` is the nightly pass (offline.py; counts only, `--dry-run` writes nothing).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import config as cfgmod
from .. import ttyio
from ..atomic import write_private
from ..canon import now_iso
from . import approvals, demotions, evidence, labels, proposal, report, shadow

LEGACY_BANNER = ("This lesson's stored evidence is self-quoted legacy text; "
                 "the human incident label is the only evidence.")


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


def _ask(prompt: str) -> str:
    """One line from the controlling terminal (not stdin, which may be a pipe); tests monkeypatch this name."""
    return ttyio.ask(prompt).strip()


def _need_tty(what: str) -> bool:
    if sys.stdin.isatty():
        return True
    print(f"error: {what} needs an interactive terminal (stdin is not a TTY)", file=sys.stderr)
    return False


def _confirm(prompts: list[str]) -> bool:
    return all(_ask(a).lower() in ("y", "yes") for a in prompts)


def _provenance(cfg: cfgmod.Config, p: dict) -> bool:
    """Print what the operator is approving; True when a cited card is legacy (the banner is printed too)."""
    print(f"{p['id']}  lesson {p['lesson_id']}  detector {p['check']['detector']}  on {p['event']} matcher {p['matcher']}")
    print(f"  evidence cards: {', '.join(p['provenance']['cards'])}")
    print(f"  source_sha256:  {p['provenance']['source_sha256']}")
    print(f"  message:        {p['message']}")
    legacy = any(c["provenance"] == "legacy" for c in evidence.load_cards(cfg, p["lesson_id"])
                 if c["id"] in p["provenance"]["cards"])
    if legacy:
        print("  " + LEGACY_BANNER)
    return legacy


def _cap(cfg: cfgmod.Config, pid: str) -> str | None:
    """The demotion cap of `pid`; a broken demotions chain or a read error caps at observe, like the hook."""
    try:
        rows, broken = demotions.read_chain(cfg.data_dir)
    except Exception:
        return "observe"
    cap = demotions.effective_cap(rows, pid)
    return demotions.lower("observe", cap) if broken is not None else cap


def cmd_approve(cfg: cfgmod.Config, args: argparse.Namespace) -> int:
    if args.mode != "observe":
        from . import ladder                                  # lazy: the promotion rules stay off the hook path
        return ladder.run(cfg, args, lambda prompt: _ask(prompt), _need_tty, cap=_cap(cfg, args.id))
    if args.opus_reviewed or args.raw_checked:
        print("error: --opus-reviewed and --raw-checked apply to --mode block and warn only", file=sys.stderr)
        return 2
    if not _need_tty("approve"):
        return 2
    src = proposal.proposal_path(cfg, args.id)
    p = proposal.load(src)
    rep = proposal.validate(p, cfg, path=src)
    if not rep.ok:
        _steps(rep)
        print(f"error: validation failed at step '{rep.failed}'", file=sys.stderr)
        return 1
    rows, broken = approvals.read_chain(cfg.data_dir)
    if broken:
        print(f"error: approvals.jsonl chain broken at row {broken}", file=sys.stderr)
        return 1
    dest = shadow.approved_path(cfg, args.id)
    try:
        rec = proposal.load(dest) if dest.exists() else None
        old, mode = shadow.pending_body(rec) if rec is not None else (None, "")
    except proposal.ProposalError:                            # an unreadable old copy is simply replaced
        rec, old, mode = None, None, ""
    verdict = approvals.verify_copy(rec, rows) if old == p and mode == "observe" else None
    if verdict == "ok":
        print(f"already approved: {args.id} (observe)")
        return 0
    legacy = _provenance(cfg, p)                              # the same block and prompts for a fresh approval and a back-fill
    asks = ["Legacy evidence: approve anyway? [y/N] "] if legacy else []
    if verdict == "missing":                                  # predates the chain: record it; "mismatch" is approved afresh below
        if rec["approved_at"] > now_iso():
            print("error: the copy's approved_at is in the future; nothing was written", file=sys.stderr)
            return 1
        if not _confirm([f"Record the approval of {args.id} in observe mode (approved {rec['approved_at']})? [y/N] "] + asks):
            print("error: not approved", file=sys.stderr)
            return 1
        approvals.append(cfg.data_dir, {"ts": rec["approved_at"], "proposal_id": args.id, "mode": "observe",
                                        "proposal_sha256": rec["proposal_sha256"]})
        print(f"ok: recorded the existing observe approval of {args.id} in approvals.jsonl")
        return 0
    if not _confirm([f"Approve {p['id']} in observe mode? [y/N] "] + asks):
        print("error: not approved", file=sys.stderr)
        return 1
    new = shadow.approved_record(p)
    approvals.append(cfg.data_dir, {"ts": new["approved_at"], "proposal_id": p["id"], "mode": "observe",
                                    "proposal_sha256": new["proposal_sha256"]})
    evidence.private_dir(dest.parent)
    write_private(dest, json.dumps(new, indent=1, sort_keys=True) + "\n")
    print(f"ok: approved {p['id']} in observe mode\n{dest}")
    return 0


def cmd_unapprove(cfg: cfgmod.Config, args: argparse.Namespace) -> int:
    if not _need_tty("unapprove"):
        return 2
    dest = shadow.approved_path(cfg, args.id)
    try:
        newest = approvals.newest(approvals.read_chain(cfg.data_dir)[0], args.id)
    except (ValueError, OSError):
        newest = None
    has_copy = dest.exists()
    if not has_copy and (newest is None or newest["mode"] == "revoked"):
        print(f"error: {args.id} is not approved", file=sys.stderr)
        return 1
    if not _confirm([f"Remove the approval of {args.id}? [y/N] "]):
        print("error: not removed", file=sys.stderr)
        return 1
    try:                                                      # a revocation row makes a restored copy "mismatch" in the hook
        rec = proposal.load(dest) if has_copy else None
        sha = rec.get("proposal_sha256") if isinstance(rec, dict) else None
    except proposal.ProposalError:
        sha = None
    try:
        approvals.append(cfg.data_dir, {"proposal_id": args.id, "mode": "revoked",
                                        "proposal_sha256": sha if isinstance(sha, str) and len(sha) == 64 else "-"})
    except (ValueError, OSError) as exc:                      # removing the copy matters more than the row
        print(f"warning: revocation row not written ({exc if isinstance(exc, ValueError) else type(exc).__name__}); "
              f"repair approvals.jsonl, then re-run unapprove {args.id}", file=sys.stderr)
        if not has_copy:
            return 1
    if not has_copy:
        print(f"ok: revocation recorded for {args.id} (no copy present)")
        return 0
    os.unlink(dest)
    print(f"ok: {args.id} is no longer approved (the proposal file is kept)")
    return 0


def cmd_label(cfg: cfgmod.Config, args: argparse.Namespace) -> int:
    return labels.run(cfg, args, lambda prompt: _ask(prompt), _need_tty)


def cmd_report(cfg: cfgmod.Config, args: argparse.Namespace) -> int:
    return report.run(cfg, args.days, args.json)


def cmd_shadow(cfg: cfgmod.Config, args: argparse.Namespace) -> int:
    return shadow.main([args.event], cfg)


def cmd_offline(cfg: cfgmod.Config, args: argparse.Namespace) -> int:
    from . import offline                                     # lazy: the hook path never loads the offline pass
    if not 1 <= args.since <= 3660 or args.idle_minutes < 0:
        print("error: --since must be 1..3660 and --idle-minutes at least 0", file=sys.stderr)
        return 2
    try:
        out = offline.run(cfg, dry_run=args.dry_run, since_days=args.since, idle_minutes=args.idle_minutes)
    except Exception as exc:                                  # type only: no traceback, no paths
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 1
    lines = [f"{k}: {out[k]}" for k in ("transcripts", "skipped", "detector_events")]
    lines += [f"labels: {sum(out['labels'].values())}", *(f"labels.{k}: {v}" for k, v in out["labels"].items())]
    lines += [f"{k}: {out[k]}" for k in ("demotions", "evasion_candidates", "state_removed", "errors", "lesson_check",
                                         "error_rate", "p95_rule")]
    print("\n".join(lines))                                   # counts only: no paths, no hashes
    return 0


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
    ap_ok = sub.add_parser("approve", help="TTY only: copy a validated proposal to proposals/approved/ in observe mode, "
                           "or promote it (warn: 14 days, 5 human TPs, 0 FPs; block: the same again in warn)")
    ap_ok.add_argument("id")
    ap_ok.add_argument("--mode", required=True, choices=("observe", "warn", "block"))
    ap_ok.add_argument("--raw-checked", action="store_true",
                       help="edit_unread_session: each TP was checked against the raw transcript")
    ap_ok.add_argument("--opus-reviewed", metavar="SHA", help="block only: the commit sha of the Opus review (7 to 40 hex)")
    sub.add_parser("unapprove", help="TTY only: remove the approved copy of a proposal").add_argument("id")
    lb = sub.add_parser("label", help="TTY only: record a human incident, tp or fp label; --list and --rows read")
    act = lb.add_mutually_exclusive_group(required=True)
    act.add_argument("--incident", action="store_true", help="the first mapped fire in a session is this lesson's incident "
                     "(needs --lesson and --session)")
    act.add_argument("--tp", metavar="ROW_ID", help="a shadow.jsonl row (see --rows) is a true positive")
    act.add_argument("--fp", metavar="ROW_ID", help="a shadow.jsonl row (see --rows) is a false positive")
    act.add_argument("--list", action="store_true", help="print labels.jsonl and verify its hash chain")
    act.add_argument("--rows", action="store_true", help="print shadow.jsonl rows with their row ids")
    lb.add_argument("--lesson", metavar="L-xxxxxxxx")
    lb.add_argument("--session", metavar="SESSION_ID", help="the raw session id; only its hash is stored")
    lb.add_argument("--note", help=f"at most {labels.NOTE_MAX} characters, scrubbed")
    lb.add_argument("--kind", choices=labels.KINDS, help="with --list: only this label")
    lb.add_argument("--limit", type=int, help="with --rows: how many (default 50, newest last)")
    rp = sub.add_parser("report", help="counts per proposal from shadow rows and labels; synthetic kept apart")
    rp.add_argument("--days", type=int, default=14, help="days in the rows-per-day block (default 14, 1 to 3660)")
    rp.add_argument("--json", action="store_true", help="one JSON object instead of text")
    of = sub.add_parser("offline", help="the nightly pass: detector events, auto labels, demotions, evasion, state cleanup")
    of.add_argument("--dry-run", action="store_true", help="compute and print the counts, write nothing")
    of.add_argument("--since", type=int, default=7, help="only transcripts modified in the last N days (1 to 3660, default 7)")
    of.add_argument("--idle-minutes", type=float, default=60.0, help="skip transcripts modified more recently (default 60)")
    sub.add_parser("shadow", help="the hook entry (what hooks/shadow.sh runs); reads a payload on stdin").add_argument(
        "event", choices=["pre", "stop"])
    args = ap.parse_args(argv)
    try:
        cfg = cfgmod.load(args.config)
        return {"cards": cmd_cards, "propose": cmd_propose, "validate": cmd_validate, "approve": cmd_approve,
                "unapprove": cmd_unapprove, "label": cmd_label, "report": cmd_report, "shadow": cmd_shadow,
                "offline": cmd_offline}[args.cmd](cfg, args)
    except proposal.ProposalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:                                 # includes store.schema.SchemaError
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:                                   # type only: an errno message would carry a path
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
