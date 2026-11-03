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

## Shadow-mode additions (SEALED 2026-10-29 prereg-v1)

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
An `evasion_pair` flag lowers nothing and does not end the horizon, and a lowering row stamped at or after the natural end ends nothing.
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

```text
detectors:
  commit_without_push weak 0ecc11354e149bf971e1acc4d5f9dd20730235cd4fbe2163dc87d5c03dbae885
  edit_unread_session strong df4de122c0027b40953e0903d6650452c198d929f84a130ee1d66dca9515adf2
  edit_unread_turn weak f0c2e1d0e86f1047777c1b5ae22a184022cb868ea00fa21adfd06226352607cb
  exitplan_question_block strong fd62ef5b1dfb4f01917e3dc9c326b5d22b9b3b429c507a4c9d6f265dffabf6ec
  preclaim_audit_block strong 23833342205da79ed6a61b36321e0483056f105dd1d9b9910bc6598f35c579a6
  rm_rf weak 0c3ae8a16b87f8d31b5e35ca995b11383ee679de0f1e318fdbb824b50127c47d
  rm_rf_nonscratch strong 53678e1a045ff59356e76fd3c65656d5b1f7e5b18716d321ebe8233ceb296a92
  unparseable weak b0ec0f6f044dee82b796923cf4829405a68c98d14a06878942846b2529e1a415
  user_correction weak 9de3bc6caf76ed9c2b5ba24c319450f7e41b17998f39e72c8094c4a4e4f0da4e
report_sha256 8b9d55c625f53c4e9474cdaaf2a16f0605779b0c0722fd5d3a8e0e43ac93330d
proposals:
  P-f9407d6c observe 5fdcedb9aa8e7c6ad0daffb6c88b268eb25b8199679663fbee5325187c41190c
shadow_seed_sha256 4a1fbc5ea985224ffe33add3219bd246fdbc4f4ec86d1362a2d2d0b57bcc1d74
permutation_seed_sha256 359736173bf92b85b4b0f03fd9882d2e22984a103271d0576922f4c28c943e59
arm_rate 0.5
measurement_start 2026-10-20T16:10:26Z
sealed_at 2026-10-29T05:44:23Z
```

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
Text and docstring edits, plus two behaviour changes: the delivered-path error count (arms.py, arms_window.py) and the natural-end rule (arms.py). What changed:

- P6 (important, text): `error_rate` and `lesson_inactive` are not arm-symmetric under the null, because the delivered-path writes of the hook and
  the model-visible `ACE P-` text exist only in treated-arm sessions.
  New pre-arm-start condition (7): the delivered path logs its failures as `error:delivered:<Type>` lines.
  The analysis prints their count whenever an `error_rate` row ends a horizon.
  During a phase no status change (retire, supersede, deactivate) of a family lesson is written from a session whose transcript carries `ACE P-`.
  The minting freeze of the spillover section now covers status changes of family lessons too.
  The hook's emission of the tag and the freeze are post-seal, pre-arm-start build items.
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
  A lowering row at or after the natural end no longer counts as the horizon's demotion.
  Three edge tests were added (outside the hash).
  Round 3: wording (two behaviour changes named; at or after the natural end), compiler.md minting, the natural-end edge test.
- Wording: "lowering" added to the docstrings of `arms_window.py` (`family`, `horizon_end`) and `arms.py`.
  The labelling paragraph now says "sessions that start after a lowering demotion".
  `docs/design/compiler.md` (Arms section) lists the hook side of condition (7) among the open conditions.
- `report_sha256` changed from c064b599fdc370ccf5029eb9813321fe0f6419f50e3e716cfb552602bd99febf to
  8b9d55c625f53c4e9474cdaaf2a16f0605779b0c0722fd5d3a8e0e43ac93330d (seven files: `arms.py` and `arms_window.py` changed;
  the delivered-error count and the natural-end rule are new).

### 2026-10-29 - arm_start hash list: the hook files that decide randomization, output and silence

