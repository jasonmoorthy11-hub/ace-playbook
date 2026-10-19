# Limitations

Read this before drawing any conclusion from the repository.

## Status

Stage 2 shadow mode, observe only. There are no results. The stage-1 plumbing (store, scrubber,
injector, reflector) and the stage-2 compiler (evidence cards, spec-only proposals, the shadow hook)
exist as code; as of 2026-10-19 no proposal has been approved, so the shadow hook has recorded
nothing and nothing has been measured. No measured claim about benefit is made.

## Statistical power

At roughly 2.6 sessions a day, a per-lesson hide/show design has limited power. With an assumed
3% recurrence per (session, lesson) pair and about ten eligible lessons per session, the
smallest rate ratio detectable at 80% power is roughly 0.33 at 8 weeks, 0.5 at 16 weeks and 0.57
at 24 weeks. A 30% reduction (rate ratio 0.7) has about 17 to 27% power at 8 weeks. The
pre-registered meaningful effect (0.67) is not reachable at any horizon under roughly 30 weeks,
and an informative null is not reachable at 16 weeks. The assumptions (baseline rate, number of
eligible lessons) are guesses and the real corpus will probably have fewer eligible lessons than
assumed once duplicates of always-loaded instructions are excluded. These figures come from a
simulation in the author's research notes, not from data; the notes are not part of this
repository.

## Evidence is thin

- No controlled study of automatically reflected cross-session lessons for a CLI coding agent
  was found. The ACE paper's gains depend on execution feedback and it reports a coding-agent
  setting only indirectly; one independent audit found ACE-style playbooks harmed 4 of 11
  settings, and one independent benchmark put an ACE playbook below plain in-context history.
- The only direct measurement of compiled rules against prose rules (TRACE) used simulated
  users, 29 checks from one researcher and no false-positive measure.
- Studies of LLM-written context files report null or slightly negative effects on task success
  with higher cost. This project's lessons are user- and environment-specific facts rather than
  repo knowledge, which weakens that prior without removing it.
- Agent-tagged helpful/harmful counters are known to be unreliable (a harmful rule has been
  tagged helpful 123 times). Here they are recorded as observability and used as a weak ranking
  feature only. They never retire or compile anything.

## Deviations from the ACE paper

- Deltas are validated and merged by code; there is no embedding-based deduplication and no
  lazy-refinement pass yet. A single reflector call replaces the paper's separate Generator,
  Reflector and Curator roles, and there is no LLM curator in stage 1.
- Execution feedback is replaced by observable proxies (user-correction count, hook blocks,
  repeated failing commands). These proxies are noisy and unvalidated.
- Decay is computed at read time and affects rank only. Nothing is ever deleted or rewritten.
- Stage 1 injects pinned lessons only, so it tests the plumbing and the holdout mechanism, not
  retrieval quality.

## Not built yet

The curator and refiner, the replay and task-suite evaluation harness, the judge, and the
state-versus-lesson classifier. In the stage-2 compiler: the arms (randomization of the check per session), the seal of the
pre-registration, the evaluation of an effect (randomization inference, the spillover flag), the review packet and the review record. `PREREGISTRATION.md` describes them as plans only and is not sealed.

## Compiler (stage 2)

- The command scanner is a best-effort linear tokenizer, not a shell parser. Known shapes it does
  not see: `find ... -delete` and `-exec rm -rf`, `xargs` fed from a pipe, `bash -c`, `eval` and
  `bash <<EOF` bodies, shell functions and aliases, and anything past the 64 KB cap (reported as
  `unparseable`). The control is the promotion ladder: a proposal starts in observe mode and every
  counted true positive is read by a human before any mode change (`docs/design/compiler.md` 7.3).
- Warn delivery, resolved: warn uses `additionalContext` (step 5b). The Claude Code hooks reference (read
  2026-10-19) says a hook's stderr on exit 0 never reaches the model, so the hook prints one
  `hookSpecificOutput.additionalContext` JSON object on stdout (and echoes the message on stderr for the debug log).
- `approvals.jsonl` is hash-chained but unkeyed. It stops hand edits of an approved copy (a copy without a matching row
  runs in observe) but not a local process that appends a correctly chained row for a `warn` or `block` copy it also
  writes: that would make the hook warn or block the agent itself, limited to proposals that pass validation.
- A proposal's `provenance.source_sha256` is a per-detector hash by exclusion: the text of `detectors.py` and `catalogue.py`, minus
  the bodies of the other rows' `fire` functions and the `CATALOGUE` registry literal, plus the row's name, fire name and catalogue
  fields and the whole text of `events.py`, `shell.py` and `textrules.py`. Editing one detector's own fire body, or another row's
  fields in the registry, retires only that detector's proposals (re-propose, re-approve). Any other edit retires every approved
  proposal: a shared helper, an import, a constant, a decorator or signature of any fire, a rebinding, `evaluate`, the `Detector`
  class, `advance` or `blank_state` (hashed for every row: `evaluate` runs `advance` before the decision is emitted), any edit to
  the three leaf modules, and adding a detector. Dynamic lookups are moot (nothing is followed, everything is text). What the hash
  does not see is any difference between the text on disk, read once when it is first used in a process, and the code actually
  loaded (a tampered `.pyc`, an in-memory monkeypatch, a file edited while a long-lived process runs); the old whole-file hash
  had the same blind spot. A fire in the registry must be the top-level function of `detectors.py` that carries its name
  (a lambda, a wrapper, a `functools.partial` or a callable instance fails the same way): hashing then raises `ValueError` for that
  row only, the hook skips its proposals, the nightly pass treats them as stale (`source_sha256` demotion), and `source_table()`
  and building evidence cards for it raise. The event object is shared by all fires in one `evaluate` call (the state is deep-copied
  per fire); a test pins that no detector mutates either. The merge that introduced the hash changed every existing proposal's hash
  once, so each proposal written before it is stale and must be re-proposed and re-approved.
