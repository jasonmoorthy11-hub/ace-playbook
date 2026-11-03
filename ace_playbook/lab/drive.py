"""The process plumbing of a lab trial: pump the child's stdout into the saved stream and a queue, wait for each `result`, drive the turns,
and stop the whole process group (docs/lab.md). `ctl` is an optional control-protocol handler (exitplan.py) with `start()`, `turn(text)` and
`handle(event)`; the other lessons pass None.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time

from . import tasks


class Sink:
    """The saved stream file, written by the pump thread and by the control handler, one whole line at a time."""

    def __init__(self, fh) -> None:
        self.fh, self.lock = fh, threading.Lock()

    def write(self, raw: str) -> None:
        with self.lock:
            self.fh.write(raw)
            self.fh.flush()


def pump(stdout, sink: Sink, lines: queue.Queue) -> None:
    for raw in stdout:
        sink.write(raw)
        lines.put(raw)
    lines.put(None)


def await_result(lines: queue.Queue, deadline: float, ctl=None):
    """(result event, None), or (None, why) on the timeout or when the process closed its stdout first. Control requests go to `ctl`."""
    while True:
        try:
            raw = lines.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            return None, "timeout"
        if raw is None:
            return None, "exited before the result"
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "result":
            return ev, None
        if ctl is not None and ev.get("type") == "control_request":
            try:
                ctl.handle(ev)
            except OSError:
                return None, "stdin closed"


def signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop(proc: subprocess.Popen, grace_s: float) -> None:
    """SIGTERM the process group, wait `grace_s` for the leader, then ALWAYS SIGKILL the group (a TERM-ignoring child must not survive)."""
    signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(grace_s)
    except subprocess.TimeoutExpired:
        pass
    signal_group(proc, signal.SIGKILL)
    proc.wait()


def send(proc: subprocess.Popen, obj: dict) -> None:
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def drive(proc, task, work, lines, deadline, rec, ctl=None) -> str | None:
    """Send each turn, wait for its result, run the `between` hook. Returns an error string or None (an `is_error` or non-success result is one)."""
    try:
        if ctl is not None:
            ctl.start()
    except OSError:
        return "stdin closed"
    for i, prompt in enumerate(task.turns):
        try:
            if ctl is not None:
                ctl.turn(prompt)
            send(proc, {"type": "user", "message": {"role": "user", "content": prompt}})
        except OSError:
            return "stdin closed"
        res, why = await_result(lines, deadline, ctl)
        if res is None:
            return why
        rec["results"].append(res.get("subtype"))
        rec["num_turns"].append(res.get("num_turns"))
        rec["is_error"] = rec["is_error"] or bool(res.get("is_error"))
        if isinstance(res.get("total_cost_usd"), (int, float)):
            rec["costs"].append(res["total_cost_usd"])
        denials = res.get("permission_denials")
        rec["permission_denials"] += len(denials) if isinstance(denials, list) else 0
        if res.get("is_error") or res.get("subtype") != "success":                  # a failed turn is an error: excluded, then retried
            return f"result error: {res.get('subtype')}"
        try:
            tasks.between(task, work, i)
        except Exception as exc:                                             # a broken spec is a recorded error, never a crash
            return f"between failed: {type(exc).__name__}"
    return None