Pre-arm-start condition (2) names six files for the sha256 record of an `arm_start` amendment: `shadow.py`, `offline.py`, `events.py`,
`demotions.py`, `modes.py` and `approvals.py`.
The output decision (`shadow_guard.decision`) was already outside `shadow.py` at the seal (298b06d).
After the seal the delivered path moved from `shadow.py` to `shadow_arm.deliver`, `demote` moved from `offline.py` to `offline_demote.py`
(where the human-only rule was added), and the randomization (`shadow_arm.assign`) and the phase marker (`phase.py`) were added outside `shadow.py`.
Every `arm_start` amendment therefore records the sha256 of THIRTEEN files, each named by its path from the repository root: the six named by
condition (2), plus `shadow_arm.py`, `shadow_guard.py`, `phase.py` and `offline_demote.py` (all in `ace_playbook/compile/`).
Three more were added on the operator's ruling: `ace_playbook/compile/chains.py` (it verifies the signed phase marker, so it decides silence),
`ace_playbook/reflect/runner.py` and `ace_playbook/sweep.py` (the minting freeze of condition (7)).
The sealed block, `report_sha256` (8b9d55c625f53c4e9474cdaaf2a16f0605779b0c0722fd5d3a8e0e43ac93330d) and the analysis are unchanged.
The `arm-<detector>` signed tag and the OpenTimestamps proof of the amendment line's sha256 cover the line as before.

### 2026-10-29 - detector v2: rm_rf_nonscratch proves three exact scratch shapes

Week 1 of the measurement read 0 true positives and 36 false positives on `rm_rf_nonscratch`. Every fire was a subagent cleaning its own
scratch (the session scratchpad under `/private/tmp/claude-501/`, or a worktree it had created), written as `cd <scratch> && rm -rf ./*`,
through a shell variable, or against a worktree path. The lesson (L-b3d1fba9, a 7.8 GB loss) was encoded too broadly: scratch the agent made
is not "a directory". Detector v2 (decision D-204, narrowed by D-209) keeps the name, the lesson and the family and changes one thing: a Bash
command whose RAW text is, in full, one of three exact shapes (`cd <scratch> && rm -rf <rel>...`, `NAME=<scratch>; rm -rf "$NAME"...`,
`git worktree add <p> && rm -rf <p>`) with every joined target scratch by v1's own rule (S3: the path this command's `worktree add`
created, whatever its root; nothing is checked against SCRATCH for S3) does not fire; every other command takes v1's literal rule unchanged. No shell
is modelled (`docs/design/compiler.md` 4.1.2). The change adds `ace_playbook/compile/grammar.py` to the text every
detector hash covers and edits `detectors.py`, `catalogue.py` and `srchash.py`
(also edited: `proposal.py` and `shadow.py` (one line, below); new: `contracts.py`; none of the three is covered by a detector hash;
`shell.py` is byte-identical to the commit before this change), so every hash moved. The nine
lines `name strength sha256`, as printed by `compile seal --dry-run` on the commit that carries this entry (regenerated by script after every
fix wave of the build, the last after the narrowing to the grammar; see 4.1.2):

```text
detectors:
  commit_without_push weak 4d7995f22ae81c3f8a6040aa7ee4bacc6614af1f5a29d8966a511450eb5da828
  edit_unread_session strong 4a8d9ffc3efdff184b0af3284c870d00e6902c9a0aae3ab95f862685a7f261d9
  edit_unread_turn weak 48e43655450c841631c47cb54bbe363089882004b8ce8cbca200fd717eced69f
  exitplan_question_block strong 315f40a2239247383d8e39f82412a23ade493291a37e1d57b449bfa5bcb32281
  preclaim_audit_block strong 9c51713970ced98bb4e72550cae93cc10b7e84b3a0a9df9924ce81ffe16d77ea
  rm_rf weak 0069832b2cc0a13fb4efb6d3e41f85c9c4cba1b5291f642b06515c668353ef5b
  rm_rf_nonscratch strong f8bc41896639d4cf3ba403828f9fd2d936e91cf92bbfa9a70df6b7915df5d833
  unparseable weak 11f4d32a667a0d8dbb8b9744d25c542ad58cdb7e7a5b907a29165eef615dbeaf
  user_correction weak 92a7297cf4b0d863944ce9282dafa70671b7cc27b178ffbee8dddf4593d294c0
```

These supersede the per-detector lines of the sealed block for provenance only. The sealed block is not edited. The proposal P-f9407d6c
is retired by the source change (`source_sha256` is stale); a new proposal id follows the operator's re-propose and approve in observe,
and the entry gate (at least 30 human-labelled sessions-with-fire, 0 false positives, 0 unlabelled) and the ladder restart for the new id.

