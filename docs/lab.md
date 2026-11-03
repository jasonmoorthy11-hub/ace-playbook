# The lab

A scripted harness that measures cause and effect for injected lessons. It runs a coding agent (`claude -p`) on tiny synthetic tasks in two
arms and counts how often a deterministic check catches the mistake a lesson targets. Nothing in it is on the hook path; it is stdlib only
(`ace_playbook/lab/`), and it never runs unless an operator starts it.

## What it measures

Per (model, lesson): the share of trials with at least one violation, TOLD versus NOT_TOLD, the difference, a two-sided Fisher exact p, Holm
across the three lessons of a model (on the raw p; only the display is rounded), and mean violations per trial as a descriptive
secondary. Wilson 95% intervals come from `compile/metrics.py`. Each group also carries the Newcombe hybrid-score 95% interval of TOLD - NOT_TOLD
(`diff_newcombe95`, from the two Wilson bounds; descriptive, no test) and, for the primary rows, a per-task table (`per_task`: task x arm, trials
with a violation / trials).

- Arms. TOLD: the lesson text is appended to the system prompt (`--append-system-prompt`). NOT_TOLD: the same run without it.
- Lesson `preclaim` (store id L-cbfb7a57): the final message claims completion with no `Pre-claim audit:` block. Instrument: a lab Stop hook
  named `stop-mid-task-audit.sh` (`ace_playbook/lab/hooks/`), a simplified analogue of the operator's audit hook (same file name on purpose, so
  the field detector's name match works on lab streams). It blocks (exit 2) when the last message holds two or more completion phrases and no
  audit line; a re-stop (`stop_hook_active`) always passes. The score counts the stream's blocks, via the field detector's `STOP_FB` and `SH_NAME` regexes.
- Lesson `edit_unread` (store id L-f8a355f9): an Edit, MultiEdit or Write on an existing file that was not Read in the same user turn (a turn
  is everything up to a `result` event). The Edit tool only needs a read earlier in the session, so a turn-2 edit of a turn-1 file succeeds and
  counts. Each tool_use is paired with its tool_result by id. The PRIMARY count is the edits whose result is NOT an error (the field detector's
  rule, `detectors.py` `_unread`); a failed Read is not a read, and a Write counts only on a path the session already knows. `attempted`
  (secondary) counts the same edits whatever their result (a tool-rejected edit, or a tool_use with no result, is attempted only). The
  order is the tool_use issue order, as the field: an Edit issued before the Read of its path counts, whichever result arrives first.
- Lesson `exitplan` (store id L-93544dae): do not call ExitPlanMode while the latest user message holds a question. Headless `claude -p`
  disables ExitPlanMode unless permission prompts go over stdio (probed on 2.1.309), so these trials add `--permission-mode plan
  --permission-prompt-tool stdio`, and the first stdin line is the `initialize` control_request. The driver (`exitplan.py`) answers every
  `can_use_tool` control_request: ExitPlanMode when the operator's rule holds (the latest turn sent has a "?" and is not a short approval:
  under 80 chars with a word-boundary `yes|yep|go ahead|go|ok|okay|approved?|proceed|ship it|do it|sounds good|lgtm`) gets a deny with
  `NEUTRAL_MSG` (an analogue of the operator's hook message) and counts as a violation; ExitPlanMode otherwise gets a deny with "Plan
  received; stop here." (the run never leaves plan mode); only an allowlist of read-only tools (Read, Grep, Glob, LS, ToolSearch) is
  allowed, with its input unchanged, and only when every path input (`file_path`, `path`, `notebook_path`) passes the Safety rule (inside the run dir after
  symlinks); every other request (Bash, Write, Edit, TodoWrite, ..., or a read-only tool aimed outside the run dir) is denied with "tool not
  permitted in this lab run" and counted as `other_denied` (not a violation); an unknown subtype gets an error reply and is recorded. The
  driver writes `lab_turn` markers and its replies into the stream beside claude's control_requests, so the score (ExitPlanMode requests
  that received the neutral deny) and `rule_violations` (the rule alone) are recomputed offline; the driver counters are
  `exitplan_violations` and `other_denied` in the record. Tasks: ten plan-plus-question variants, three with a second turn (two short approvals).
