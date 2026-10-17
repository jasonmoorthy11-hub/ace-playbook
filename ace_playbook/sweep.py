"""Nightly sweep: reflect any transcript that has no reflections-ledger entry."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .inject.ledger import read_jsonl
from .reflect.client import ReflectorClient, log_line
from .reflect.runner import reflect_transcript

MIN_BYTES = 2000


@dataclass
class Candidate:
    path: Path
    source: str  # queue | scan


def reflected_ids(cfg: Config) -> set[str]:
    return {r["session_id"] for r in read_jsonl(cfg.reflections)
            if r.get("status") != "retryable" and r.get("session_id")}


def queue_entries(cfg: Config) -> list[dict]:
    return list(read_jsonl(cfg.queue))


def _eligible(path: Path, done: set[str], idle_s: float, now: float) -> bool:
    if path.suffix != ".jsonl" or "subagents" in path.parts or path.stem in done:
        return False
    st = path.stat()
    return st.st_size >= MIN_BYTES and now - st.st_mtime >= idle_s


def find_candidates(cfg: Config, idle_minutes: float = 60.0, now: float | None = None) -> list[Candidate]:
    """Queued transcripts first, then idle transcripts under transcripts_root."""
    now = time.time() if now is None else now
    done = reflected_ids(cfg)
    seen: dict[Path, Candidate] = {}
    for entry in queue_entries(cfg):
        p = Path(entry.get("transcript_path") or "")
        if p.is_file() and _eligible(p, done, 0, now):
            seen.setdefault(p, Candidate(p, "queue"))
    if cfg.transcripts_root and Path(cfg.transcripts_root).is_dir():
        files = sorted(Path(cfg.transcripts_root).rglob("*.jsonl"), key=lambda f: f.stat().st_mtime)
        for p in files:
            if _eligible(p, done, idle_minutes * 60, now):
                seen.setdefault(p, Candidate(p, "scan"))
    return list(seen.values())


def prune_queue(cfg: Config) -> None:
    """Rewrite the queue without entries whose session has been reflected."""
    if not cfg.queue.exists():
        return
    done = reflected_ids(cfg)
    keep = [e for e in queue_entries(cfg) if Path(e.get("transcript_path") or "").stem not in done]
    tmp = cfg.queue.with_name(cfg.queue.name + ".tmp")
    tmp.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in keep))
    os.replace(tmp, cfg.queue)


def run_sweep(cfg: Config, client: ReflectorClient | None = None, max_n: int = 20,
              dry_run: bool = False, idle_minutes: float = 60.0,
              now: float | None = None) -> list[dict]:
    cands = find_candidates(cfg, idle_minutes, now)[:max_n]
    if dry_run:
        return [{"path": c.path.name, "source": c.source, "status": "would_reflect"} for c in cands]
    if cands and not (cfg.api_key_path and Path(cfg.api_key_path).exists()):
        log_line(cfg, f"sweep SKIPPED no-api-key candidates={len(cands)}")
        return [{"status": "skipped_no_key", "candidates": len(cands)}]
    client = client or ReflectorClient(cfg)
    out = []
    for c in cands:
        res = reflect_transcript(cfg, c.path, client)
        out.append({"path": c.path.name, "source": c.source, "status": res["status"]})
    prune_queue(cfg)
    log_line(cfg, f"sweep done processed={len(out)}")
    return out
