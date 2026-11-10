# Finding: the lab, pre-registration version 2 (run 2026-11-05 21:25Z to 2026-11-06 05:42Z)

**Result: on both models, reminding the agent to read a file before editing it removed unread edits; reminding it to write the
self-audit block before claiming "done" did not reduce the hook's blocks (on Claude Sonnet 5.5 it increased them; on Claude Opus 5.5
no difference was detected, +0.02, 95 % [−0.09, 0.12]).** Every confirmatory result below is the output of the sealed analysis
(re-runs print identical bytes) on the data of version 2's run; version 1's trials, reported apart, are from its ended run.

## What ran
The lab ([docs/lab.md](../lab.md)) runs `claude -p` (Claude Code 2.1.317) on tiny synthetic tasks in two arms, TOLD (the lesson text
is injected at the start) and NOT_TOLD, and a deterministic check counts the mistake. The pre-registration
([docs/lab_prereg.md](../lab_prereg.md), version 2) was sealed at commit ebcad10 (sha256 `acaf6e0c1372600f…`, OpenTimestamps
2026-11-05T21:24:49Z, earliest Bitcoin attestation block 974714) before any version-2 trial; the lab code is pinned to commit
bcb8f9e (35-file hash in the text). Per model and lesson: 8 tasks × 13 reps × 2 arms = 208 trials (104 per arm), shuffled by a seed
drawn and logged before the seal, run in one fresh data dir per model, Sonnet first, then Opus, each command followed by a safety
check as run (binary hash before each command, then the sealed block: tool set, no unrefused tool input outside the run dir; counts
only, streams never opened by hand). Two-sided Fisher exact test, Holm over the two lessons of a model; a decision is `reduce`,
`increase` or `none`, and a decision that differs when excluded trials are added back against the observed direction, or when every
trial is scored on its first attempt, is labelled fragile. A third lesson (leaving plan mode with a question pending) ran
descriptively at 20 per arm with no test registered.

Version 1 of the same registration (sha256 `877efbd00661f46f…`, earliest attestation block 974627) ended on its own safety rule
after its first command: two of 208 Sonnet trials typed their own working directory wrong and the binary refused the path, which
version 1 counted as run-ending.
Version 2 splits refused from executed outside-run-dir inputs (a refused one is reported, any other one still ends the run), draws
fresh seeds, uses new data dirs and reads the denied-tool pairing the same stricter way; the question, instrument, code, tasks,
design, analysis, power and null handling are unchanged. Version 1's 208 trials are reported apart below and never pooled.

## Confirmatory results (104 trials per arm)
| model | lesson (outcome per trial) | TOLD | NOT_TOLD | difference, Newcombe 95 % | Fisher p (Holm) | decision | fragile |
|---|---|---|---|---|---|---|---|
| Claude Sonnet 5.5 | read before edit (a successful unread edit) | 0/104 | 99/104 | −0.95 [−0.98, −0.88] | 2.1e-53 | reduce | no |
| Claude Sonnet 5.5 | self-audit before "done" (a block by the Stop hook) | 64/104 | 32/104 | +0.31 [0.17, 0.43] | 1.4e-05 | increase | no |
| Claude Opus 5.5 | read before edit | 0/104 | 104/104 | −1.00 [−1.00, −0.95] | 1.8e-61 | reduce | no |
| Claude Opus 5.5 | self-audit before "done" | 86/104 | 84/104 | +0.02 [−0.09, 0.12] | 0.86 | none | no |

An unread edit is a successful edit of a file not read in the same turn.

Wilson 95 % intervals: Sonnet read-before-edit TOLD 0–0.036, NOT_TOLD 0.892–0.979; Sonnet self-audit TOLD 0.519–0.703, NOT_TOLD
0.227–0.402; Opus read-before-edit TOLD 0–0.036, NOT_TOLD 0.964–1.0; Opus self-audit TOLD 0.743–0.888, NOT_TOLD 0.722–0.872. No trial
was excluded (ceiling 10 per arm); 2 of 418 Sonnet attempts errored and were retried as registered, 0 of 416 Opus attempts.
The Opus self-audit null is reported with its interval, as the registered Power section says, not as "no effect".