`report_sha256` (8b9d55c625f53c4e9474cdaaf2a16f0605779b0c0722fd5d3a8e0e43ac93330d) is unchanged: `compile seal --dry-run` prints the same
value and the seven files it covers hash to it. Of the thirteen files that every `arm_start` amendment records (the entry of 2026-10-29
above), twelve are byte-identical to the commit before this change (14f7ecd). One is not: `ace_playbook/compile/shadow.py` changed by one
line, because an approved proposal may now carry the optional `contract` key (`pending_body` accepts it; no randomization, output or silence
decision moved). Its sha256 was 6f2355d8a788b99e424cf821939d11efd282bb93a743f8f0d3b34f033ed95c54 and is
97a4014fc383d73722f656880f75563ef37059d6b47fd5fb7145302d2dc67370; the `arm_start` amendment records the hashes in force when it is written,
as before.

### 2026-11-03 - lesson-text holdout (field): the draft RER activated for three lessons

This entry activates the draft sections "Question" to "Null handling" above (the injection RER experiment) for three lessons, with the
deviations listed below. Where this entry and the draft disagree, this entry governs. The sealed block and the shadow-mode analysis are
not edited. Arms stay NO-GO, and a holdout phase excludes an arm phase (sealed text, lines 8-10).

Lessons, each scored by one detector (the hashes are those of the 2026-10-29 detector v2 entry above, unchanged at commit 2c88d58):

```text
L-cbfb7a57 (pre-claim audit)              preclaim_audit_block strong 9c51713970ced98bb4e72550cae93cc10b7e84b3a0a9df9924ce81ffe16d77ea
L-f8a355f9 (read in the turn before edit) edit_unread_turn weak 48e43655450c841631c47cb54bbe363089882004b8ce8cbca200fd717eced69f
L-93544dae (answer before ExitPlanMode)   exitplan_question_block strong 315f40a2239247383d8e39f82412a23ade493291a37e1d57b449bfa5bcb32281
```

`preclaim_audit_block` and `exitplan_question_block` count the block events of two operator hooks (`stop-mid-task-audit.sh`,
`pretooluse-exitplan-question-check.sh`), matched by hook name. `edit_unread_turn` is an offline detector over the transcript; no hook
blocks an edit of a file not read in the same turn (Claude Code itself only requires a read earlier in the session).

The injector runs in `pinned` mode, which shows the pinned lessons. The pinned set is frozen for the horizon as exactly these three and
L-b3d1fba9, all global. L-b3d1fba9 is set `holdout_exempt` for the horizon, so it is always shown.

Deviations from the draft, each decided before any holdout session:

- (a) Timing: one confirmatory read (decision D-235), replacing the looks at 8, 16 and 24 weeks. There is no harm-only interim. For the
  two hook-scored lessons, the hooks keep blocking in both arms, so hiding a lesson's text opens no harm path the hooks do not stop. For
  L-f8a355f9 no hook stops the mistake; the risk (an edit against stale content) is judged small for the horizon and is accepted.
- (b) Outcome: the primary outcome is binary: at least one violation per (session, lesson), as the draft's "whether the specific mistake
  recurs". The secondary outcome is the violation count per (session, lesson).
- (c) Content eligibility (draft line 42): for the horizon, the three rules are removed from the operator's global CLAUDE.md (Discipline
  rules 6, 8 and 9) by a dated edit (decision D-232) and restored at the end. Residual exposure remains. The memory indexes of two
  project directories (SymbolicSystems, SymbolicSystems-ThinkHospitality) state the ExitPlanMode rule, and the description of the
  `brutal-honesty` skill, listed in every session, names the audit format. Both are present in either arm. For the two hook-scored
  lessons, a hook's block message restates the rule, but only after a violation, so this exposure depends on the outcome. It does not
  affect the binary primary, because the first block is itself the outcome event. It can affect the count secondary. Auto-memory files
  may also gain rule text during the horizon. At the switch-on and at `holdout_end` the operator records, in a private file in the data
  dir, every line (file path and full text) of the auto-loaded files (`$HOME/.claude/CLAUDE.md`, `$HOME/.claude/rules/*.md`, the root
  CLAUDE.md of each working directory with a field session, and `$HOME/.claude/projects/*/memory/MEMORY.md`) that matches, by
  `grep -n -i -E`, a broad per-rule pattern: pre-claim `pre-claim|preclaim`, read-before-edit `read.{0,30}before.{0,30}edit|read the
  target file`, exitplan `exitplanmode|exit plan mode`. The sha256 of each record goes in a dated line. Before the read, and so before
  any per-arm data is seen, the operator compares the two records and classifies every added or changed line as a statement of a rule or
  a mere mention, in a dated line; drift maps to a deviation only as (k) states. A paraphrase that matches no pattern is not caught
  (a limitation). The `brutal-honesty` skill description is not counted: it is the same in both arms, and a change to it is logged in
  a dated line. The estimate is therefore the incremental effect of the injected text over this residual exposure. The draft rule is
  amended to say so.
  The subject is not blind. Of the 40 transcripts counted in (d), 34 ran in the rebuild-lab project, where the agent can read this entry
  (the lesson ids and their rules) and sees its own block, so it can infer its arm for a lesson. The estimand is the effect of the text on
  a subject that may be aware of the experiment, demand effects included. A split by the project directory holding the session's
  transcript (a name ending in `-rebuild-lab` or containing `-ace-playbook`, against all others; joined by session id) is pre-stated as
  exploratory only.