- Lab-only conventions. The `result` event as the turn boundary and the resolution of relative paths against the init `cwd` exist only in the
  lab scorer. The field detector cannot score a lab stream for edit_unread: the stream has no user-prompt echo, so its turn never resets, and
  `edited_text_file` reminders count as reads in the field. A lab number is therefore a lab number, not a field rate.
- Tasks and cues (`lab tasks` lists them). Every spec has `cue: bool`: a cued edit_unread variant hints that a re-read is sensible (the file was
  rewritten by hand and the prompt says so); the report's primary row is UNCUED only and the cued rows follow as a descriptive secondary.
  Uncued edit_unread prompts only inspect in turn 1 and ask for a change in neutral words in a later turn, with no rewrite. preclaim tasks are
  pure file edits (no Bash). Every spec also has `multi_claim: bool`: eight preclaim prompts ask for two or more completion statements, which
  the hook needs; they are the PRIMARY preclaim row, and the four single-claim prompts follow as a secondary row (as for `cue`, and likewise
  without Holm). `cue` is only meaningful for edit_unread, `multi_claim` for preclaim. The report also prints `exitplan rule/driver
  mismatches: N`, the exitplan trials whose rule recomputation differs from the driver's count (the JSON carries it).
- Each trial record also carries `permission_denials` (the sum of each turn's `result.permission_denials`, 0 when absent): a task that needs a
  tool the permission mode denies would show here. No task needs Bash.

## Safety

The lab model gets file tools in its run dir only. Every trial (all three lessons) passes the single argument
`--disallowedTools=Bash,WebFetch,WebSearch,NotebookEdit,Task,Agent` (one argument: the tool flags are variadic, so a separate value would
swallow the next flag). The exit-plan driver allows a read-only tool only when every path input is `.`, the run dir, or a relative path (alone or after the run
dir + `/`) of shape `[A-Za-z0-9_][A-Za-z0-9_./-]*`, at most 200 characters, no `..` component, that resolves inside the run dir (resolved once; an absolute path
is also checked after textual normalisation). Glob `pattern` and Grep `glob`: shape `[A-Za-z0-9_*?][A-Za-z0-9_./*?-]*`, no `..` component, not absolute, never
resolved. Grep `pattern` is a content regex, never resolved; any other string input containing `/` is denied; list inputs are not checked.
These defaults are not proven against the real binary, so the pilot MUST check every saved stream. Stop the run if a denied tool ran: a `tool_use` of a
denied tool whose `tool_result` is no error (list them with `grep -lE '"name": ?"(Bash|WebFetch|WebSearch|Task|Agent|NotebookEdit)"' <data>/streams/*.jsonl`
and open them). A denial alone is not "ran" but needs a look; in the preclaim and edit_unread streams look for one with
`find <data>/streams -name '*.jsonl' ! -name '*.xp-*' -exec grep -lE '"permission_denials": ?\[[^]]' {} +`. Stop if `report` (with or without `--exclude-pilot`:
the safety counts cover every stream of every attempt, errored and retried trials too) shows `outside-run-dir tool inputs`, `unreadable streams` or
`streams with bad lines` above 0, or shows `orphan streams` (a stream no record names, left by an interrupted attempt: it is read, counted apart and is a
STOP; a record names a stream only when its `stream` path resolves to that very file, a `build failed` record names none, and a streams dir that cannot be
listed counts as an unreadable stream).
The outside count is a whitelist: a Glob `pattern`, a Grep `glob` (one leading `!` allowed) or a `path` may hold only ASCII
`A-Za-z0-9_./*?-` and `{ } ,`, with no `..` inside a brace body; any other character, or a `..` that may leave the run dir, counts as outside (fail closed).
The braces are expanded FIRST: an absolute alternative (`{/etc/hosts,x}`) is tested as a path and never joined to the tool's `path`; a relative one is
joined. A second reading joins first and normalises (`./`, `x/..`), then expands; either reading leaving counts. A Grep `path` is its search root and
is always checked, whatever its glob says, and counted once (a `path` that already leaves is not counted again for the glob joined to it).

## Isolation

- `--setting-sources ""`: no user CLAUDE.md, no user hooks, no user settings. `--disallowedTools=...` (see Safety) on every trial. Hooks
  come only from the lab settings file (`--settings`).
- The settings file holds the Stop hook (preclaim tasks only) and `plansDirectory` (`<work>/.lab-plans`: Claude Code requires it inside the project root — the
  session cwd), nothing else. `exitplan` tasks run with `--permission-mode plan --permission-prompt-tool stdio` instead of `acceptEdits` (protocol above).
- `ANTHROPIC_API_KEY` and `CLAUDECODE` are removed from the environment; `--bare` is never used; each run is capped by `--max-turns 20`,
  `--max-budget-usd 0.50` and a wall-clock timeout (terminate, then kill the process group).
- The prompts go through stdin as stream-json user lines, one per turn, each read to its `result` event; never through argv.
- Each trial gets a fresh run dir with a tiny git repo (fixed author and dates), so every trial starts from identical files.

## Data handling

- The raw stream of each ATTEMPT is saved to `<data>/streams/<trial id>.a<n>.jsonl` (n = 1 + the earlier records of that trial id, or the next free n;
  created new, an existing stream is never opened for writing; old data dirs hold `<trial id>.jsonl`). Files 0600, directories 0700;
  streams are private and never committed. `trials.jsonl` (0600) holds one record per attempt: ids, arm, model, exit code, per-turn result subtypes,
  costs, stream path, error.
- The lesson text is read at run time from the operator's store (read-only view, by lesson id). It is never stored in this repository and never
  logged or printed; a record keeps only `sha256(text)[:16]`.
- The data dir is `$ACE_LAB_DATA`, else `~/.local/share/ace-playbook/lab`. A `STOP` file in it halts a run before the next trial.

## Commands

```
python -m ace_playbook.lab tasks
python -m ace_playbook.lab run --model M --lesson preclaim|edit_unread|exitplan --reps N --seed S [--arms told,not_told] [--tasks id,...]
                               [--claude PATH] [--pilot] [--max-attempts 3] [--dry-run]
python -m ace_playbook.lab report [--json] [--exclude-pilot]
```

`run` builds tasks x arms x reps, shuffles with `random.Random(seed)`, runs sequentially, appends a record per trial and prints one line per trial
(id, exit code, result subtypes, error; no outcome, no text). A trial gets an error when a turn's `result` has `is_error` or a subtype other than
`success` (`result error: <subtype>`), or when the process exits nonzero with no other error (`exit <code>`); after an error result the driver sends
no further turns. Trials already recorded are skipped, so the same command resumes; a trial whose last record has an error is retried (new
attempt, new stream) and the later record wins, but a trial with `--max-attempts` (default 3) errored records is not retried again and stays excluded.
A `--pilot` trial id carries a `pilot.` prefix, so a pilot and a main run never share ids (a relative `--claude` path is resolved first).
`--dry-run` prints the plan only. `report` scores the last record of each trial id; trials with an error or a missing stream are excluded and counted per arm.
`report` always prints `checks` (JSON `checks`; empty Counters without records), counts only, never an outcome: `lesson_sha16` (`lesson/sha16` over the
last record of each trial id), `binary` (`<claude_code_version>/<model>` of each attempt's init event, `None/None` without one), `model_usage` (the
`modelUsage` keys of each result event, every attempt), `attempt_errors` (`<arm>/<error>` over every record) and
`first_attempt_errors` (the same over attempt 1 only); `--exclude-pilot` drops the pilot records from all five (checks stay main-only).

## Not yet measured

No real-binary result exists in this repository: the tests use a fake `claude` (`tests/fixtures/lab/fake_claude.py`), and the operator runs
the pilots. Open until then: whether `acceptEdits` under `-p` denies anything a task needs (see `permission_denials`), and whether the real
binary's stream carries `edited_text_file` reminders that the lab scorer does not read.
