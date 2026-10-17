"""CLI: `python -m ace_playbook.reflect <transcript.jsonl> [--force] [--dry-run]`."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import config as cfgmod
from ..clean import clean_file
from .client import ReflectorClient
from .runner import reflect_transcript


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ace_playbook.reflect")
    ap.add_argument("transcript", type=Path)
    ap.add_argument("--config")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="clean only; no API call")
    args = ap.parse_args(argv)
    cfg = cfgmod.load(args.config)
    if args.dry_run:
        c = clean_file(args.transcript, cfg.tool_result_max_chars,
                       tuple(cfg.untrusted_path_markers))
        print(c.header)
        print(f"turns kept: {len(c.kept_turns)}/{len(c.all_turns)}; body chars: {len(c.body)}")
        return 0
    result = reflect_transcript(cfg, args.transcript, ReflectorClient(cfg), args.force)
    print(json.dumps({k: v for k, v in result.items() if k != "usage"}, sort_keys=True))
    return 0 if result["status"] in ("ok", "already_reflected", "skipped_short") else 1


if __name__ == "__main__":
    raise SystemExit(main())