Descriptive, registered: the third lesson (plan mode left with a question pending), 20 per arm, as rates with Wilson 95 % intervals:
Sonnet TOLD 0.85 (0.640–0.948) vs NOT_TOLD 1.0 (0.839–1.0), Opus 0.90 (0.699–0.972) vs 1.0 (0.839–1.0). Registered secondaries: mean
violations per trial edit_unread TOLD 0.00 vs NOT_TOLD 1.19 (Sonnet) and 0.00 vs 1.25 (Opus), preclaim 0.62 vs 0.31 (Sonnet) and
0.83 vs 0.81 (Opus); attempted unread edits equal the counted ones (every attempt succeeded); permission denials in 5 Sonnet
NOT_TOLD trials, none elsewhere (attempted edits and permission denials are from a script written after the run, not a sealed
block). Mean cost per trial as the binary reports it: Sonnet $0.023 (NOT_TOLD) to $0.027 (TOLD), Opus $0.049 to $0.058; mean turns
4.8 to 7.0. Version 1's 208 Sonnet self-audit trials, apart: TOLD 55/104 vs NOT_TOLD 29/104, the same direction as version 2's
Sonnet row. The other registered outputs (the bounds, first and retried rows, errors per arm, per-task tables, quarter shares, turns
per arm, cost maxima, the environment line, the pause times, the installed label) are in the operator's result record, kept with the
run files and not in this repository.

## Safety and deviations
Seven post-command safety checks, all passed; no STOP. Three deviations: two the run script's: the safety block did not run before
the first trial (an emptiness check of each new data dir stood in; the sealed text states that on an empty dir the block prints
every count 0 and PASS), and the binary hash was checked before each of the seven command runs (re-run included) but not after the
last one, the block running only in the command's own dir, not in each of the four dirs after every command (a read-only run of the
block in all four dirs on 2026-11-06 printed PASS with the same counts); and one the operator's: a read of the Sonnet data file's
line counts (and, at 21:34Z, that the other three data dirs had no file yet) and, just after the stalls (03:54Z), the start times of
the stalled and following attempts and the stalled ones' exit codes and turn counts while that model's commands were still running
(no outcome, trial id or arm read), where the sealed text allows only the safety checks. Not a deviation, on the records' reading of
the sealed text's error burst: the re-run of the 2 errored attempts came at once (2 errors, below the more-than-5 count that raises
`errored_attempts`), so no 5-hour wait was due. Sonnet refused-outside-run-dir inputs: 15 in 11 trials, each refused by the binary
with a registered refusal text (not inspected; no unrefused outside input, `outside_ran` 0); Opus 0 in its confirmatory dir, 1 in
its exitplan dir. The Sonnet read-before-edit command stalled four times for about 4.7 hours in all (two attempts ended after one
turn with an error result and were retried); the cause was not diagnosed, since the registered procedure opens no stream; the lab's
600 s per-trial timeout did not fire, so, if the stall fell inside the lab's wait for a result, the clock it reads advanced less
than 600 s.

## What it shows
1. The read-before-edit reminder works on these tasks for both models, and the effect is large: without it the agent edited an
   unread file in 95 to 100 % of trials, with it in none (close to the largest the design can show).
2. The self-audit reminder did not reduce the Stop hook's blocks on these tasks. On Sonnet the hook blocked twice as often with the
   reminder; on Opus no difference was detected (+0.02, 95 % [−0.09, 0.12]): it blocked about 80 % of trials in both arms, five of
   the eight tasks at 13 of 13, far above the 0.4 rate the Power section's caveat names; at that rate the registered power for a
   relative rate of 0.5 or 0.67 is 1.00 or 0.95, and the interval excludes a reduction larger than 0.09. Why the Sonnet rate rose
   is not measured here.
3. Trials are clustered in 8 fixed tasks and the test treats them as independent, so the p values do not carry to other tasks.
   The trial list is completely randomised, not blocked, so the arms need not balance over time: the Opus seed put 43 of 104 TOLD
   trials in the first half (35 of 52 in the last quarter); the sealed text's "60 of 104" is version 1's retired seed, an error in
   the sealed text.
   The tasks are small and built to elicit each mistake, and the outcomes are the lab's own counts (a block by its Stop hook; a
   successful edit of a file not read in the same turn, counted from the stream), so these are effects on scripted tasks, not in
   real sessions. The second field experiment, whose registration is written and reviewed but not yet sealed, is meant to test that.
