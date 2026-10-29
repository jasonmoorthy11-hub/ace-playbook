# Pre-registration (DRAFT, NOT SEALED)

Status: **draft placeholder.** Nothing in this file is binding yet. It becomes binding when it is
committed with the fields marked TBD filled in and tagged `prereg-v1`, before the first
randomized session that will count as confirmatory. After the tag, changes are dated amendments
appended at the bottom; the sealed text is never edited. Any run made before the tag is
labelled exploratory and is excluded from the confirmatory analysis.
`prereg-v1` seals only "Shadow-mode additions"; the injection RER experiment (the sections from
"Question" to "Compiled path") stays a draft, and no session counts toward it. `holdout_enabled`
stays false during any shadow arm phase.

## Question

Does showing a specific, well-evidenced lesson at session start reduce the chance that the same
mistake recurs in that session, compared with hiding that lesson in otherwise identical sessions?
A second, separate question (not testable in the live design) is whether compiling a lesson into
an enforcing hook beats injecting it as prose; see "Compiled path" below.

## Primary metric (planned)

**Repeat-error rate (RER)**: for each eligible (session, lesson) pair, whether the specific
mistake the lesson targets recurs in that session. Reported as a rate ratio, shown versus hidden.

- Unit of analysis: the (session, lesson) pair, with errors clustered by session.
- Outcome detection: a mechanical detector wherever one exists (command pattern, a hook block
  event, an exact repeated failing command); a blinded judge otherwise. Judge-scored lessons are
  dropped from the confirmatory analysis unless the judge gate below is met. TBD: the detector
  list per lesson, frozen at seal time.
- Assignment: micro-randomized. Each eligible lesson in each session is independently shown or
  hidden by a seeded Bernoulli draw with p = 0.5, computed from (seed, session id, lesson id)
  and written to `injections.jsonl` at injection time. Holdout is off by default.
  Seed commitment: generate a private seed (`python3 -c "import secrets; print(secrets.token_hex(16))"`),
  keep it only in the private config file as `holdout_seed`, set `holdout_enabled` to true, and
  commit only `sha256(seed)` here and as `holdout_seed_sha256` in the config; the injector refuses
  a seed that does not match the committed hash. TBD: the committed hash.
- Analysis: randomization inference with a clustered Poisson regression as a cross-check.
  TBD: analysis code and its SHA-256, committed before the first confirmatory session.
- Pre-registered meaningful effect: rate ratio 0.67 (a one-third reduction).

## Eligibility and exclusions (planned)

- A lesson is eligible only if its content does not also appear in CLAUDE.md, rules, memory
  files or repo docs, because hiding a duplicate cannot create a contrast.
- Compare injected versus hidden versions of the same lesson, never differently authored text.
- The reflector's own tags (helpful, harmful, violated) are not outcomes. Perceived benefit is
  never an outcome.
- Injected blocks, `[L-id]` citations and hook messages are stripped from anything a judge sees.
- Friction is measured in every arm: turns, tokens, correction turns per 100 user turns, hook
  false-blocks.

## Timing and stopping (planned)

