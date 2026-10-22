"""Nightly sweep: reflect any transcript that has no reflections-ledger entry."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from . import health
from .atomic import write_private
from .config import Config
from .filelock import locked
from .inject.ledger import read_jsonl
from .reflect.client import ReflectorClient, log_line
from .reflect.runner import reflect_transcript
from .store import DeltaLog, SchemaError

MIN_BYTES = 2000
# Per-transcript or sweep-level outcomes that count as a failed night: API errors (including a
# spend/usage cap, which the API answers with a 4xx or 429), a missing key, a broken delta log.
# parse_error, refusal and truncated are deliberately excluded: they are model-output problems
# on one transcript, not a broken setup, and would otherwise raise false alarms.
FAILED = {"retryable", "retry_exhausted", "failed_final", "skipped_no_key", "refused_chain_invalid"}


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
    with locked(cfg.queue):
        done = reflected_ids(cfg)
        keep = [e for e in queue_entries(cfg) if Path(e.get("transcript_path") or "").stem not in done]
        write_private(cfg.queue, "".join(json.dumps(e, sort_keys=True) + "\n" for e in keep))


def run_sweep(cfg: Config, client: ReflectorClient | None = None, max_n: int = 20,
              dry_run: bool = False, idle_minutes: float = 60.0,
              now: float | None = None, notes: list[str] | None = None) -> list[dict]:
    """Reflect pending transcripts, then record the outcome in health.json (not on dry runs); `notes` go into that record.

    An exception still propagates unchanged; health writing never raises.
    """
    problems: list[str] = []
    try:
        out = _sweep(cfg, client, max_n, dry_run, idle_minutes, now, problems)
    except Exception as exc:
        if not dry_run:
            health.record(cfg, False, exc=exc, notes=notes)
        raise
    if not dry_run:
        health.record(cfg, not problems, "; ".join(problems), notes=notes)
    return out


def _sweep(cfg: Config, client: ReflectorClient | None, max_n: int, dry_run: bool,
           idle_minutes: float, now: float | None, problems: list[str]) -> list[dict]:
    cands = find_candidates(cfg, idle_minutes, now)[:max_n]
    if dry_run:
        return [{"path": c.path.name, "source": c.source, "status": "would_reflect"} for c in cands]
    if cands and not (cfg.api_key_path and Path(cfg.api_key_path).exists()):
        log_line(cfg, f"sweep SKIPPED no-api-key candidates={len(cands)}")
        problems.append(f"skipped_no_key: {len(cands)} candidates, no api key file")
        return [{"status": "skipped_no_key", "candidates": len(cands)}]
    try:
        if cfg.deltas.exists():
            DeltaLog(cfg.deltas).verify()
    except SchemaError as e:
        log_line(cfg, f"sweep REFUSED delta log failed verification: {e}")
        problems.append(f"refused_chain_invalid: {e}")
        return [{"status": "refused_chain_invalid", "candidates": len(cands)}]
    client = client or ReflectorClient(cfg)
    out = []
    for c in cands:
        res = reflect_transcript(cfg, c.path, client)
        out.append({"path": c.path.name, "source": c.source, "status": res["status"]})
        if res["status"] in FAILED:
            problems.append(f"{res['status']}: {res.get('error', '')}")
    prune_queue(cfg)
    log_line(cfg, f"sweep done processed={len(out)}")
    return out
