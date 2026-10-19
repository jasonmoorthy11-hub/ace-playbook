# Platform notes (observed, not documented)

Observations about Claude Code behaviour that the design relies on or deliberately avoids.
Each entry names the version and date it was observed; re-check after upgrades.

## Memory directory writes are not surfaced mid-session

Observed 2026-10-19, Claude Code 2.1.302, macOS. During one interactive session, four new
Markdown files and an updated index were written into the project's auto-memory directory
(`~/.claude/projects/<key>/memory/`) with the file tools. No system message or context update
mentioned them for the rest of that session; the index is read at session start only. A fresh
non-interactive session started afterwards did see the new index.

Consequence: anything that must reach a running session has to go through a hook that emits
`additionalContext` (SessionStart, including the `compact` and `clear` matchers), not through a
file written next to the session. The injector therefore re-emits the pinned block on every
SessionStart source instead of expecting a mid-session read.

## SessionEnd work is enqueue-only; no detached child is relied on

The SessionEnd hook (`hooks/sessionend.sh`) appends one queue record and exits within its
5-second budget. Reflection runs later under launchd (`hooks/nightly_sweep.py`), so the design
never depends on a child process surviving an interactive exit or a `/clear`. That question
remains untested here on purpose: nothing in this repo needs the answer.

## Smoke test used

From a project directory with a configured engagement key, a fresh `claude -p` session was asked
to list the `L-` ids in its advisory block. It returned exactly the pinned ids, and
`injections.jsonl` gained one row with that session id, `mode: pinned` and the block size. This is
the end-to-end check for "hooks live"; repeat it after any settings or injector change.