- `additionalContext` was confirmed live for PreToolUse only; Stop delivery follows the hooks reference and is unverified.
- The `Stop` hook entry is wiring only: no approvable detector consumes `Stop` today.
- A lesson migrated from an older corpus carries self-quoted `legacy:` evidence and gets an evidence
  card only through a human incident label (`compile label --incident`). Re-minting such a lesson
  with `store promote` does not help here: the promote evidence carries session `promote-cli`, which
  has no transcript, so it never yields a standard card.
- `compile label` is TTY-only, which stops hooks, pipes and plain subprocesses, not a determined
  local process; promotion makes a forged label visible to the operator rather than impossible.
- The nightly `compile offline` pass pre-fills labels from transcript signals; they are pre-fill only.
  An auto `fp` counts as a false positive (and can demote a proposal) until a human relabels it, a
  `tp_candidate` is never counted as a true positive, and a human row always wins. The signals are
  heuristics (a regex for user corrections, a hash match against the latest prompt). The p95 hook-cost
  rule (disable above 200 ms over seven days) is not implemented and is reported as `not implemented` on every run (rows carry `dur_ms`, but
  it covers firing calls only and excludes the interpreter start); the report's hook cost has the same limits, and a subagent's tool calls
  may count as live fires against a parent session whose tool calls the offline pass excludes; the 1% hook-error rule needs a sample of 100 (rows count fires, not hook calls) and is
  reported `not computable` below it.
- An operator approval lifts demotions: a proposal's cap is the lowest `to_mode` among the `demotions.jsonl` rows stamped at or after its
  newest `approvals.jsonl` row, so `approve --mode observe` (TTY, which lists each row it lifts) on a demoted proposal,
  `disabled` included, lifts the strictly older rows (a same-second row stays: re-approve a second later) and restarts the
  ladder from observe; until then `approve --mode warn|block` is refused with `demoted:<cap>`. The agent
  cannot write a valid approval row without the TTY, except by forging the unkeyed chain (the stated residual). A restored copy
  after `unapprove` is skipped (`revoked`), not run in observe.
  The `from_mode` check in `demotions.append` is a guard the nightly pass cannot trip, not a control.
- Every input of the offline pass can be written by a local process: transcripts, `shadow.jsonl` (including its `mode`),
  `shadow.log`, `labels.jsonl`, `demotions.jsonl` (hash-chained but unkeyed), `offline_state.json`, the approved copy's
  `approved_at` and `deltas.jsonl`. Demotions can so be caused by forged inputs and suppressed by deleting or truncating the
  file. The hook's safety is that a copy above `observe` needs a matching approvals row and only an operator approval lifts a demotion; the
  operator's controls are the TTY listing at promotion and the `Demotions` section of `compile report`.
- `tp_candidate` rows must never feed the ladder (the agent can print a guard-looking line into its own tool output in observe
  and warn mode). The shell-mode rule skips meta records (`isMeta`, compact summary, transcript-only) and
  needs the whole-record `<bash-input>` shape.

## Security and scrubbing

Secret scanners have measured recall well below 100% (31 to 88% in one comparison, 36.6% for the
best static scanner on runtime credentials in another). The scrubber here is regex, entropy and
decoded-content checks plus lints, and it has not been measured against real credentials; the
synthetic corpus is generated to match its own patterns, so detection on it is circular.
Paraphrase leakage (a lesson that restates a token's prefix or last characters) is a hypothesis
the long-token lint partly covers and nothing measures.

## Platform assumptions

Behavior of Claude Code hooks (SessionStart context cap, SessionEnd budget, Stop payload,
`onFailure`) is taken from its documentation as read in October 2026 and may change. Two
behaviors were not settled by documentation; `docs/platform-notes.md` records what was observed
on 2026-10-19 (Claude Code 2.1.302, macOS): files written to the memory directory during a
session are not surfaced to the running session, and the SessionEnd hook only enqueues, so the
design never relies on a detached child surviving an interactive exit. Stage 1 depends on
neither; the nightly sweep runs under launchd instead.

## Provisional promotion

A `PATCH` that sets a live status (`active` or `provisional`) is refused unless the lesson holds
user-origin evidence that is not migrated: evidence whose session id starts with `legacy:` is
self-quoted by the migration and does not count. So a bare `PATCH {status: active}` on a migrated
lesson fails, and the `promote` CLI, which attaches the operator's typed quote, is the way through.

An `ADD` with a live status is still validated by origin only (`user_message` or `user_file`),
which is what the migration's override activations relied on. Existing `ADD active` records with
`legacy:` evidence are therefore still valid: `verify()` and a view rebuild of an existing log are
unaffected (covered by a test). The remaining gap is that nothing stops a new hand-written
`ADD` with a live status and self-quoted `user_file` evidence; the log checks origin, not who
typed the quote.

The `promote` TTY check proves a terminal is attached to stdin, not that a person typed: a pty
wrapper can pass it. It stops hooks, pipes and plain subprocesses, not a determined local process.

Migrated dormant lessons (legacy-only evidence) cannot be revived by a bare `PATCH` either, and
`promote` accepts only `provisional` lessons; reviving a dormant one is an operator decision for
later. `verify()` checks schema and the hash chain but does not fold the log, so `rebuild` is the
compatibility check after any change to a view-level rule.

## Single user

One person's workflow on one machine. Results, if any, will not generalize to teams or to other
agents without separate evidence.
