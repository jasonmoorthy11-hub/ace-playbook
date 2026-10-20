"""CLI of the compiler: cards, propose, validate, approve, unapprove, keygen, label, report, status, digest, dashboard, offline, shadow, seal.

No network, no model, stdlib only. Exit codes: 0 ok, 1 refused or failed validation, 2 usage, configuration, store or id errors. `approve`, `unapprove` and
`keygen` need a terminal (`approve --mode warn|block` is the promotion ladder, ladder.py). `label --queue` is the blind label queue (queue.py).
`report`, `status`, `digest` and `dashboard` only read; the last three register their own parsers and `run` callables. `offline` is the nightly pass.
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
from . import approvals, chains, chains_cli, chains_io, dashboard, digest, evidence, labels, modes, proposal, report, seal, shadow, status, synth

LEGACY_BANNER = "This lesson's stored evidence is self-quoted legacy text; the human incident label is the only evidence."


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
    print(f"{p['id']}  lesson {p['lesson_id']}  detector {p['check']['detector']}  on {p['event']} matcher {p['matcher']}\n"
          f"  evidence cards: {', '.join(p['provenance']['cards'])}\n  source_sha256:  {p['provenance']['source_sha256']}\n"
          f"  message:        {p['message']}")
    legacy = any(c["provenance"] == "legacy" for c in evidence.load_cards(cfg, p["lesson_id"])
                 if c["id"] in p["provenance"]["cards"])
    if legacy:
        print("  " + LEGACY_BANNER)
    return legacy


def cmd_approve(cfg: cfgmod.Config, args: argparse.Namespace) -> int:
    if args.mode != "observe":
        from . import ladder                                  # lazy: the promotion rules stay off the hook path
        return ladder.run(cfg, args, lambda prompt: _ask(prompt), _need_tty)
    if args.opus_reviewed or args.raw_checked:
        print("error: --opus-reviewed and --raw-checked apply to --mode block and warn only", file=sys.stderr)
        return 2
    if not _need_tty("approve"):
        return 2
    ring = chains_io.need_key(cfg, approvals)                    # KeyMissing: the chain has a v2 row and the key is gone (exit 1)
    src = proposal.proposal_path(cfg, args.id)
    p = proposal.load(src)
    rep = proposal.validate(p, cfg, path=src)
    if not rep.ok:
        _steps(rep)
        print(f"error: validation failed at step '{rep.failed}'", file=sys.stderr)
        return 1
    rows, broken = approvals.read_chain(cfg.data_dir, ring)
    if broken is not None:
        print(f"error: approvals.jsonl chain broken {chains.where(broken)}", file=sys.stderr)
        return 1
    dest = shadow.approved_path(cfg, args.id)
    try:
        rec = proposal.load(dest) if dest.exists() else None
        old, mode = shadow.pending_body(rec) if rec is not None else (None, "")
    except proposal.ProposalError:                            # an unreadable old copy is simply replaced
        rec, old, mode = None, None, ""
    verdict = approvals.verify_copy(rec, rows) if old == p and mode == "observe" else None
    drows, dbroken = modes.read_demotions(cfg.data_dir, ring)
    live = modes.in_force(drows, args.id, rows, now_iso(), broken)
    if verdict == "missing" and (dbroken is not None or any(r["proposal_id"] == args.id for r in drows)):
        verdict = None          # no back-fill under demotions, or an unreadable file (the copy's stamp is agent-writable)
    if verdict == "ok" and not [r for r in live if modes.lifted(r, {"ts": now_iso()})]:      # else a newer approval lifts them
        print("\n".join(filter(None, [modes.chain_notice(dbroken),
                                  modes.capped(live, now_iso()) if live else f"already approved: {args.id} (observe)"])))
        return 0
    asks = ["Legacy evidence: approve anyway? [y/N] "] if _provenance(cfg, p) else []      # same block for approval and back-fill
    if verdict == "missing":                                  # predates the chain: record it; "mismatch" is approved afresh below
        if rec["approved_at"] > now_iso():
            print("error: the copy's approved_at is in the future; nothing was written", file=sys.stderr)
            return 1
        if not _confirm([f"Record the approval of {args.id} in observe mode (approved {rec['approved_at']})? [y/N] "] + asks):
            print("error: not approved", file=sys.stderr)
            return 1
        approvals.append(cfg.data_dir, {"ts": rec["approved_at"], "proposal_id": args.id, "mode": "observe",
                                        "proposal_sha256": rec["proposal_sha256"]}, ring)
        print(f"ok: recorded the existing observe approval of {args.id} in approvals.jsonl")
        return 0
    new = shadow.approved_record(p)
    lift = [r for r in live if modes.lifted(r, {"ts": new["approved_at"]})]
    if dbroken is not None:
        print("  " + modes.chain_notice(dbroken))
    for r in lift:                                            # a fresh observe row lifts every older demotion: show each
        print(f"  lifts demotion: {r['to_mode']}  {r['cause']}  {r['ts']}")
    if rest := [r for r in live if r not in lift]:            # e.g. a future-dated row: this approval leaves the cap in place
        print("  still " + modes.capped(rest, new["approved_at"]))
    if not _confirm([f"Approve {p['id']} in observe mode{f', lifting {len(lift)} demotion row(s)' if lift else ''}? [y/N] "] + asks):
        print("error: not approved", file=sys.stderr)
        return 1
    approvals.append(cfg.data_dir, {"ts": new["approved_at"], "proposal_id": p["id"], "mode": "observe",
                                    "proposal_sha256": new["proposal_sha256"]}, ring)
    evidence.private_dir(dest.parent)
    write_private(dest, json.dumps(new, indent=1, sort_keys=True) + "\n")
    print(f"ok: approved {p['id']} in observe mode\n{dest}")
    return 0


def cmd_unapprove(cfg: cfgmod.Config, args: argparse.Namespace) -> int:
    if not _need_tty("unapprove"):
        return 2
    try:
        ring, missing = chains_io.need_key(cfg, approvals), False
    except chains.KeyMissing:                                 # need_key printed the keygen line: only the revocation row is refused
        ring, missing = None, True
    dest = shadow.approved_path(cfg, args.id)
    try:
        newest = approvals.newest(approvals.read_chain(cfg.data_dir, ring)[0], args.id)
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
                                        "proposal_sha256": sha if isinstance(sha, str) and len(sha) == 64 else "-"}, ring)
    except (ValueError, OSError) as exc:                      # removing the copy matters more than the row
        if not missing:
            print(f"warning: revocation row not written ({exc if isinstance(exc, ValueError) else type(exc).__name__}); "
                  f"repair approvals.jsonl, then re-run unapprove {args.id}", file=sys.stderr)
        if not has_copy:
            return 1
    if not has_copy:
        print(f"ok: revocation recorded for {args.id} (no copy present)")
        return 0
    os.unlink(dest)
    print(f"ok: {args.id} is no longer approved (the proposal file is kept)")
    return int(missing)                                       # 1 when the revocation row was refused for want of the key


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
    lines = [f"{k}: {out[k]}" for k in ("transcripts", "skipped", "detector_events", "sessions")]
    lines += [f"labels: {sum(out['labels'].values())}", *(f"labels.{k}: {v}" for k, v in out["labels"].items())]
    lines += [f"{k}: {out[k]}" for k in ("demotions", "evasion_candidates", "state_removed", "errors", "lesson_check", "error_rate", "p95_rule")]
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
    ap_ok.add_argument("--raw-checked", action="store_true", help="edit_unread_session: each TP was checked against the raw transcript")
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
    act.add_argument("--queue", action="store_true", help="the blind label queue: unlabelled fires, oldest first, one key each (needs a terminal)")
    lb.add_argument("--lesson", metavar="L-xxxxxxxx")
    lb.add_argument("--session", metavar="SESSION_ID", help="the raw session id; only its hash is stored")
    lb.add_argument("--note", help=f"at most {labels.NOTE_MAX} characters, scrubbed")
    lb.add_argument("--kind", choices=labels.KINDS, help="with --list: only this label")
    lb.add_argument("--limit", type=int, help="with --rows: how many (default 50, newest last); with --queue: how many cards (default 50)")
    lb.add_argument("--all", action="store_true", help="with --queue: also the fires of retired proposals (left out by default)")
    rp = sub.add_parser("report", help="counts per proposal from shadow rows and labels; synthetic kept apart")
    rp.add_argument("--days", type=int, default=14, help="days in the rows-per-day block (default 14, 1 to 3660)")
    rp.add_argument("--json", action="store_true", help="one JSON object instead of text")
    for verb in (status, digest, dashboard): verb.add_parser(sub)       # noqa: E701 - the read-side verbs own their arguments
    of = sub.add_parser("offline", help="the nightly pass: detector events, auto labels, demotions, evasion, state cleanup")
    of.add_argument("--dry-run", action="store_true", help="compute and print the counts, write nothing")
    of.add_argument("--since", type=int, default=7, help="only transcripts modified in the last N days (1 to 3660, default 7)")
    of.add_argument("--idle-minutes", type=float, default=60.0, help="skip transcripts modified more recently (default 60)")
    kg = sub.add_parser("keygen", help="TTY only: create the chain key (HMAC key of approvals.jsonl and demotions.jsonl, never overwritten), "
                        "re-seal both chains under it and sign their heads")
    kg.add_argument("--rehead", action="store_true", help="rewrite the signed chain heads from the verified chains after you confirm the rows; needs the key")
    sub.add_parser("seal", help="freeze the pre-registration's instruments: --dry-run prints the block").add_argument("--dry-run", action="store_true")
    sub.add_parser("replay-synthetic", help="replay the frozen corpus; exit 1 on a disagreement").add_argument("--out", help="rows file")
    sub.add_parser("shadow", help="the hook entry (what hooks/shadow.sh runs); reads a payload on stdin").add_argument("event", choices=["pre", "stop"])
    args = ap.parse_args(argv)
    try:
        cfg = cfgmod.load(args.config)
        return (getattr(args, "run", None) or {
            "cards": cmd_cards, "propose": cmd_propose, "validate": cmd_validate, "approve": cmd_approve, "unapprove": cmd_unapprove,
            "label": lambda c, a: labels.run(c, a, lambda prompt: _ask(prompt), _need_tty), "report": lambda c, a: report.run(c, a.days, a.json),
            "shadow": lambda c, a: shadow.main([a.event], c), "offline": cmd_offline, "replay-synthetic": lambda c, a: synth.run(c, a.out),
            "keygen": lambda c, a: chains_cli.keygen(c, _need_tty, a.rehead, lambda prompt: _ask(prompt)),
            "seal": lambda c, a: seal.run(c, a, lambda prompt: _ask(prompt), _need_tty)}[args.cmd])(cfg, args)
    except proposal.ProposalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except chains.KeyMissing:                                 # the operator line (naming keygen) was printed by need_key
        return 1
    except ValueError as exc:                                 # includes store.schema.SchemaError
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:                                   # type only: an errno message would carry a path
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
