"""CLI: `python -m ace_playbook.migrate --legacy-glob G --classified F --out-dir D (--dry-run | --apply)`.

One-shot migration of legacy lessons into the delta log. No network access; every
paraphrase verdict and action comes from the input files. Outputs are private.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import config as cfgmod
from ..store import DeltaLog, SchemaError
from . import apply as applymod
from . import inputs, plan as planmod


def run(args: argparse.Namespace) -> int:
    cfg = cfgmod.load(args.config)
    log = DeltaLog(cfg.deltas, tuple(cfg.deny_terms))
    legacy = inputs.load_legacy(args.legacy_glob)
    records, notes = inputs.load_classified(Path(args.classified))
    overrides, ov_notes = inputs.load_overrides(Path(args.overrides) if args.overrides else None)
    notes = [*legacy.notes, *notes, *ov_notes]
    if args.apply:
        log.verify()
    plan = planmod.build(records, legacy, overrides, log)
    before = applymod.StoreDelta(log.head_hash(), "", len(log.read()), 0) if args.apply else None
    delta = None
    try:
        if args.apply:
            applymod.commit(plan, log)
    finally:  # a failed batch must still leave a report of what did and did not land
        if before:
            delta = applymod.StoreDelta(before.head_before, log.head_hash(), before.count_before,
                                        len(log.read()))
        applymod.write_outputs(plan, legacy, len(records), notes, Path(args.out_dir), delta)
    if args.apply:
        log.verify()
    stored = len(plan.stored())
    mode = "applied" if args.apply else "DRY RUN, store untouched"
    print(f"migrate ({mode}): {len(records)} records, {stored} "
          f"{'stored' if args.apply else 'would be stored'}, reports in {args.out_dir}")
    failed = sum(1 for o in plan.outcomes if o.reason_code == "apply_failed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ace_playbook.migrate", description=__doc__.split("\n")[0])
    ap.add_argument("--legacy-glob", required=True, help="glob of legacy lessons-*.json files")
    ap.add_argument("--classified", required=True, help="classified.jsonl")
    ap.add_argument("--overrides", help="overrides.jsonl")
    ap.add_argument("--config")
    ap.add_argument("--out-dir", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="write reports only, never touch the store")
    mode.add_argument("--apply", action="store_true", help="append to the delta log")
    args = ap.parse_args(argv)
    try:
        return run(args)
    except (SchemaError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
