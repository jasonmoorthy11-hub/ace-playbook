#!/usr/bin/env python3
"""Reflect every transcript without a ledger entry. Run nightly via launchd (see INSTALL.md)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ace_playbook import config as cfgmod  # noqa: E402
from ace_playbook.sweep import run_sweep  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--max", type=int, default=20, dest="max_n")
    ap.add_argument("--idle-minutes", type=float, default=60.0)
    ap.add_argument("--dry-run", action="store_true", help="list candidates; no API calls")
    args = ap.parse_args()
    cfg = cfgmod.load(args.config)
    results = run_sweep(cfg, max_n=args.max_n, dry_run=args.dry_run, idle_minutes=args.idle_minutes)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