Group-sequential looks at 8, 16 and 24 weeks, with early stopping for harm only. The 24-week look
is the final look and the only confirmatory read. The two-sided boundaries are z 3.471, 2.454 and
2.004 (p 0.0005, 0.0141 and 0.0451, O'Brien-Fleming style), verified by the 2026-10-29
statistician pass.

## Gate before any live experiment (Step 0)

Replay the existing transcripts and hook-block events to estimate each lesson's baseline
recurrence. If fewer than about six lessons recur at 2% or more per eligible session, the live
experiment cannot reach useful power and is not run; the project then reports descriptive
numbers only.

## Judge validation gate (planned)

60 judge-positive and 60 judge-negative items labelled blind by the author; Clopper-Pearson
lower bound on precision of at least 0.80; test-retest on 30 items two weeks later; one rubric
rewrite allowed before judge-scored lessons are dropped. Prefer mechanical detectors; the judge
must not be weaker than the model whose sessions it scores.

## Null handling (planned, written in advance)

If the confidence interval for the rate ratio includes 1.0 and the point estimate is above 0.8,
stop investing in injection quality and report the null. The design is underpowered for the
pre-registered effect at any horizon under roughly 30 weeks at about 2.6 sessions a day, and an
informative null (excluding both a rate ratio below 0.67 and above 1.5) is not reachable at
16 weeks. These figures rest on assumed baselines (for example 3% per pair) and must be
recomputed after the lesson store is migrated and eligibility is known. See LIMITATIONS.md.

## Compiled path (planned, separate)

For a rule that a hook enforces deterministically, blocking is true by construction, so the live
primary for that path is false-block rate and friction, with recurrence reported descriptively.
The comparison "compiled beats injected" needs a controlled synthetic task suite with arms for no
memory, a hand-written CLAUDE.md line, a plain recent-history notepad, an ACE-style playbook,
injected lessons and a compiled hook. That suite is not built in stage 1.

## Shadow-mode additions (DRAFT until sealed)

These commitments cover the compiled path in shadow mode (docs/design/compiler.md, section 8). They
are binding only once `compile seal` has frozen the block below and the repository is tagged
`prereg-v1`; until then the heading says DRAFT and nothing here is a commitment.

### Effect estimate (compiled path, descriptive)

The compiled-path primary stays the false-block rate and friction, with recurrence reported
descriptively, as the "Compiled path" section above says; nothing here replaces it. The shadow arm
phase estimates one effect per detector: the "with" arm (the warn arm) runs the proposal in its
approved mode, the "without" arm (the observe arm) runs it in observe. A hook has no memory across
sessions, so the first attempt in a session cannot differ between arms.

One primary per detector: repeat fires per eligible session. A session's value is its fires beyond
the first (`max(0, fires - 1)`), counting only rows with `agent_id_present` false; subagent fires
(`agent_id_present` true) are secondary, and so are rows that do not record the field. Test
statistic: D = (mean of the primary, warn arm) - (mean of the primary, observe arm). The rate ratio
is reported but not used for the test (it is undefined when the observe-arm mean is 0).

Test: a permutation test with 10,000 Bernoulli(`arm_rate`) re-draws of the arm labels over the
eligible sessions, drawn from `random.Random(permutation_seed)` under a committed permutation seed
(only its sha256 is sealed, as `permutation_seed_sha256`). Two-sided p = (1 + #{|D*| >= |D|}) /
10,001, where D* is D under a re-draw. The analysis is by assigned arm (intention to treat). The
interval for D is the permutation interval [D - q97.5(D*), D - q2.5(D*)] from the same re-draws,
which assumes a constant additive effect. Holm across detectors, family alpha 0.05 two-sided. The
Holm family holds one hypothesis per proposal approved at the arm start (its newest approvals row at or
before the start, read from the chain prefix the `arm_start` line names, not `revoked`); a detector with
two such proposals contributes two, which is conservative; rows written later change nothing (intention
to treat).

Secondary, with no p-values: evasion, sessions-with-fire and friction. Sessions-with-fire gets a
Wilson 95 % interval per arm (`metrics.wilson`); a Wilson interval is never put on fires per
session, which can exceed 1. Friction is reported descriptively: messages per 100 calls is 0 in
observe by construction (a manipulation check, not an outcome), and corrections per 100 prompts is
not specific to any detector.

### Eligibility

A session is eligible for a detector if it is an interactive session (the definition of
`step0/measure.py:79`: a human prompt or a plan start; in the `_session` row, `prompts` above 0; the plan-start clause is not recorded in the summary row) and
has at least one event that detector consumes: the detector is listed in the `eligible` field of
the newest `_session` row of that session in `detector_events.jsonl` (the last row in file order
wins). Its first transcript event (the row's `start`; a row without one is reported as missing)
must fall in [arm start, horizon end) for that detector.
Automated sessions are not eligible. Revoked and kill-file gaps are kept in their assigned
arm and reported; a lowering demotion ends the horizon (see Arm assignment). The scan reads `*.jsonl` transcripts and `offline.discover` skips subagent
transcripts, non-regular files (symlinks included), names that do not match the session-id pattern,
transcripts older than the `since_days` look-back, already-scanned transcripts that have not changed,
oversized transcripts (over 64 MiB) and sessions idle for less than the configured idle time.
Excluded: the synthetic corpus, fires with `cause: truncated`, and sessions still awaiting the
offline scan at analysis time. The analysis runs 7 days after the horizon ends, after one forced
offline scan; sessions still unscanned are counted and reported as missing, never silently dropped.

### Arm assignment

Each (session, proposal) pair is assigned by `arms.assign`: the first 4 bytes of HMAC-SHA256 under
`shadow_seed` of `session hash|proposal id`, read as a fraction of 2**32, below `arm_rate` means the
warn arm; `arm_rate` is 0.5. The arm start of a detector is a line `arm_start <detector> <UTC
stamp> approvals=<64 hex>` under the top-level Amendments heading, added before its first randomized session (the sealed
block cannot hold it; the field is the `hash` of the last row of the verified `approvals.jsonl` chain at that moment);
the report reads `arm_start` lines nowhere else, refuses a stamp earlier than the block's `sealed_at` and a line whose
head is missing, malformed or not the hash of a chain row, says "not started" without one, and shows no effect before the
analysis day.
The seeds are private and only their sha256 is sealed, the same
commitment as `holdout_seed` above. The shadow seed's value lives only in the private config
file, outside anything the agent reads by default (not in the repository, the transcripts, a
CLAUDE.md or a rules file). Limit: a Bash tool can read any file the operator can, so this keeps the
arm out of default view and does not make it unreadable; the seal records only the sha256.

Labelling is symmetric in the randomized phase: a user correction within 2 prompts of any fire, in either
arm, is a neutral flag, not an automatic false positive, and the label queue hides the mode and the arm
of every row. Any lowering demotions.jsonl row of the detector's proposals stamped at or after the arm start and before
the natural end of the horizon (12 weeks, or the 400th eligible session) ends the horizon, whatever its cause.
An `evasion_pair` flag lowers nothing and does not end the horizon, and a lowering row stamped after the natural end ends nothing.
The report prints the cause. Only a `label_fp` or `label_fp_pair` row driven by human labels is the harm
event. Harm = 1 human-labelled FP block, or 2 FP warns within 30 days. A human-labelled FP of an observe-arm fire counts as an FP
at the proposal's approved mode (offline.py applies no arm filter). Harm is counted symmetrically across arms. The analysis is intention to treat
by assigned arm, so sessions run in a revoked or kill-file gap stay in their assigned arm, and sessions
that start after a lowering demotion are not eligible. The true-positive and false-positive rules of design
section 7.3 and the promotion and demotion ladder are frozen by reference to the section numbers of the
design document and to the report code hash (`report_sha256`) in the block below. holdout_enabled stays
false during any shadow arm phase.

Pre-arm-start conditions. Before any `arm_start` line exists: (1) the hook applies
`arms.assign(session hash, shadow_seed, arm_rate, proposal id)` and writes `arm` and
`agent_id_present` on every shadow row, and `arms report` refuses (exit 1) when a fire row of an
eligible session lacks either field or records an `arm` that `arms.assign` does not give; (2) the
`arm_start` amendment records the sha256 of `shadow.py`, `offline.py`, `events.py`, `demotions.py`, `modes.py` and
`approvals.py` at that commit, carries `approvals=<head hash>` (the Holm family is read from the approvals chain prefix
ending at that row), and is anchored like the seal: a signed tag `arm-<detector>` on that
commit plus an OpenTimestamps proof of the amendment line's sha256 (a commit time alone proves
nothing, the author sets it); (3) `offline.classify` is made symmetric (the correction rule
above); (4) harm and demotion during a phase count human labels only, never auto labels; (5) the
spillover comparison is built after the seal, is exploratory and carries no p-values; the freeze on lesson minting
and on status changes of family lessons from `ACE P-` sessions is a binding pre-arm-start build item (condition (7)); (6) at the
arm start every proposal of the family is approved at `warn` or `block` and no lowering demotion row stamped before the arm start
is in force (`modes.in_force` over those rows at the arm start is empty); `arms report` refuses otherwise; (7) the delivered path
(the warn/block output writes of the hook) logs its failures as `error:delivered:<Type>` lines of `shadow.log`.
The hook writes each line through `shadow._log`: `<YYYY-MM-DDTHH:MM:SSZ> error:delivered:<Type>` (a 20-character UTC stamp, a space, the tag).
The analysis already counts those lines stamped in [arm start, horizon end], the end included
(the eligibility window itself stays half-open).
It prints the count whenever an `error_rate` row ends a horizon (`unknown`, never 0, when the log is missing, unreadable or full).
That code is built and hashed before the seal.
During a phase no status change (retire, supersede, deactivate) of a family lesson is written, and no lesson is minted,
from a session whose transcript carries `ACE P-`.

### Spillover

Lessons are not arm-specific, so the "without" arm is contaminated by the prompt-level lesson that
the same incident produced, and the operator also sees the stderr echo. The estimand is "the
within-session effect of the warning given cross-session contamination"; it is biased toward 1.
Lesson minting and status changes of family lessons from sessions carrying `ACE P-` are frozen during the phase (condition (7)).
The report flags every lesson whose originating session holds an `ACE P-` message and reports the arm comparison
separately for sessions where such a lesson was shown.
The flag is reported, not corrected.

### Timing and stopping

Entry gate (per detector, observe mode): at least 30 human-labelled sessions-with-fire (the first
fire per session counts), 0 FP, 0 unlabelled. Fires within a session share one label, so the gate
counts sessions, not fires. At the Step 0 rates (7.2 % of sessions with a fire, 2.4 interactive
sessions a day; week 1 had 7 of 34 sessions with a fire, 20.6 %) a valid detector needs about 25
weeks to reach it.

Horizon (randomized phase): 12 calendar weeks from the detector's arm start or 400 eligible
sessions, whichever comes first. No interim looks; the analysis after the horizon is the final and
only look. Stopping early happens only through a lowering demotion row (any lowering cause ends the
horizon; an `evasion_pair` flag does not); only a human-label harm row is the harm event.

Power note (assumptions: a repeat-fire mean of 0.47 per eligible session in the observe arm, a
variance-to-mean ratio of 3.65, 80 % power, two-sided alpha 0.05, a normal approximation to D). The
0.47 is an upper bound: every week-1 fire was a subagent fire, which the primary excludes, so the
week-1 primary baseline is about 0 and at that baseline no effect is detectable. Over the 12-week
interactive-only horizon, 2.4 interactive sessions a day x 84 days is about 202 eligible sessions,
about 100 per arm, and the minimum detectable rate ratio is about 0.16; at the 400-session cap,
about 200 per arm, it is about 0.36. A rate ratio of 0.5 would need about 366 eligible sessions per
arm and 0.67 about 935, more than the horizon holds, so the design confirms only large effects. A
detector that passes the 0-FP gate fires less often, which lowers power further. Week 1 is
exploratory and not part of any
confirmatory analysis: v1 had 0 human-confirmed TPs among 23 fires in 7 sessions (95 % upper bound
on per-session precision 35 %). Every fire matched one pattern, agent-created scratch or worktree
cleanup, so v1 does not separate it from the target hazard.

The seal refuses unless this subsection holds the entry gate (at least 30) and a horizon line, and
the block below holds `permutation_seed_sha256`, `shadow_seed_sha256` and `arm_rate`.

### Null handling

A permutation p-value (Holm-adjusted across detectors) at or above 0.05 is reported as "no
detectable effect at this horizon", never as "no effect".

### Frozen instruments

Filled by `compile seal`: one line per detector (`name strength sha256`: the strength from the
catalogue registry and the hash from `catalogue.source_table()`), `report_sha256`, one line per
approved proposal (`P-id mode proposal_sha256`, from the newest non-revoked row of
`approvals.jsonl`), `shadow_seed_sha256`, `permutation_seed_sha256`, `arm_rate`, `measurement_start` (the greatest, over those
proposals, of each one's newest `observe` row of `approvals.jsonl`: the restart point of the promotion ladder, so a warn promotion
does not move it) and `sealed_at`. `report_sha256` covers exactly seven files in `ace_playbook/compile/`, `arms.py`, `arms_stats.py`,
`arms_window.py`, `labels.py`, `labels_queue.py`, `metrics.py` and `report.py` (their bytes in name order, joined by a NUL byte); the
detector code is covered by the per-detector hashes, not by `report_sha256`. `shadow.py`, `offline.py` and `events.py` are not in it,
because they must still change before the first `arm_start` (the hook's arm and flag fields, the symmetric `offline.classify`); the
`arm_start` amendment records their sha256 instead, and those of `demotions.py` (its causes decide what ends a horizon), `modes.py` and
`approvals.py` (they decide the family and condition (6)). The arm analysis that computes the primary (`arms.py`, `arms_stats.py`) is
built and hashed here before the seal, never written after it.

(filled by compile seal)

### Amendments

Changes after the seal are made only as dated appendices under the top-level "Amendments" heading
below, never as edits of sealed text.

## Amendments

### 2026-10-29 - amendments from the section 9.10 statistician pass (made before the seal, so they edit the draft)

Source: the hostile-statistician review of 2026-10-29 (verdict NOT SEALABLE; blocking B1-B6, important I1-I8, minor M1-M2) and the
operator's decisions D-184 (week 1: no arms yet, seal later) and D-186 (the entry gate and the horizon below). Nothing was sealed, so
these are edits of the draft, recorded here so that the reasons survive. What changed:

- B1, B2: "N" is no longer a free number. The entry gate is at least 30 human-labelled sessions-with-fire (first fire per session
  counts), 0 FP, 0 unlabelled, counted over sessions because fires within a session share one label (effective n is about 9, not 30,
  if fires are counted). At the week-1 rates a valid detector needs about 25 weeks to reach it. The horizon is 12 calendar weeks
  from the detector's arm start or 400 eligible sessions, whichever comes first. `compile seal` now refuses without both.
- B3: the analysis is stated in the text (one primary per detector, D, a permutation test with 10,000 Bernoulli(`arm_rate`) re-draws
  under a committed permutation seed, two-sided p = (1 + #{|D*| >= |D|}) / 10,001, intention to treat, Holm). `labels.py` joins the
  `report_sha256` file set (it shapes the TP/FP numbers). The arm analysis code must be built and hashed before the seal.
  `report_sha256` changed from 5c9cb343d93adbbe9519bacdad03f9fb0e4afe32c332d489f390c4e64404ed24 (metrics.py + report.py) to
  88a7fc26f87fe2ce3786ef480d13252eed91136d1450421cdb3532a16463525c (the six files named in "Frozen instruments": `labels.py` and the new
  `labels_queue.py`, `arms.py`, `arms_stats.py` joined `metrics.py` and `report.py`; an intermediate value of
  399061fa96eafd4ec0c7cfe628878ba974dd7249a9782c2886e1850ffe908c89 covered `labels.py` + `metrics.py` + `report.py` only). The value that
  counts is the one `compile seal --dry-run` prints on the day of the seal. The arm analysis is built: `python3 -m ace_playbook.compile.arms
  report` (`assign`, `eligible_sessions`, `primary`, `permutation_test`, `holm`), the `_session` summary row records the session `start`,
  and the label queue hides mode and arm during a phase.
- B4: prereg-v1 seals only "Shadow-mode additions"; the injection RER experiment stays a draft and `holdout_enabled` stays false
  during any shadow arm phase (line 7 of this file). The 16-versus-24-week to-do of the injection experiment is dropped: the 24-week
  look is the final one, with the verified boundaries z 3.471, 2.454 and 2.004.
- B5, B6: eligibility has a time window ([arm start, horizon end), by the first transcript event), counts interactive sessions only,
  and keeps revoked and kill-file gaps in the assigned arm. The primary counts only rows with `agent_id_present` false;
  subagent fires are secondary. The analysis runs 7 days after the horizon, after one forced offline scan; unscanned sessions are
  reported as missing.
- I1-I3: a symmetric correction rule and a label queue that hides mode and arm; a demotion ends the phase and is the harm event (superseded by round 3);
  one primary per detector with Holm across detectors; evasion, sessions-with-fire and friction are secondary with no p-values.
- I4-I8: friction is descriptive; the power note is in "Timing and stopping"; the spillover estimand is stated and lesson minting is
  frozen from sessions carrying `ACE P-` during the phase; Wilson only for sessions-with-fire, a permutation interval for D.
- M1: the week-1 result is stated as exploratory (0 human-confirmed TPs among 23 fires in 7 sessions; one pattern; not separated from
  the target hazard). M2: the shadow seed value lives outside anything the agent reads by default; a Bash tool can read any file
  the operator can, so the limit is documented and the seal records only sha256 values (`shadow_seed_sha256`,
  `permutation_seed_sha256`).

### 2026-10-29 - round-2 statistician pass (made before the seal, so they edit the draft)

Source: the second hostile-statistician pass (verdict SEALABLE WITH THE BLOCKING FIXES). What changed:

- `arms report` refuses (exit 1) a fire row of an eligible session that lacks `arm` or `agent_id_present`, or whose recorded `arm` differs
  from `arms.assign`; without this the analysis would split sessions that all received the same treatment and return D = 0, p = 1. The text
  now states the pre-arm-start conditions (hook randomizes and flags every row, `shadow.py`/`offline.py`/`events.py` hashes recorded in the
  `arm_start` amendment, symmetric `offline.classify`, human labels only for harm and demotion, spillover and minting freeze exploratory)
  (superseded by round 7).
- `arm_start` lines are read only under the top-level Amendments heading and are refused when earlier than the block's `sealed_at`.
- I2 is settled one way: a demotion ends the detector's horizon (the harm event) (superseded by round 3); the
  "demoted gaps kept in arm" sentences are removed and `arms_window.horizon_end` takes the first demotion from demotions.jsonl.
- The Holm family is every detector with an `arm_start`; no adjusted p and no rejection is printed until all of them are final.
- The power note and the rate sentence are rewritten for the 12-week interactive-only horizon with a primary that excludes subagent fires
  (about 100 per arm, minimum detectable rate ratio about 0.16; about 200 per arm at the cap, about 0.36); the 7.2 % and 2.4 a day are Step 0
  rates, week 1 was 7 of 34 sessions (20.6 %).
- `report_sha256` changed from 88a7fc26f87fe2ce3786ef480d13252eed91136d1450421cdb3532a16463525c to
  70d67f87b0b7960176aee8b9172274aa5f841cb314e44768fc2aaea31cc40d7d (seven files: `arms_window.py` joined). `offline.py` and `events.py` are
  deliberately not hashed here: they must still change before the first `arm_start`, so freezing them now would force a change after the seal;
  their sha256 is recorded in the `arm_start` amendment instead.

### 2026-10-29 - round-3 statistician pass (made before the seal, so they edit the draft)

Source: the third hostile-statistician pass (verdict SEALABLE WITH TWO SMALL BLOCKING FIXES). What changed:

- A (`arms.py`): the proposals of a detector are taken from the newest non-revoked rows of `approvals.jsonl` (superseded by round 4), not
  from the shadow rows. A detector with an `arm_start` whose approved proposal never fired (or whose fires the warn arm suppressed) had no
  rows and stayed pending for good, so no Holm result was ever shown. It now enters the family at D = 0, p = 1 (also when an arm has no
  eligible session). An `arm_start` for a detector with no approved proposal is refused (exit 1), never left pending. The report prints
  the cause of a demotion that ended a horizon.
- B (text): any `demotions.jsonl` row of the detector's proposals at or after the arm start ends the horizon, whatever its cause, and only a
  human-label `label_fp` or `label_fp_pair` row is the harm event. The `arm_start` amendment also records the sha256 of `demotions.py`,
  and is anchored like the seal: a signed tag `arm-<detector>` on its commit plus an OpenTimestamps proof of the amendment line's sha256
  (a commit time is set by the author and proves nothing).
- `report_sha256` changed from 70d67f87b0b7960176aee8b9172274aa5f841cb314e44768fc2aaea31cc40d7d to
  acb09a3f4e9f7c95c583e16de5f00813d4460d3702cb4706bbb7f76cdc030692 (seven files: `arms.py` and `arms_window.py` changed).

### 2026-10-29 - round-4 statistician pass (made before the seal, so they edit the draft)

Source: the fourth hostile-statistician pass (verdict SEALABLE WITH THE BLOCKING FIXES). What changed:

- R1 (`arms.py`, `arms_window.py`): the Holm family is fixed at the arm start (intention to treat). A proposal belongs when its newest
  approvals row stamped at or before the arm start is not `revoked`; a revocation, re-approval or mode change stamped later changes nothing,
  so no hypothesis leaves the family after the data are seen and no revocation vetoes the other detectors.
- R2: an approved proposal whose proposal file is missing or unreadable is refused, no longer dropped silently.
- R3 (text): one sentence states that the Holm family holds one hypothesis per approved proposal, not per detector (conservative).
- R4 (text): early stopping happens only through a demotion row (any cause ends the horizon); only a human-label harm row is the harm event.
- R5: new pre-arm-start condition (6), also enforced in `arms.py`: at the arm start every family proposal is approved at `warn` or `block`
  with no demotion in force, so a null by construction cannot join Holm.
- Minor (text): the two history entries that still called a demotion "the harm event" are marked superseded by round 3.
- `report_sha256` changed from acb09a3f4e9f7c95c583e16de5f00813d4460d3702cb4706bbb7f76cdc030692 to
  37a311cf7ca5e8e9a323da09c7ba6bc74d777e309805f76fb564be2b0f387143 (seven files: `arms.py` and `arms_window.py` changed).

### 2026-10-29 - round-5 statistician pass (made before the seal, so they edit the draft)

Source: the fifth hostile-statistician pass (verdict SEALABLE WITH THE BLOCKING FIXES). What changed:

- S1 (text): `modes.py` and `approvals.py` decide the family and condition (6) (`modes.anchor`, `modes.in_force`, `approvals.read_chain`), so
  the `arm_start` amendment also records their sha256 (the list is now `shadow.py`, `offline.py`, `events.py`, `demotions.py`, `modes.py`,
  `approvals.py`); "Frozen instruments" names the same six. Condition (6) now reads "no demotion row stamped before the arm start is in
  force", which is what the code filters.
- S2 (`arms.py`, `arms_window.py`): the `arm_start` line gains a third field, `approvals=<64 hex>`, the `hash` of the last row of the verified
  approvals chain at the arm start. The family is read from the chain prefix ending at that row (append order); a row appended later, even
  one stamped before the arm start (`compile approve` back-fill), is ignored. A line without the field, with a malformed one, or naming a hash
  that is not in the chain is refused. The head row may itself be stamped after the arm start (it only bounds the prefix); membership is
  still anchored at the arm start stamp.
- S3 (text): the Holm sentence now says "approved at the arm start", read from the chain prefix the line names, later rows change nothing.
- Minor (text): the round-3 line "newest non-revoked rows" is marked superseded by round 4.
- Tests added: the back-dated append (probes BF, BF2), a head not in the chain, a line without or with a malformed head, a head row stamped
  after the arm start, the tie of a re-approval at the demotion stamp, a revocation exactly at the arm start (sole: refused; with a sibling:
  dropped), and a revocation after the start with the approved copy unlinked.
- `report_sha256` changed from 37a311cf7ca5e8e9a323da09c7ba6bc74d777e309805f76fb564be2b0f387143 to
  5029fd0b52f410abbbd2ae18d28b8d6746270072b523406da3151e1b770f1058 (seven files: `arms.py` and `arms_window.py` changed).

### 2026-10-29 - round-6 statistician pass (made before the seal, so they edit the draft)

Source: the sixth hostile-statistician pass (verdict SEALABLE WITH THE BLOCKING FIXES). What changed:

- F2 (`arms_window.py`, text): only a lowering demotion row (`to_mode` differs from `from_mode`) ends the horizon or counts under condition (6);
  an `evasion_pair` flag keeps the mode and neither ends the horizon nor refuses the family. Stopping on it would make the analysed sessions
  depend on the assignment (candidates exist only after a delivered block). The text of the labelling paragraph, "Timing and stopping" and (6) says so.
- F2b (same fix): an `evasion_pair` row stamped before the arm start no longer refuses the family.
- Residual F1 (recorded, not fixed): a demotion stamped before the arm start but appended after the head refuses the family (the demotions chain is
  not bounded by the head); only an API call with an explicit `now` can write one, and it fails closed.
- Residual F3 (recorded, not fixed): a malformed first `arm_start` line of a detector is not replaced by a corrected second one (the first line
  wins), so the analysis refuses for good; run `arms report` right after adding the line.
- `report_sha256` changed from 5029fd0b52f410abbbd2ae18d28b8d6746270072b523406da3151e1b770f1058 to
  c064b599fdc370ccf5029eb9813321fe0f6419f50e3e716cfb552602bd99febf (seven files: `arms_window.py` changed).

### 2026-10-29 - round-7 statistician pass (SEALABLE AS IS; made before the seal, so they edit the draft)

Source: the seventh hostile-statistician pass (verdict SEALABLE AS IS).
Text and docstring edits, plus one new output: the delivered-path error count (arms.py, arms_window.py). What changed:

- P6 (important, text): `error_rate` and `lesson_inactive` are not arm-symmetric under the null, because the delivered-path writes of the hook and
  the model-visible `ACE P-` text exist only in treated-arm sessions.
  New pre-arm-start condition (7): the delivered path logs its failures as `error:delivered:<Type>` lines.
  The analysis prints their count whenever an `error_rate` row ends a horizon.
  During a phase no status change (retire, supersede, deactivate) of a family lesson is written from a session whose transcript carries `ACE P-`.
  The minting freeze of the spillover section now covers status changes of family lessons too.
  The hook's emission of the tag and the freeze are post-seal build items.
- P5 (text): the harm paragraph now says a human-labelled FP of an observe-arm fire counts as an FP at the proposal's approved mode.
  Harm is counted symmetrically across arms.
- P7 (`hooks/INSTALL.md`, outside the hash): a demotion in force before the arm start is lifted only by a re-approval that differs from
  the newest `approvals.jsonl` row of the proposal.
  The same proposal hash and mode as that row appends nothing.
- Docs-lens on the round-7 wave: condition (7) rewritten so nothing hashed changes after the seal (the `error:delivered:` tag defined; the
  analysis counts it now).
  Condition (5) narrowed to the spillover comparison; the re-approval note corrected (the mode must differ from the newest approvals row);
  wording minors.
  Round 2: the Amendments wording, the inclusive end stated, minting named in (7), the log line format fixed.
  A lowering row after the natural end no longer counts as the horizon's demotion.
  Three edge tests were added (outside the hash).
- Wording: "lowering" added to the docstrings of `arms_window.py` (`family`, `horizon_end`) and `arms.py`.
  The labelling paragraph now says "sessions that start after a lowering demotion".
  `docs/design/compiler.md` (Arms section) lists the hook side of condition (7) among the open conditions.
- `report_sha256` changed from c064b599fdc370ccf5029eb9813321fe0f6419f50e3e716cfb552602bd99febf to
  615fe3dd3a517fe7de5ac572b9c1e3a1b17d0c41a678d6df13953de9245ddbc3 (seven files: `arms.py` and `arms_window.py` changed;
  the delivered-error count and the natural-end rule are new).
