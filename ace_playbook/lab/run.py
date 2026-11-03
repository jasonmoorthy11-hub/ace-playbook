"""One lab trial: build the run dir, launch `claude -p` in stream-json mode, drive the turns, save the raw stream (docs/lab.md).

Isolation: `--setting-sources ""` (no user CLAUDE.md, hooks or settings), `--disallowedTools=...` on every trial (Bash, web and agent
tools), the hooks and `plansDirectory` come only from the lab settings file, the prompts go through stdin (never argv), and
ANTHROPIC_API_KEY and CLAUDECODE are removed from the environment. The lesson text is read from the operator's store by id at run time; the
record keeps only `sha256(text)[:16]`. The only run-time reader of the store here.
The process plumbing (pump, turn loop, group stop) is in drive.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from .. import config as cfgmod
from ..canon import now_iso
from ..store import view
from . import drive, exitplan, tasks

ARMS = ("told", "not_told")
HOOK = Path(__file__).resolve().parent / "hooks" / "stop-mid-task-audit.sh"
DROP_ENV = ("ANTHROPIC_API_KEY", "CLAUDECODE")
DENY = "--disallowedTools=Bash,WebFetch,WebSearch,NotebookEdit,Task,Agent"       # ONE argument: the tool flags are variadic, a separate
#                                                                                  value would swallow the next flag


def lesson_text(lesson: str) -> str:
    """The text of the lesson's store row (read-only view); LookupError (no text in the message) when it is not there."""
    cfg = cfgmod.load()
    if not cfg.sqlite.exists():
        raise LookupError("the store view does not exist")
    con = view.open_view(cfg.sqlite)
    try:
        row = con.execute("SELECT text FROM lessons WHERE id = ?", (tasks.LESSON_IDS[lesson],)).fetchone()
    finally:
        con.close()
    if not row or not row["text"]:
        raise LookupError("the lesson is not in the store")
    return row["text"]


def trial_id(model: str, task_id: str, arm: str, rep: int, pilot: bool = False) -> str:
    """Filename-safe id; a pilot trial carries the `pilot.` prefix so a pilot and a main run never share an id."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{'pilot.' if pilot else ''}{model}.{task_id}.{arm}.r{rep}")


def settings(task: tasks.Task, run_dir: Path) -> dict:
    """The lab settings file: the plans directory, and the Stop hook for preclaim tasks. Nothing else."""
    out: dict = {"plansDirectory": str(Path(run_dir) / "plans")}
    if task.lesson == "preclaim":
        out["hooks"] = {"Stop": [{"hooks": [{"type": "command", "command": str(HOOK)}]}]}
    return out


def command(claude: str, model: str, task: tasks.Task, settings_path: Path, arm: str, text: str) -> list[str]:
    argv = [claude, "-p", "--model", model, "--no-session-persistence", "--setting-sources", "", "--settings", str(settings_path),
            "--permission-mode", task.permission_mode, "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            DENY, "--max-turns", "20", "--max-budget-usd", "0.50"]
    if task.lesson == "exitplan":                                            # ExitPlanMode exists headless only over the stdio protocol
        argv += ["--permission-prompt-tool", "stdio"]
    return argv + (["--append-system-prompt", text] if arm == "told" else [])


def private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def open_private(path: Path, append: bool = False):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC) | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "a" if append else "w", encoding="utf-8")


def trial(task: tasks.Task, arm: str, model: str, lesson_text: str, run_dir: Path, data_dir: Path, claude: str = "claude",
          timeout_s: float = 600, rep: int = 0, pilot: bool = False, grace_s: float = 5.0) -> dict:
    """Run one trial; returns its record (no lesson text, only its hash). Errors are recorded in `error`, never raised."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm}")
    tid, run_dir, data_dir = trial_id(model, task.id, arm, rep, pilot), Path(run_dir), Path(data_dir)
    streams = private_dir(private_dir(data_dir) / "streams")
    stream = streams / f"{tid}.jsonl"
    rec = {"trial_id": tid, "task": task.id, "lesson": task.lesson, "arm": arm, "model": model, "rep": rep, "pilot": pilot, "cue": task.cue,
           "multi_claim": task.multi_claim,
           "lesson_sha16": hashlib.sha256(lesson_text.encode()).hexdigest()[:16], "started": now_iso(), "exit_code": None, "results": [],
           "num_turns": [], "costs": [], "permission_denials": 0, "is_error": False, "stream": str(stream), "error": None}
    private_dir(run_dir)
    try:
        work = tasks.build(task, run_dir)
    except Exception as exc:                                                 # a broken spec or a full disk is a recorded error
        rec["error"] = f"build failed: {type(exc).__name__}"
        return rec
    (run_dir / "plans").mkdir(exist_ok=True)
    with open_private(run_dir / "settings.json") as fh:
        json.dump(settings(task, run_dir), fh)
    argv = command(claude, model, task, run_dir / "settings.json", arm, lesson_text)
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    lines: queue.Queue = queue.Queue()
    with open_private(stream) as fh, open_private(run_dir / "stderr.log") as err:
        sink = drive.Sink(fh)
        try:
            proc = subprocess.Popen(argv, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True,
                                    encoding="utf-8", errors="replace", start_new_session=True)
        except OSError as exc:
            rec["error"] = f"spawn failed: {type(exc).__name__}"
            return rec
        pump = threading.Thread(target=drive.pump, args=(proc.stdout, sink, lines), daemon=True)
        pump.start()
        deadline = time.monotonic() + timeout_s
        ctl = exitplan.Control(proc, sink, work) if task.lesson == "exitplan" else None
        try:
            rec["error"] = drive.drive(proc, task, work, lines, deadline, rec, ctl)
            if ctl is not None:
                rec["exitplan_violations"], rec["other_denied"], rec["unsupported_control"] = ctl.violations, ctl.other_denied, ctl.unsupported
            if rec["error"] is None:
                try:
                    proc.stdin.close()
                    proc.wait(max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    rec["error"] = "timeout"
                except OSError:
                    pass
        finally:
            drive.stop(proc, grace_s)
            pump.join(5)
            if not pump.is_alive():
                proc.stdout.close()
            rec["exit_code"] = proc.returncode
            try:
                proc.stdin.close()
            except OSError:
                pass
    return rec
