# Finding: the rm_rf shadow detector (2026-10-20 to 2026-11-02)

**Result: no evidence either way.** The detector fired 44 times in 13 days. All 44 were false positives, and the operator labelled every
one. The pre-registered gates for live arms cannot be met on this detector, so the arms never started.

## What ran
A pre-registered shadow-mode experiment (`PREREGISTRATION.md`, sealed 2026-10-29T05:44:23Z, tag `prereg-v1`, OpenTimestamps-anchored)
around one detector, `rm_rf_nonscratch`. It encodes one lesson: never `rm -rf` a directory without listing it first. That lesson came
from a real 7.8 GB loss. Over the window there were four proposals: v1, a retired variant, and a v2 grammar rewrite approved 2026-10-30.
The hook runs on PreToolUse and stores payloads as hashes only. Labels are human-only. Hook cost was p50 3 ms and p95 13 ms against a
200 ms budget.

## Numbers
| | value |
|---|---|
| eligible sessions | 44 |
| fires, all proposals | 44 |
| human labels: true positive / false positive | 0 / 44 |
| sessions with at least one fire | 12 |
| v1 fire-session rate (10-20 to 10-29), 95 % Wilson | 9 of 44 = 0.20, [0.11, 0.35] |
| v2 fire-session rate, 95 % Wilson | 0 of 44, [0.0, 0.080] |

Every fire was the same event: a sandboxed subagent deleting its own scratch tree, written as a plain path, a variable, a variable with a
suffix, or a `find -exec`. The v2 grammar silenced the literal scratch forms, and its three later fires were cleanups in documented
residual forms. No fire was ever on a directory a person cared about, and the original incident did not recur.

## Against the gates
- **Arms:** require at least 30 human-labelled sessions with a fire, 0 false positives and 0 unlabelled. Actual: 12 sessions with a
  fire and 44 false positives. NO-GO.
- **Promotion to warn:** requires 14 days and 5 human true positives. Actual: 0. No promotion.
- **Horizon:** 12 weeks or 400 sessions. With 0 true positives, that ends in a null by construction.

## What it shows
1. The machinery works end to end. A detector can be proposed, approved, sealed, run in shadow at negligible cost, labelled by a person,
   and reported against pre-registered gates, and no payload leaves the machine.
2. The chosen detector had nothing to measure. In this environment, the only violations were subagents cleaning up inside their own
   sandboxes.
3. The experiment therefore says nothing about whether the loop changes behaviour. That limit comes from the choice of detector, not from
   the loop.

## What came next
The next experiments target lessons that recur in the main session's own behaviour. Over the same 14 days the detectors counted 11
pre-claim-audit blocks, 10 edits of a file not read in the same turn, and 6 ExitPlanMode calls with a question pending. That work is a
field holdout of those three lessons, plus a controlled lab with its own pre-registration. The rm_rf detector stays in observe mode and
keeps recording.