- (d) Step-0 gate (draft: "about six lessons at 2 % or more"): replaced by measured rates and a power table, stated before data. Counts
  were read on 2026-11-03 from `detector_events.jsonl`, offline tier, without the interactive filter (77 of the 79 injection rows written
  before 2026-11-03T02:10Z carry no entrypoint; the field was added that day, and the two that do are 02:02Z `cli` and 02:07Z `sdk-cli`).
  The share of sessions with at least one violation, for preclaim / edit_unread_turn / exitplan, is 34 % / 18 % / 14 % of the 50
  sessions with prompts >= 1, and 57 % / 32 % / 25 % of the 28 with prompts >= 3. `holdout_power` at commit 2c88d58 can fit only the 9
  sessions whose row carries a `start` (rows written before 2026-10-29 lack the field). Its fit (22 % / 22 % / 11 %) gives the power below: 2 eligible
  sessions/day, binary primary, Holm over 3, normal method, 400 simulations, seed 1.

  | lesson | 12 weeks, RR 0.5 | 12 weeks, RR 0.67 | 24 weeks, RR 0.5 | 24 weeks, RR 0.67 |
  |---|---|---|---|---|
  | preclaim | 0.34 | 0.14 | 0.68 | 0.30 |
  | edit_unread_turn | 0.40 | 0.14 | 0.69 | 0.27 |
  | exitplan | 0.15 | 0.07 | 0.42 | 0.14 |

  The rate of 2 is conservative. From 2026-10-19T00:00Z to 2026-11-03T03:00Z, 40 transcripts started with `entrypoint` `cli` and carried
  the injected block, which is 2.6 per calendar day. Counted: top-level transcripts in the project directories whose first record that
  carries a timestamp falls in that span, whose first `entrypoint` is `cli`, and which have an attachment record carrying the block. The expected
  n is about 168 eligible sessions in 84 days. No minimum n is set: the read happens at the registered time whatever n is. The field
  read is expected to be inconclusive for the registered effect of 0.67. A controlled lab (`ace_playbook/lab`, its own
  pre-registration) carries the causal question.
- (e) Assignment and estimand: the injector's coin (`ace_playbook/inject/injector.py`, `coin` and `is_holdout`) is the first 8 hex
  digits of sha256 of `seed|session id|lesson id`, read as a fraction of 2**32. The lesson is hidden when that fraction is below
  `holdout_rate` 0.5. Draws are independent per (session, lesson). Each `/clear` starts a new session id and so re-draws. The estimand for
  each lesson is marginal over the other two lessons' coins (the block shows a varying subset).
- (f) Eligible sessions: a `_session` row with a `start` inside the registered window and prompts >= 1, and the session must be
  interactive (decision D-239): every injection row of the session carries `entrypoint` `cli`, which the injector records from
  `CLAUDE_CODE_ENTRYPOINT`. Automated sessions (SDK reviews, `claude -p` probes, the brief) are excluded as `not_interactive`. Subagent
  transcripts are never scanned.
