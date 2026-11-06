# Finding: the lab, pre-registration version 2 (run 2026-11-05 21:25Z to 2026-11-06 05:42Z)

**Result: on both models, reminding the agent to read a file before editing it removed unread edits; reminding it to write the
self-audit block before claiming "done" did not reduce the hook's blocks (on Claude Sonnet 5.5 it increased them; on Claude Opus 5.5
no difference was detected, +0.02, 95 % [−0.09, 0.12]).** Every result below is the output of the sealed analysis, run once, on the
data of the one registered run.

## What ran
The lab ([docs/lab.md](../lab.md)) runs `claude -p` (Claude Code 2.1.317) on tiny synthetic tasks in two arms, TOLD (the lesson text is
injected at the start) and NOT_TOLD, and a deterministic check counts the mistake. The pre-registration
([docs/lab_prereg.md](../lab_prereg.md), version 2) was sealed at commit ebcad10 (sha256 `acaf6e0c1372600f…`, OpenTimestamps
2026-11-05T21:24:49Z, earliest Bitcoin attestation block 974714) before any version-2 trial; the lab code is pinned to commit bcb8f9e (35-file hash in the
text). Per model and lesson: 8 tasks × 13 reps × 2 arms = 208 trials (104 per arm), shuffled by a seed drawn and logged before the seal,
run in one fresh data dir per model, Sonnet first, then Opus, each command followed by a registered safety check (binary hash, tool set,
no file touched outside the run dir, no stream opened by hand). Two-sided Fisher exact test, Holm over the two lessons of a model;
a decision is `reduce`, `increase` or `none`, and a decision that differs when excluded or retried trials are added back is labelled
fragile. A third lesson (leaving plan mode with a question pending) ran descriptively at 20 per arm with no test registered.

Version 1 of the same registration (sha256 `877efbd00661f46f…`, earliest attestation block 974627) ended on its own safety rule after its first command: two
of 208 Sonnet trials typed their own working directory wrong and the binary refused the path, which version 1 counted as run-ending.
Version 2 splits refused from executed outside-run-dir inputs (a refused one is reported, an executed one still ends the run), draws
fresh seeds, uses new data dirs and reads the denied-tool pairing the same stricter way; the question, instrument, code, tasks,
design, analysis, power and null handling are unchanged. Version 1's 208 trials are reported apart below and never pooled.

## Confirmatory results (104 trials per arm)
| model | lesson (outcome per trial) | TOLD | NOT_TOLD | difference, Newcombe 95 % | Fisher p (Holm) | decision | fragile |
|---|---|---|---|---|---|---|---|
| Claude Sonnet 5.5 | read before edit (an edit of a file not read in the turn) | 0/104 | 99/104 | −0.95 [−0.98, −0.88] | 2.1e-53 | reduce | no |
| Claude Sonnet 5.5 | self-audit before "done" (a block by the Stop hook) | 64/104 | 32/104 | +0.31 [0.17, 0.43] | 1.4e-05 | increase | no |
| Claude Opus 5.5 | read before edit | 0/104 | 104/104 | −1.00 [−1.00, −0.95] | 1.8e-61 | reduce | no |
| Claude Opus 5.5 | self-audit before "done" | 86/104 | 84/104 | +0.02 [−0.09, 0.12] | 0.86 | none | no |

Wilson 95 % intervals: Sonnet read-before-edit TOLD 0–0.036, NOT_TOLD 0.892–0.979; Sonnet self-audit TOLD 0.519–0.703, NOT_TOLD
0.227–0.402; Opus read-before-edit TOLD 0–0.036, NOT_TOLD 0.964–1.0; Opus self-audit TOLD 0.743–0.888, NOT_TOLD 0.722–0.872. No trial
was excluded (ceiling 10 per arm); 2 of 418 Sonnet attempts errored and were retried as registered, 0 of 416 Opus attempts.
The Opus self-audit null is reported with its interval, as the registered Power section says, not as "no effect".

Descriptive, registered: the third lesson (plan mode left with a question pending), 20 per arm: Sonnet TOLD 17/20 vs NOT_TOLD 20/20,
Opus 18/20 vs 20/20. Cost per trial as the binary reports it: Sonnet $0.023 (NOT_TOLD) to $0.027 (TOLD), Opus $0.049 to $0.058;
mean turns 4.8 to 7.0. Version 1's 208 Sonnet self-audit trials, apart: TOLD 55/104 vs NOT_TOLD 29/104, the same direction as
version 2's Sonnet row.

## Safety and deviations
Seven post-command safety checks, all passed; no STOP. Two deviations, both the run script's: the safety block did not run before the
first trial (an emptiness check of each new data dir stood in; on an empty dir the block passes by its own statement), and the binary
hash was checked before each of the seven command runs (re-run included) but not after the last one. Sonnet refused-outside-run-dir
inputs: 15 in 11 trials, each refused by the binary with a registered refusal text (not inspected; nothing ran outside a run dir,
`outside_ran` 0); Opus 0 in its confirmatory dir, 1 in its exitplan dir. The Sonnet read-before-edit command stalled four times for
about 4.7 hours in all (two attempts ended after one turn with an error result and were retried); the cause was not diagnosed, since
the registered procedure opens no stream; the lab's 600 s per-trial timeout did not fire, so the clock it reads did not advance.

## What it shows
1. The read-before-edit reminder works on these tasks for both models, and the effect is large: without it the agent edited an
   unread file in 95 to 100 % of trials, with it in none (close to the largest the design can show).
2. The self-audit reminder does not reduce the Stop hook's blocks. On Sonnet the hook blocked twice as often with the reminder; on Opus
   it blocked about 80 % of trials either way, five of the eight tasks at 13 of 13 in both arms, so the outcome sat at a ceiling the
   planning value (near 0.4) did not anticipate. Why the Sonnet rate rose is not measured here.
3. The tasks are small and built to elicit each mistake, and the outcome is the lab's own hook, so these are effects on scripted
   tasks, not in real sessions. The field experiment ([PREREGISTRATION.md](../../PREREGISTRATION.md)) is the test of that.
