#!/usr/bin/env python3
"""Run the offline compiler pass, then reflect every transcript without a ledger entry. Run nightly via launchd (see INSTALL.md)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ace_playbook import config as cfgmod  # noqa: E402
from ace_playbook import health  # noqa: E402
from ace_playbook.sweep import run_sweep  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--max", type=int, default=20, dest="max_n")
    ap.add_argument("--idle-minutes", type=float, default=60.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="list reflection candidates; no API calls (the offline pass still runs, see --offline-dry-run)")
    ap.add_argument("--no-offline", action="store_true", help="skip the offline compiler pass")
    ap.add_argument("--offline-dry-run", action="store_true", help="the offline pass computes its counts and writes nothing")
    args = ap.parse_args()
    cfg = cfgmod.load(args.config)
    report, failed = {}, ""
    if not args.no_offline:
        try:                                          # the pass makes no API call, so --dry-run does not stop it
            from ace_playbook.compile import offline
            report["offline"] = offline.run(cfg, dry_run=args.offline_dry_run, idle_minutes=args.idle_minutes)
            failed = f"offline pass reported {report['offline'].get('errors')} errors" if report["offline"].get("errors") else ""
        except Exception as exc:                      # type only; reflection still runs
            report["offline"] = {"error": type(exc).__name__}
            failed = f"offline pass raised {type(exc).__name__}"
    try:
        report["sweep"] = run_sweep(cfg, max_n=args.max_n, dry_run=args.dry_run, idle_minutes=args.idle_minutes)
    finally:
        if failed and not args.offline_dry_run:       # a real offline pass failed, whatever --dry-run says about reflection
            health.record(cfg, False, failed)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