- (g) Analysis: the code at commit 2c88d58. The sha256 values are (the last line is the sealed `report_sha256` over its seven files,
  section "Frozen instruments" above, which hold `arms_stats`, `metrics`, `labels` and `report`, all imported by the analysis):

  ```text
  ace_playbook/compile/holdout_report.py c05de1c362c2397c75e484ccefc99bb5f90a5c433d72cd4d7b3c65274ca9048f
  ace_playbook/compile/holdout_io.py     d542ddc6a285ced9dc96ad6c9f4f05a3dcd7d209184e0cd19d85f4888829970d
  ace_playbook/compile/holdout_power.py  6705752c5a81b49951583071fea2ce93239fa89a687e0f0b5e3a8c9ee5fcd9f2
  ace_playbook/inject/injector.py        d193e5e214425282510674b003c1cc57bf4e45c22ecbb3bc6e0ffc97e21a8268
  ace_playbook/compile/state.py          bd7e6e3dd23738a5ee2d34e8d1895625f56554ccf59045a8ff9a26265e27d8bc
  report_sha256                          8b9d55c625f53c4e9474cdaaf2a16f0605779b0c0722fd5d3a8e0e43ac93330d
  ```

  For each lesson the report gives the risk difference D (shown minus hidden) in the any-violation rate, a Wilson interval per arm, and
  the rate ratio as a point estimate. The test is two-sided: |D| is compared against 10,000 re-draws of the coin (`arms_stats.permutation
  _test`, arm rate 0.5), seeded by the config key `holdout_permutation_seed`. That is an exact test of the sharp null of no effect for any
  session; no clustering is used. The Wilson intervals treat sessions as independent and are descriptive. Holm runs over the three
  lessons at alpha 0.05, and the count secondary is a separate family with its own Holm. No interval is reported for D or for the rate
  ratio; the secondary JSON's `shift_ci95_null_redraws` field (D minus the quantiles of the shifted null re-draws, not a confidence
  interval) is not reported either. The draft's clustered Poisson cross-check is dropped.
- (h) The registered window is 2026-11-10T00:00:00Z to 2027-02-02T00:00:00Z (84 days). The report reads it from the config keys
  `holdout_start` and `holdout_end`, which it does not compare with these literals. The read counts as confirmatory only if the window
  line it prints equals them. The read runs after the first nightly sweep that completes on or after 2027-02-05; that sweep's completion
  time is logged in a dated line before the read, and the read's output is committed as it is.
  It runs with the default `--min-prompts 1` (the report refuses another value outside the descriptive mode).
- (i) Exclusions, each counted and reported. In the order the code applies them: null_session_id (an injection row without a session
  id); unmatched_session (an eligible session with no injection row); then, per session, no_start, below_min_prompts, outside_window,
  not_interactive, refused, holdout_off, mixed_rate, rate_not_half, seed_fp_mismatch and assignment_conflict; unmatched_injection (an
  injection session in the window with no `_session` row); and, per lesson, lesson_not_assigned. Every one of these is decided without
  the coin, so they cannot differ by arm through the analysis. Per-arm exclusion counts are not computed. A lesson in `dropped_cap` is
  analysed as assigned SHOWN (intention to treat). The read refuses if any of the three lessons was `holdout_exempt` at any time before
  `holdout_end` (read from the delta log).
