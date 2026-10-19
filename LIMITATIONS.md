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
state-versus-lesson classifier. In the stage-2 compiler: the offline nightly pass over transcripts,
automatic demotion, the synthetic
replay corpus, and the full measurement report. `PREREGISTRATION.md` describes them as plans only
and is not sealed.

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
- `additionalContext` was confirmed live for PreToolUse only; Stop delivery follows the hooks reference and is unverified.
- The `Stop` hook entry is wiring only: no approvable detector consumes `Stop` today.
- A lesson migrated from an older corpus carries self-quoted `legacy:` evidence and gets an evidence
  card only through a human incident label (`compile label --incident`). Re-minting such a lesson
  with `store promote` does not help here: the promote evidence carries session `promote-cli`, which
  has no transcript, so it never yields a standard card.
- `compile label` is TTY-only, which stops hooks, pipes and plain subprocesses, not a determined
  local process; promotion makes a forged label visible to the operator rather than impossible.

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
