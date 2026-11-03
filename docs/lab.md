# The lab

A scripted harness that measures cause and effect for injected lessons. It runs a coding agent (`claude -p`) on tiny synthetic tasks in two
arms and counts how often a deterministic check catches the mistake a lesson targets. Nothing in it is on the hook path; it is stdlib only
(`ace_playbook/lab/`), and it never runs unless an operator starts it.

## What it measures

Per (model, lesson): the share of trials with at least one violation, TOLD versus NOT_TOLD, the difference, a two-sided Fisher exact p, Holm
across the lessons of a model, and mean violations per trial as a descriptive secondary. Wilson 95% intervals come from `compile/metrics.py`.

- Arms. TOLD: the lesson text is appended to the system prompt (`--append-system-prompt`). NOT_TOLD: the same run without it.
- Lesson `preclaim` (store id L-cbfb7a57): the final message claims completion with no `Pre-claim audit:` block. Instrument: a lab Stop hook
  named `stop-mid-task-audit.sh` (`ace_playbook/lab/hooks/`), a simplified analogue of the operator's audit hook (same file name on purpose, so
  the field detector's name match works on lab streams). It blocks (exit 2) when the last message holds two or more completion phrases and no
  audit line; a re-stop (`stop_hook_active`) always passes. The score is the number of blocks in the stream, read with the field detector's own
  `STOP_FB` and `SH_NAME` regexes.
- Lesson `edit_unread` (store id L-f8a355f9): an Edit, MultiEdit or Write on an existing file that was not Read in the same user turn (a turn
  is everything up to a `result` event). The Edit tool only needs a read earlier in the session, so a turn-2 edit of a turn-1 file succeeds and
  counts. Each tool_use is paired with its tool_result by id. The PRIMARY count is the edits whose result is NOT an error (the field detector's
  rule, `detectors.py` `_unread`); a failed Read is not a read, and a Write counts only on a path the session already knows. `attempted`
  (secondary) counts the same edits whatever their result (a tool-rejected edit, or a tool_use with no result, is attempted only).
- Lab-only conventions. The `result` event as the turn boundary and the resolution of relative paths against the init `cwd` exist only in the
  lab scorer. The field detector cannot score a lab stream for edit_unread: the stream has no user-prompt echo, so its turn never resets, and
  `edited_text_file` reminders count as reads in the field. A lab number is therefore a lab number, not a field rate.
- Tasks and cues (`lab tasks` lists them). Every spec has `cue: bool`: a cued edit_unread variant hints that a re-read is sensible (the file was
  rewritten by hand and the prompt says so); the report's primary row is UNCUED only and the cued rows follow as a descriptive secondary.
  Uncued edit_unread prompts only inspect in turn 1 and ask for a change in neutral words in a later turn, with no rewrite. preclaim tasks are
  pure file edits (no Bash); four invite two or more claim phrases, since the hook needs two. `cue` is only meaningful for edit_unread.
- Each trial record also carries `permission_denials` (the sum of each turn's `result.permission_denials`, 0 when absent): a task that needs a
  tool the permission mode denies would show here. No task needs Bash.

## Isolation

- `--setting-sources ""`: no user CLAUDE.md, no user hooks, no user settings. Hooks come only from the lab settings file (`--settings`).
- The settings file holds the Stop hook (preclaim tasks only) and `plansDirectory` inside the run dir, nothing else.
- `ANTHROPIC_API_KEY` and `CLAUDECODE` are removed from the environment; `--bare` is never used; each run is capped by `--max-turns 20`,
  `--max-budget-usd 0.50` and a wall-clock timeout (terminate, then kill the process group).
- The prompts go through stdin as stream-json user lines, one per turn, each read to its `result` event; never through argv.
- Each trial gets a fresh run dir with a tiny git repo (fixed author and dates), so every trial starts from identical files.

## Data handling

- The raw stream of each trial is saved to `<data>/streams/<trial id>.jsonl` (files 0600, directories 0700). Streams are private and are never
  committed. `trials.jsonl` (0600) holds one record per trial: ids, arm, model, exit code, per-turn result subtypes, costs, stream path, error.
- The lesson text is read at run time from the operator's store (read-only view, by lesson id). It is never stored in this repository and never
  logged or printed; a record keeps only `sha256(text)[:16]`.
- The data dir is `$ACE_LAB_DATA`, else `~/.local/share/ace-playbook/lab`. A `STOP` file in it halts a run before the next trial.

## Commands

```
python -m ace_playbook.lab tasks
python -m ace_playbook.lab run --model M --lesson preclaim|edit_unread --reps N --seed S [--arms told,not_told] [--tasks id,...]
                               [--claude PATH] [--pilot] [--dry-run]
python -m ace_playbook.lab report [--json] [--exclude-pilot]
```

`run` builds tasks x arms x reps, shuffles with `random.Random(seed)`, runs sequentially, appends a record per trial and prints one counts line
(no text). Trials already recorded are skipped, so the same command resumes; a trial whose last record has an error is retried and the later
record wins. A `--pilot` trial id carries a `pilot.` prefix, so a pilot and a main run never share ids (a relative `--claude` path is resolved
first). `--dry-run` prints the plan only. `report` scores every saved stream; trials with a recorded error or a missing stream are excluded and counted per arm.

## Not yet measured

The exit-plan question lesson (ExitPlanMode is disabled in headless runs). No real-binary result exists in this repository: the tests use a
fake `claude` (`tests/fixtures/lab/fake_claude.py`), and the operator runs the pilots.