- (j) Frozen at the switch-on and re-checked before the read: the seeds, the two scoring hooks and the three hook entries in
  `$HOME/.claude/settings.json`, the text and `applies_when` of the four pinned lessons, `HEADER` and `FOOTER` of the injected block,
  the config values `inject_mode`, `holdout_rate`, `max_global` and `inject_max_chars`, the edited CLAUDE.md, the active pinned set, and
  the injector code set. Every value known before the switch-on is written here now (computed 2026-11-03 at main 2c88d58); only the
  two seed hashes and the post-edit CLAUDE.md hash are placeholders:

  ```text
  holdout_seed_sha256                      <HOLDOUT_SEED_SHA256>
  sha256(holdout_permutation_seed)         <PERMUTATION_SEED_SHA256>
  CLAUDE.md (global, after the D-232 edit) <CLAUDE_MD_SHA256>
  stop-mid-task-audit.sh                   256dc2b7d87ac0b1d8d49ebce7b5d27c30362de0b2fdb2b754361bef9777c79b
  pretooluse-exitplan-question-check.sh    1be70af396c4b5ad0a3ba2a0b004eef21bc740addcd13e4884ab0ee53f4ff1e4
  settings entry SessionStart              31945a89c652b4e8f1ee719d1942a1fd578d38716d72067637d5ba5ba1ad5f99
  settings entry Stop (preclaim hook)      9d30cf896d099c12f01dde46b9426a0b904bd06d6c2fab14904e2b7a7f7a40e7
  settings entry PreToolUse ExitPlanMode   34da19728cb6cfe1feace78e777deaa2c43f57990c7d1188cbab426a32eb64e0
  L-cbfb7a57 / L-f8a355f9 / L-93544dae / L-b3d1fba9 text+applies_when  359d8dc49d25e4fe6dd75b8e146c391cee85609ba93d2b2ce8f08f769abd7912
  injector HEADER+FOOTER                   9724a65957f24d5ec9c62d43b4a5bc066fc9ab0580d4962b60cc89bacf1193c5
  config values                            inject_mode=pinned holdout_rate=0.5 max_global=4 inject_max_chars=7000
  active pinned set (id:status:scope)      L-93544dae:active:global,L-b3d1fba9:active:global,L-cbfb7a57:active:global,L-f8a355f9:active:global
  injector code set                        19e2a985e36288f77666ecc5ae1904f11daf412b1f99de6aa02dfa49d5d632e9
  ```

  Each value is computed by one stated command. The seeds are generated as `secrets.token_hex(32)` each; the operator types each one
  into `python3 -c "import getpass,hashlib;print(hashlib.sha256(getpass.getpass().encode()).hexdigest())"` (for `holdout_seed` this is
  the hash `config.check_holdout` compares with `holdout_seed_sha256`; the permutation seed has no config-side check). A 256-bit seed is
  not recoverable from its public hash. The hooks and CLAUDE.md: `shasum -a 256` of `$HOME/.claude/hooks/<name>` and
  `$HOME/.claude/CLAUDE.md`. A settings entry: sha256 of `json.dumps({"event": E, "matcher": M, "command": C}, sort_keys=True)` for the
  hook object whose command names the hook (E the event key, M its group's `matcher` or null). The injector code set, in
  `$HOME/ace-playbook` with `git status --porcelain` empty for these paths: `git ls-files -s -- ace_playbook/inject ace_playbook/store
  ace_playbook/config.py ace_playbook/health.py ace_playbook/keys.py ace_playbook/canon.py hooks/sessionstart.sh | shasum -a 256`.
  The SessionStart hook runs `$HOME/ace-playbook` only when `ACE_PLAYBOOK_HOME` and `ACE_PYTHON` are unset: the settings `env` block sets
  neither, and the operator records `env | grep -c -E '^(ACE_PLAYBOOK_HOME|ACE_PYTHON)='` (0) from his terminal in a dated line. The
  lessons, the block, the config values and the pinned set, from that checkout, with `PYTHONPATH="$HOME/ace-playbook" python3 -P -` fed
  the block below without its two-space list indentation:

  ```python
  import hashlib, json
  from ace_playbook import config
  from ace_playbook.store.log import DeltaLog
  from ace_playbook.inject.injector import HEADER, FOOTER
  cfg = config.load()
  s = DeltaLog(cfg.deltas).state()
  ids = sorted(["L-cbfb7a57", "L-f8a355f9", "L-93544dae", "L-b3d1fba9"])
  doc = [[i, s[i]["text"], s[i]["applies_when"]] for i in ids]
  print(hashlib.sha256(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest())
  print(hashlib.sha256((HEADER + "\n" + FOOTER).encode()).hexdigest())
  print(f"inject_mode={cfg.inject_mode} holdout_rate={cfg.holdout_rate} max_global={cfg.max_global} inject_max_chars={cfg.inject_max_chars}")
  print(",".join(f"{i}:{l['status']}:{l['scope_type']}" for i, l in sorted(s.items()) if l.get("pinned")))
  ```

  The config line prints only those four keys. At the switch-on every literal above must reproduce by its command; if one does not, the
  fill stops, and the entry is amended by a text commit and a docs-lens review before `holdout_start` (or the start moves). Fill gate:
  the commit that fills (j) changes exactly the three placeholder lines, and in each only the `<...>` token, replaced by 64 lowercase hex
  characters (`git diff --word-diff=porcelain` of that commit shows three removed tokens, three added tokens, nothing else); a read-only
  docs-lens review confirms the fill. The fill commit is the last commit on this branch before the merge, and the sha256 of the merged
  PREREGISTRATION.md equals the stamped one.

  Before the read, the operator checks that sha256 of each config seed equals its line above, that every live injection row's `seed_fp`
  equals the first 8 hex digits of `holdout_seed_sha256`, that every other value in the block reproduces by its command, and that the six
  values of (g) reproduce in `$HOME/ace-playbook`. The result is recorded in a dated line before the read is run. Any mismatch, whenever
  it occurred (also between step 4 and `holdout_start`), maps by (k).
  Recorded, not frozen: the commit of the ace-playbook checkout and the last commit touching the sweep and cleaner code
  (`git log -1 --format=%H -- ace_playbook/compile/offline.py ace_playbook/clean/`), at the switch-on and at the read; any change is
  reported with the read.
- (k) Changes and looks during the horizon. No per-arm view of holdout data is taken before the read: `--interim-descriptive` is run only
  for the day-1 check below, piped so that only its `counts` object is printed. Evidence cards (`compile cards`, `compile propose`)
  cross a lesson's exposure with its incidents (`lesson_shown_in_incident_session`), which is a per-arm view: none is built or read for
  the three lessons before the read (the nightly sweep builds none). A change after `holdout_start`, or a (j) mismatch found later,
  makes a read exploratory as follows, mapped in advance: a scoring hook or its settings entry → the lesson it scores; a detector, its
  mode or its approval → its lesson; the text or `applies_when` of one of the three lessons → that lesson; L-b3d1fba9, the pinned set,
  `HEADER`/`FOOTER`, a config value in (j), a seed, the window, the SessionStart settings entry, the injector code set or any value of (g)
  → all three; CLAUDE.md or an auto-memory file → a lesson only if a line classified in (c) as a statement of that lesson's rule was
  added or changed. Any other edit to those files is logged in a dated line and is not a deviation. The one exception is a recorded bug
  fix, written before any per-arm data is seen. If the holdout is switched off before `holdout_end`, the sessions up to the switch-off
  are analysed and the result is labelled exploratory.
- (l) Null handling (replacing the draft's paragraph, whose figures assumed another horizon and rate): if no lesson's Holm-adjusted p is
  below 0.05, the README reports "no detectable effect" with D, the per-arm rates and the power table in (d). No other outcome, window
  or subset is searched for an effect. A significant D in the harmful direction (more violations when shown) is reported as harm.
- (m) At the horizon, the three rules return to CLAUDE.md and `holdout_enabled` returns to false.

Switch-on order: steps 1-5 before `holdout_start` (a step 1-5 after it is a deviation); step 6 follows later, as it states.

1. The operator removes rules 6, 8 and 9 from the global CLAUDE.md (D-232) and writes the rule-line record of (c).
2. `store exempt L-b3d1fba9` (the delta log has no exempt record yet).
3. The operator generates `holdout_seed` and `holdout_permutation_seed` in his terminal (`secrets.token_hex(32)` each). He sets them,
   together with `holdout_seed_sha256` (required, non-empty), `holdout_rate` 0.5, `holdout_start` and `holdout_end`, in the private config.
4. A commit on this entry, before its merge and as its last commit, replaces the three placeholders in (j) by the commands of (j) and
   passes the fill gate. The sha256 of PREREGISTRATION.md at that commit is OpenTimestamps-stamped.
5. `holdout_enabled` is set to true.
6. Day-1 check. D0 is the first `YYYY-MM-DDT00:00:00Z` after step 5 and D1 the same form one day later; the operator runs at least one
   interactive session between them. After the first nightly sweep that completes after D1, with P the data dir's `injections.jsonl`:

   ```sh
   PYTHONPATH="$HOME/ace-playbook" python3 -P -m ace_playbook.compile.holdout_report --interim-descriptive --json \
     --start "$D0" --end "$D1" | python3 -c 'import json,sys;print(json.load(sys.stdin)["counts"])'
   python3 -c 'import json,sys,collections;a,b=sys.argv[2:4];print(collections.Counter(e for _,e in {(r.get("session_id"),r.get("entrypoint"))
     for r in map(json.loads,open(sys.argv[1])) if a<=r["ts"]<b}))' "$P" "$D0" "$D1"
   ```

   The first command prints only the `counts` object, so no per-lesson data is printed. It must show `analysed_sessions` at least 1 and
   each in-window failure count (`refused`, `holdout_off`, `mixed_rate`, `rate_not_half`, `seed_fp_mismatch`, `assignment_conflict`) 0.
   The second counts the sessions of D0 to D1 per entrypoint, from the injection log only: none lacks the field (no `None` key), and
   `unmatched_injection` is at most the number of non-`cli` sessions (automated runs whose transcript is not scanned). If no
   interactive session ran, the check repeats for the next day. A failed check is fixed as a recorded bug fix of (k).
