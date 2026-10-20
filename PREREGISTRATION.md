# Pre-registration (DRAFT, NOT SEALED)

Status: **draft placeholder.** Nothing in this file is binding yet. It becomes binding when it is
committed with the fields marked TBD filled in and tagged `prereg-v1`, before the first
randomized session that will count as confirmatory. After the tag, changes are dated amendments
appended at the bottom; the sealed text is never edited. Any run made before the tag is
labelled exploratory and is excluded from the confirmatory analysis.

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

Confirmatory read at 16 weeks, with group-sequential looks at 8, 16 and 24 weeks and early
stopping for harm only. The boundary values (about 0.0005, 0.014 and 0.045, O'Brien-Fleming
style) are textbook figures that have not been verified for this design; TBD: confirm before
sealing.

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
descriptively, as the "Compiled path" section above says; nothing here replaces it. The descriptive
effect is fires per eligible session with versus without the check, per detector: the "with" arm
runs the proposal in its approved mode, the "without" arm runs it in observe. It is reported as a
rate ratio, with randomization inference over 10,000 seeded permutations of the arm labels and a
Wilson 95 % interval per arm (`metrics.wilson`). A hook has no memory across sessions, so the first
attempt in a session cannot differ between arms. The leading contrast is therefore REPEAT fires per
eligible session and evasion; sessions-with-fire is reported descriptively.

### Eligibility

A session is eligible for a detector if it has at least one event that detector consumes: the
detector is listed in the `eligible` field of the newest `_session` row of that session in
`detector_events.jsonl` (the last row in file order wins). Interactive and automated sessions alike;
the scan does not distinguish them. It reads `*.jsonl` transcripts and `offline.discover` skips subagent
transcripts, non-regular files (symlinks included), names that do not match the session-id pattern,
transcripts older than the `since_days` look-back, already-scanned transcripts that have not changed,
oversized transcripts (over 64 MiB) and sessions idle for less than the configured idle time. Excluded: the synthetic corpus, fires with
`cause: truncated`, and sessions still awaiting the offline scan at report time.

### Arm assignment

Each (session, proposal) pair is assigned by `coin(shadow_seed, session_id, proposal_id) <
arm_rate`, with `arm_rate` 0.5. The seed is private and only its sha256 is sealed, the same
commitment as `holdout_seed` above. The true-positive and false-positive rules of design section 7.3
and the promotion and demotion ladder are frozen by reference to the section numbers of the design
document and to the report code hash (`report_sha256`) in the block below.

### Spillover

Lessons are not arm-specific, so the "without" arm is contaminated by the prompt-level lesson that
the same incident produced. The report flags every lesson whose originating session holds an `ACE P-`
message and reports the arm comparison separately for sessions where such a lesson was shown. The
flag is reported, not corrected.

### Timing and stopping

A fixed horizon of N eligible sessions per detector, or 4 weeks, whichever comes first. No interim
looks; stopping early is allowed for harm only.

N per detector: (filled by hand before the seal)

The operator fills N from the week-1 rates with the power note of design section 8, before running
`compile seal`; the real seal refuses while the placeholder above is still present.

### Null handling

A rate ratio whose permutation p-value is at least 0.05 is reported as "no detectable effect at
this N", never as "no effect".

### Frozen instruments

Filled by `compile seal`: one line per detector (`name strength sha256`: the strength from the
catalogue registry and the hash from `catalogue.source_table()`), `report_sha256`, one line per
approved proposal (`P-id mode proposal_sha256`, from the newest non-revoked row of
`approvals.jsonl`), `shadow_seed_sha256`, `arm_rate`, `measurement_start` and `sealed_at`.
`report_sha256` covers exactly two files, `ace_playbook/compile/metrics.py` and
`ace_playbook/compile/report.py` (their bytes in that order, joined by a NUL byte); the detector
code is covered by the per-detector hashes, not by `report_sha256`.

(filled by compile seal)

### Amendments

Changes after the seal are made only as dated appendices under the top-level "Amendments" heading
below, never as edits of sealed text.

## Amendments

(none)
