"""CLI of the lab: `python -m ace_playbook.lab run|report|tasks` (docs/lab.md). Data dir: $ACE_LAB_DATA, else ~/.local/share/ace-playbook/lab.
Exit codes: 0 ok (or stopped by the STOP file), 2 usage or unreadable store. No lesson text is ever printed.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
from pathlib import Path

from . import run, score, stats, tasks


def data_dir() -> Path:
    return Path(os.environ.get("ACE_LAB_DATA") or Path.home() / ".local" / "share" / "ace-playbook" / "lab")


def recorded(trials: Path) -> set[str]:
    """Trial ids whose LAST record has no error; an errored trial is not recorded, so a resume retries it (a later record wins)."""
    last: dict[str, bool] = {}
    if trials.exists():
        for line in trials.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
                last[rec["trial_id"]] = not rec.get("error")
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    return {tid for tid, ok in last.items() if ok}


def plan(args) -> list[tuple[tasks.Task, str, int]]:
    """tasks x arms x reps, shuffled by random.Random(seed); ValueError names an unknown task, arm or a bad rep count."""
    if args.reps < 1:
        raise ValueError("reps must be at least 1")
    chosen = [t for t in tasks.TASKS if t.lesson == args.lesson]
    if args.tasks:
        wanted = args.tasks.split(",")
        unknown = [w for w in wanted if w not in {t.id for t in chosen}]
        if unknown:
            raise ValueError(f"unknown task for {args.lesson}: {','.join(unknown)}")
        chosen = [t for t in chosen if t.id in wanted]
    arms = args.arms.split(",")
    if not arms or any(a not in run.ARMS for a in arms):
        raise ValueError(f"arms must be a comma list of {','.join(run.ARMS)}")
    items = [(t, a, r) for t in chosen for a in arms for r in range(args.reps)]
    random.Random(args.seed).shuffle(items)
    return items


def counts_line(i: int, n: int, rec: dict, lesson: str) -> str:
    try:
        sc = f"violations={score.score(rec['stream'], lesson)['violations']}"
    except OSError:
        sc = "violations=?"
    err = f" error={rec['error']}" if rec["error"] else ""
    return f"[{i}/{n}] {rec['trial_id']} exit={rec['exit_code']} results={','.join(str(r) for r in rec['results'])} {sc}{err}"


def cmd_run(args) -> int:
    try:
        items = plan(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    data = data_dir()
    done = recorded(data / "trials.jsonl")
    ids = [run.trial_id(args.model, t.id, a, r, args.pilot) for t, a, r in items]
    claude = str(Path(args.claude).resolve()) if "/" in args.claude else args.claude            # the work dir becomes the cwd
    if args.dry_run:
        for i, tid in enumerate(ids, 1):
            print(f"[{i}/{len(ids)}] {tid}" + (" (recorded, skipped)" if tid in done else ""))
        print(f"plan: {len(ids)} trials, {sum(t in done for t in ids)} already recorded; nothing was run")
        return 0
    try:
        text = run.lesson_text(args.lesson)
    except (LookupError, OSError, ValueError) as exc:
        print(f"error: lesson text unavailable ({type(exc).__name__})", file=sys.stderr)
        return 2
    run.private_dir(data)
    for i, ((task, arm, rep), tid) in enumerate(zip(items, ids), 1):
        if (data / "STOP").exists():
            print("stopped: STOP file present")
            return 0
        if tid in done:
            continue
        run_dir = data / "runs" / tid
        shutil.rmtree(run_dir, ignore_errors=True)
        rec = run.trial(task, arm, args.model, text, run_dir, data, claude=claude, rep=rep, pilot=args.pilot)
        with run.open_private(data / "trials.jsonl", append=True) as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        print(counts_line(i, len(ids), rec, args.lesson), flush=True)
    return 0


def cmd_tasks(args) -> int:
    for t in tasks.TASKS:
        print(f"{t.id:<16} {t.lesson:<12} turns={len(t.turns)} files={len(t.files)}" + (" cue" if t.cue else "") + (" multi_claim" if t.multi_claim else ""))
    return 0


def cmd_report(args) -> int:
    rows, excluded = stats.load_rows(data_dir() / "trials.jsonl", args.exclude_pilot)
    groups, cued, single = stats.summarize(rows), stats.summarize(rows, cued=True), stats.summarize(rows, single=True)
    bad, out = stats.mismatches(rows), stats.outside(data_dir() / "trials.jsonl", args.exclude_pilot)
    print(json.dumps({"groups": groups, "cued": cued, "single": single, "excluded": excluded, "exitplan_mismatches": bad,
                      "outside_run_dir_tool_inputs": out}, indent=1, sort_keys=True) if args.json
          else stats.render(groups, excluded, cued, single, bad, out))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ace_playbook.lab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    resume = "Trials already recorded are skipped, but a trial whose last record has an error is retried (a later record wins)."
    p = sub.add_parser("run", help="run trials (tasks x arms x reps, shuffled by --seed), appending to trials.jsonl", description=resume)
    p.add_argument("--model", required=True)
    p.add_argument("--lesson", required=True, choices=sorted(tasks.LESSON_IDS))
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--arms", default=",".join(run.ARMS))
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--tasks", help="comma list of task ids (default: all of the lesson)")
    p.add_argument("--claude", default="claude")
    p.add_argument("--pilot", action="store_true", help="mark the records as pilot trials (report --exclude-pilot drops them)")
    p.add_argument("--dry-run", action="store_true", help="print the plan only")
    p.set_defaults(func=cmd_run)
    sub.add_parser("tasks", help="list task ids and lessons").set_defaults(func=cmd_tasks)
    p = sub.add_parser("report", help="score every recorded stream and print the per-model, per-lesson statistics")
    p.add_argument("--json", action="store_true")
    p.add_argument("--exclude-pilot", action="store_true")
    p.set_defaults(func=cmd_report)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
