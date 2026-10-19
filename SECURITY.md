# Security

## Reporting

Please use the repository's private vulnerability reporting (Security tab, "Report a
vulnerability") rather than a public issue. This is a single-maintainer research project:
expect a best-effort response, not an SLA.

## Threat model summary

The reflector reads transcripts, and transcripts contain hostile text: fetched web pages,
third-party READMEs, cloned repositories and command output. Whatever the reflector turns into a
lesson is injected at the start of every future session. The realistic attack is indirect prompt
injection into memory, not a query-only attacker. Published results show writing to memory is
easy while the behavioral payoff varies widely (from near zero to very high depending on setup),
so this design relies on structural controls rather than on a classifier.

Other assets at risk: credentials and personal data that pass through transcripts, the integrity
of the lesson store, and the settings and hooks of the agent that consumes it.

## Controls in stage 1 (in code, with tests)

1. **Provenance-gated minting.** A lesson needs a verbatim evidence quote found in a user message
   or in a project file the user owns that the agent did not write. Quotes found only in tool
   output, assistant text or nowhere are quarantined. The schema also refuses a live status
   without user-origin evidence.
2. **Inert, non-authoritative injection.** The injected block is delimited, labelled advisory data,
   capped at 7,000 characters, and built only from lessons that pass an inertness check (no URLs,
   code, shell syntax, markup, override phrases or tool-directed imperatives). A lesson that fails
   is left out, never rewritten.
3. **Quarantine, not redaction.** The scrubber runs on outbound text (masking, for data
   minimization), on every returned lesson (hit means the whole lesson goes to an untracked
   quarantine file), in the store writer (which refuses text with hits) and as a repo check.
4. **Append-only, hash-chained log** with all-or-nothing atomic writes, a per-run limit on lessons
   touched and on new lessons, and a verifier for tampering.
5. **Fail-open hooks with no network.** Stage-1 hooks never block a session; stage-2 shadow hooks
   block only a proposal the operator promoted to block mode after the observe/warn ladder (see
   docs/design/compiler.md; amended 2026-10-19). No hook calls the API; only the reflector does,
   with no tools, from a path-configured key that is never logged.
6. **Reflector prompt** tells the model to ignore instructions inside tool results. This is the
   weakest control and is not relied on.

## Planned, not implemented

A compiler that emits specs and diffs only, behind a human gate and a second reviewer that sees
provenance; a `ConfigChange` audit hook; canary lessons and fake-secret fixtures fed through the
pipeline on a schedule; signature or hash verification of the log at session start; an LLM
classifier used only as a signal to human review. The compiler must never output executable text
that is applied automatically.

## Do not commit

Real lesson text, evidence quotes, transcript excerpts, session ids, client or ticket names,
project ids, internal hostnames, home-directory paths, API keys, `.env` files, or any ledger,
quarantine file or SQLite view. `.gitignore` excludes the usual names; CI runs
`python -m ace_playbook.scrub check` on every push. Fixtures are synthetic and every planted
credential carries the marker `FAKE`. If a real credential ever lands in a file or in history,
rotate it first; deleting the file does not remove it from history.

## Operational hygiene

Do not run the reflector as an agent session with an untrusted clone as its working directory;
the reflector here is a direct API call with no tools. Do not enable live secret verification
tools against real data. Keep the API key in a dedicated, spend-capped workspace.
